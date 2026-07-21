"""Migrate existing repositories to the "path as on GitHub" rule.

Two parts. The plan computation is pure, touches nothing, and is covered by tests.
Execution writes to Gitea, one at a time, and stops at the very first error,
without heroically trying to force the rest through: a half-done migration is a state
you can read where it stopped from, not a random pile.

Rule for existing cards:
  - source not recorded            -> leave it alone, we don't know the GitHub address;
  - one card from a source         -> owner/repository, as on GitHub;
  - several cards from a source    -> owner/repository-folder, so that
    74 sets from a single awesome-design-md don't land on one address.
"""

import logging
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from vivatlas.importer import _safe_name
from vivatlas.models import Artifact, Repository, UpstreamLink
from vivatlas.providers.gitea import GiteaProvider

log = logging.getLogger(__name__)


@dataclass
class RemapItem:
    repo_id: int
    old_owner: str
    old_name: str
    new_owner: str
    new_name: str
    upstream_repo: str

    @property
    def old_full(self) -> str:
        return f"{self.old_owner}/{self.old_name}"

    @property
    def new_full(self) -> str:
        return f"{self.new_owner}/{self.new_name}"


@dataclass
class RemapPlan:
    changes: list[RemapItem]
    unchanged: list[str]  # source not recorded — nothing to mirror
    already: list[str]  # already follows the rule, nothing to touch

    @property
    def new_orgs(self) -> list[str]:
        return sorted({i.new_owner for i in self.changes})


def _leaf(path: str) -> str:
    """Tool folder name. For a file, take the folder above it, not the file itself."""
    parts = [p for p in (path or "").split("/") if p]
    if not parts:
        return ""
    if "." in parts[-1] and len(parts) >= 2:
        return parts[-2]
    return parts[-1]


def target_for(upstream_repo: str, upstream_path: str, shared_source: bool) -> tuple[str, str]:
    """Where the card should move to. Owner and name per the rule."""
    gh_owner, gh_repo = upstream_repo.split("/", 1)
    owner = _safe_name(gh_owner)
    if shared_source:
        name = _safe_name(f"{gh_repo}-{_leaf(upstream_path)}")
    else:
        name = _safe_name(gh_repo)
    return owner, name


def compute_plan(session: Session) -> RemapPlan:
    """What moves where. Touches nothing — only reads the database."""
    repos = session.scalars(select(Repository).where(Repository.gone_at.is_(None))).all()

    links: dict[int, UpstreamLink | None] = {}
    for repo in repos:
        art = session.scalar(select(Artifact).where(Artifact.repository_id == repo.id))
        links[repo.id] = (
            session.scalar(select(UpstreamLink).where(UpstreamLink.artifact_id == art.id))
            if art
            else None
        )

    # How many cards point to one and the same source.
    shared = Counter(link.upstream_repo for link in links.values() if link)

    changes: list[RemapItem] = []
    unchanged: list[str] = []
    already: list[str] = []

    for repo in repos:
        link = links[repo.id]
        if not link or "/" not in (link.upstream_repo or ""):
            unchanged.append(f"{repo.owner}/{repo.name}")
            continue
        owner, name = target_for(
            link.upstream_repo, link.upstream_path or "", shared[link.upstream_repo] > 1
        )
        if (owner, name) == (repo.owner, repo.name):
            already.append(f"{repo.owner}/{repo.name}")
            continue
        changes.append(
            RemapItem(
                repo_id=repo.id,
                old_owner=repo.owner,
                old_name=repo.name,
                new_owner=owner,
                new_name=name,
                upstream_repo=link.upstream_repo,
            )
        )

    _assert_no_collisions(changes, unchanged, already)
    return RemapPlan(changes=changes, unchanged=unchanged, already=already)


def _assert_no_collisions(
    changes: list[RemapItem], unchanged: list[str], already: list[str]
) -> None:
    """Two repositories must not land on the same address. Silently merging two different
    tools into one is the worst thing that can happen here."""
    seen: dict[str, str] = {}
    fixed = set(unchanged) | set(already)
    for item in changes:
        if item.new_full in fixed:
            raise RuntimeError(
                f"{item.old_full} is moving to {item.new_full}, but an untouched"
                " repository is already there"
            )
        if item.new_full in seen:
            raise RuntimeError(
                f"collision: both {seen[item.new_full]} and {item.old_full} are"
                f" moving to {item.new_full}"
            )
        seen[item.new_full] = item.old_full


async def apply_item(
    session: Session, provider: GiteaProvider, item: RemapItem, gitea_url: str
) -> None:
    """Migrate one repository. Writes to Gitea and to the database.

    Order matters: first the rename within the old owner, then the transfer.
    That way the repository stays where we know it until the very last step.
    """
    base = gitea_url.rstrip("/")

    # The target address must not be taken: otherwise the transfer either fails or
    # overwrites someone else's. Check before doing anything.
    if await provider.repo_exists(item.new_owner, item.new_name):
        raise RuntimeError(f"{item.new_full} already exists — not migrating on top of it")

    if item.new_name != item.old_name:
        await provider.rename_repo(item.old_owner, item.old_name, item.new_name)

    if item.new_owner != item.old_owner:
        await provider.create_org(item.new_owner)
        await provider.transfer_repo(item.old_owner, item.new_name, item.new_owner)

    # Verify the repository really is where we intended it to go.
    if not await provider.repo_exists(item.new_owner, item.new_name):
        raise RuntimeError(
            f"migrated {item.old_full}, but it's not at {item.new_full} — stopping"
        )

    repo = session.get(Repository, item.repo_id)
    repo.owner = item.new_owner
    repo.name = item.new_name
    repo.html_url = f"{base}/{item.new_owner}/{item.new_name}"
    repo.clone_url = f"{base}/{item.new_owner}/{item.new_name}.git"
    # The source address now reads straight from the path — but record it explicitly too.
    repo.original_url = f"https://github.com/{item.upstream_repo}"
