"""Record sources and check for updates."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from vivatlas.archive import RepoContents
from vivatlas.models import Artifact, Repository, UpstreamLink
from vivatlas.providers.base import GitProvider
from vivatlas.upstream import (
    UpstreamChecker,
    UpstreamRef,
    decide_status,
    detect_from_mirror,
    detect_from_readme,
)

log = logging.getLogger(__name__)


@dataclass
class DiscoverResult:
    found: int = 0
    not_found: int = 0
    updated: int = 0


@dataclass
class CheckResult:
    checked: int = 0
    in_sync: int = 0
    update_available: int = 0
    locally_modified: int = 0
    diverged: int = 0
    failed: int = 0


def discover_for_artifact(
    session: Session,
    artifact: Artifact,
    contents: RepoContents,
    original_url: str = "",
) -> UpstreamRef | None:
    """Find the source. Ordered by reliability: a mirror beats a line in the README."""
    ref = detect_from_mirror(original_url) or detect_from_readme(
        contents, artifact.repository.name, artifact.anchor_path
    )
    if ref is None:
        return None

    link = session.scalar(select(UpstreamLink).where(UpstreamLink.artifact_id == artifact.id))
    if link is None:
        link = UpstreamLink(artifact_id=artifact.id)
        session.add(link)

    link.kind = ref.kind
    link.upstream_repo = ref.repo
    link.upstream_path = ref.path
    link.upstream_url = ref.url
    link.discovered_by = ref.discovered_by
    return ref


async def check_link(
    session: Session,
    provider: GitProvider,
    checker: UpstreamChecker,
    link: UpstreamLink,
    repo: Repository,
) -> str:
    """Compare the copy against the source. Returns the state."""
    from vivatlas.providers.base import RepoRef

    ref = RepoRef(
        external_id=repo.external_id,
        owner=repo.owner,
        name=repo.name,
        default_branch=repo.default_branch,
        is_private=False,
        is_archived=repo.is_archived,
        is_empty=repo.is_empty,
        html_url=repo.html_url,
        clone_url=repo.clone_url,
        size_kb=repo.size_kb,
    )

    if link.kind == "gitea-mirror":
        # For a mirror we compare wholesale by the last commit: there are no
        # individual files here, the copy must mirror the source as-is.
        local = await provider.get_head_sha(ref)
        upstream = await checker.head_sha(link.upstream_repo)
    elif link.kind == "github-file":
        if not link.upstream_path:
            link.status = "unknown"
            link.check_error = "we don't know where this file lives in the source"
            return "unknown"
        head = await provider.get_head_sha(ref)
        ours = await provider.blob_shas(ref, head)
        theirs = await checker.blob_shas(link.upstream_repo)
        anchor = link.artifact.anchor_path or "DESIGN.md"
        local = ours.get(anchor, "")
        upstream = theirs.get(link.upstream_path, "")
        if not upstream:
            link.status = "unknown"
            link.check_error = f"the source has no file {link.upstream_path}"
            link.last_checked_at = datetime.now(UTC)
            return "unknown"
    else:
        link.status = "unknown"
        link.check_error = f"unknown source kind: {link.kind}"
        return "unknown"

    # First encounter: if they match right now, that's an honest baseline.
    if not link.baseline_at:
        if local == upstream:
            link.baseline_local_sha = local
            link.baseline_upstream_sha = upstream
            link.baseline_at = datetime.now(UTC)
        else:
            # Already diverged with no baseline — we can't tell the cause apart.
            # We won't lie about it.
            link.last_local_sha = local
            link.last_upstream_sha = upstream
            link.last_checked_at = datetime.now(UTC)
            link.status = "unknown"
            link.check_error = (
                "the copy and source diverged before we started tracking — "
                "unclear whether it's a new version or your edit"
            )
            return "unknown"

    status = decide_status(local, upstream, link.baseline_local_sha, link.baseline_upstream_sha)
    link.last_local_sha = local
    link.last_upstream_sha = upstream
    link.last_checked_at = datetime.now(UTC)
    link.status = status
    link.check_error = None
    return status


async def check_all(
    session: Session, provider: GitProvider, checker: UpstreamChecker
) -> CheckResult:
    links = session.scalars(select(UpstreamLink)).all()
    result = CheckResult()

    for i, link in enumerate(links, 1):
        repo = link.artifact.repository
        try:
            status = await check_link(session, provider, checker, link, repo)
            session.commit()
            result.checked += 1
            match status:
                case "in-sync":
                    result.in_sync += 1
                case "update-available":
                    result.update_available += 1
                case "locally-modified":
                    result.locally_modified += 1
                case "diverged":
                    result.diverged += 1
            log.info("[%d/%d] %s — %s", i, len(links), repo.full_name, status)
        except Exception as exc:
            session.rollback()
            link.status = "unknown"
            link.check_error = str(exc)[:300]
            session.commit()
            result.failed += 1
            log.error("[%d/%d] %s — ERROR: %s", i, len(links), repo.full_name, exc)

    return result
