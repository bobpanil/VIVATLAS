"""Scanning: fetch the list of repositories and stash it in the database.

Never writes to Git. Read-only.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from vivatlas import changes
from vivatlas.models import Repository, ScanRun, Source
from vivatlas.providers.base import GitProvider, RepoRef

log = logging.getLogger(__name__)


def is_scannable(repo: RepoRef) -> bool:
    """Private repositories are never scanned. Never, no exceptions.

    This is a rule, not a setting: there is no toggle for it anywhere — not in .env,
    not in the database. Empty repositories are skipped separately: there is
    nothing to read in them.
    """
    if repo.is_private:
        return False
    if repo.is_empty:
        return False
    return True


@dataclass
class ScanResult:
    seen: int = 0
    added: int = 0
    updated: int = 0
    gone: int = 0
    skipped_private: int = 0

    @property
    def stored(self) -> int:
        return self.added + self.updated


def get_or_create_source(session: Session, kind: str, base_url: str, name: str) -> Source:
    source = session.scalar(select(Source).where(Source.kind == kind, Source.base_url == base_url))
    if source is None:
        source = Source(kind=kind, base_url=base_url, display_name=name)
        session.add(source)
        session.flush()
    return source


async def scan_source(
    session: Session, provider: GitProvider, source: Source, include_private: bool = False
) -> ScanResult:
    """include_private allows private repositories — but ONLY for a user's personal
    source (with their token, into their private zone). For the shared zone the
    "don't touch private" rule stands: True is never passed here."""
    run = ScanRun(source_id=source.id)
    session.add(run)
    session.flush()

    result = ScanResult()
    try:
        remote_repos = await provider.list_repositories()
        result.seen = len(remote_repos)

        allowed: list[RepoRef] = []
        for repo in remote_repos:
            if repo.is_empty:
                continue
            if repo.is_private and not include_private:
                result.skipped_private += 1
                continue
            allowed.append(repo)

        existing = {
            row.external_id: row
            for row in session.scalars(select(Repository).where(Repository.source_id == source.id))
        }

        now = datetime.now(UTC)
        for repo in allowed:
            row = existing.get(repo.external_id)
            if row is None:
                new_row = _new_row(source.id, repo, now)
                session.add(new_row)
                session.flush()
                changes.record(
                    session,
                    "added",
                    repository_id=new_row.id,
                    title=repo.full_name,
                    details=repo.description,
                    scan_run_id=run.id,
                )
                result.added += 1
            else:
                old_name = row.full_name
                _update_row(row, repo, now)
                if old_name != repo.full_name:
                    changes.record(
                        session,
                        "renamed",
                        repository_id=row.id,
                        title=repo.full_name,
                        details=f"was: {old_name}",
                        scan_run_id=run.id,
                    )
                result.updated += 1

        # Gone from the listing — flag it, but don't delete: we keep the history.
        allowed_ids = {repo.external_id for repo in allowed}
        for external_id, row in existing.items():
            if external_id not in allowed_ids and row.gone_at is None:
                row.gone_at = now
                changes.record(
                    session,
                    "removed",
                    repository_id=row.id,
                    title=row.full_name,
                    details="gone from the host's listing: deleted or made private",
                    scan_run_id=run.id,
                )
                result.gone += 1

        run.status = "success"
    except Exception as exc:
        run.status = "failed"
        run.error = str(exc)
        run.finished_at = datetime.now(UTC)
        session.flush()
        raise
    finally:
        run.repos_seen = result.seen
        run.repos_added = result.added
        run.repos_updated = result.updated
        run.repos_gone = result.gone
        run.repos_skipped_private = result.skipped_private
        if run.finished_at is None:
            run.finished_at = datetime.now(UTC)
        session.flush()

    return result


def _new_row(source_id: int, repo: RepoRef, now: datetime) -> Repository:
    return Repository(
        source_id=source_id,
        external_id=repo.external_id,
        owner=repo.owner,
        name=repo.name,
        default_branch=repo.default_branch,
        description=repo.description,
        html_url=repo.html_url,
        clone_url=repo.clone_url,
        size_kb=repo.size_kb,
        is_archived=repo.is_archived,
        is_empty=repo.is_empty,
        original_url=repo.original_url,
        remote_created_at=repo.created_at,
        remote_updated_at=repo.updated_at,
        first_seen_at=now,
        last_seen_at=now,
    )


def _update_row(row: Repository, repo: RepoRef, now: datetime) -> None:
    row.owner = repo.owner
    row.name = repo.name
    row.default_branch = repo.default_branch
    row.description = repo.description
    row.html_url = repo.html_url
    row.clone_url = repo.clone_url
    row.size_kb = repo.size_kb
    row.is_archived = repo.is_archived
    row.is_empty = repo.is_empty
    row.original_url = repo.original_url
    row.remote_created_at = repo.created_at
    row.remote_updated_at = repo.updated_at
    row.last_seen_at = now
    # A repository a user removed is not resurrected: it exists on the host, but
    # a scan would bring it back into the catalogue, while the user removed the
    # card deliberately and for good.
    if not row.user_removed:
        row.gone_at = None  # came back
