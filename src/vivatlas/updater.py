"""Put the new version in place of the old one.

The second — and last — place in the program that writes to Git. The rules are
just as strict as for import, but here there's exactly one reason to refuse and
it's the main one:

    we update ONLY what you haven't touched.

If the copy was edited, overwriting would wipe the edit silently and without a
trace. So the state is re-checked live, right before writing, rather than taken
from the database: the file could have been edited between yesterday's check
and today's command.

A single file is replaced — the anchor, the very one we compare against. We
can't and don't pretend to pull the whole repository again: files the source
doesn't have would be left dangling, and the card would lie that everything is
up to date.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from vivatlas.models import UpstreamLink
from vivatlas.providers.base import RepoRef
from vivatlas.providers.gitea import GiteaProvider
from vivatlas.upstream import STATUS_NAMES, UpstreamChecker
from vivatlas.upstream_sync import check_link

log = logging.getLogger(__name__)


class UpdateRefused(RuntimeError):
    """Can't update. The reason is in the text — a human needs to read it."""


@dataclass
class UpdatePlan:
    link_id: int
    artifact_name: str
    repo_full_name: str
    path: str  # what we'll replace on our side
    upstream_repo: str
    upstream_path: str
    old_sha: str
    new_sha: str
    content: bytes

    @property
    def size_kb(self) -> float:
        return len(self.content) / 1024


def _repo_ref(link: UpstreamLink) -> RepoRef:
    repo = link.artifact.repository
    return RepoRef(
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


async def plan_update(
    session: Session,
    provider: GiteaProvider,
    checker: UpstreamChecker,
    link: UpstreamLink,
) -> UpdatePlan:
    """What exactly we'll replace. Writes nothing — just looks.

    A refusal here is a normal outcome, not a failure. Of the five states, only
    one can be updated.
    """
    if link.kind == "gitea-mirror":
        raise UpdateRefused(
            "this is a mirror — Gitea pulls it itself, and our write would break its sync"
        )

    status = await check_link(session, provider, checker, link, link.artifact.repository)
    session.commit()

    if status != "update-available":
        why = {
            "in-sync": "the same thing is already in place — nothing to update",
            "locally-modified": "you edited this copy — overwriting would wipe your edit",
            "diverged": "you edited it and the source has new content — this needs "
            "hands, not this command",
            "unknown": link.check_error or STATUS_NAMES.get(status, status),
        }.get(status, STATUS_NAMES.get(status, status))
        raise UpdateRefused(why)

    if not link.upstream_path:
        raise UpdateRefused("we don't know where the source keeps this file")

    anchor = link.artifact.anchor_path
    if not anchor:
        raise UpdateRefused("the card has no anchor file — nothing to replace")

    content = await checker.blob(link.upstream_repo, link.last_upstream_sha)
    if not content:
        raise UpdateRefused("the source file is empty — we don't replace with that")

    return UpdatePlan(
        link_id=link.id,
        artifact_name=link.artifact.name,
        repo_full_name=link.artifact.repository.full_name,
        path=anchor,
        upstream_repo=link.upstream_repo,
        upstream_path=link.upstream_path,
        old_sha=link.last_local_sha,
        new_sha=link.last_upstream_sha,
        content=content,
    )


async def apply_update(
    session: Session,
    provider: GiteaProvider,
    checker: UpstreamChecker,
    plan: UpdatePlan,
) -> str:
    """Write it. Returns the digest of what came out.

    Called only after explicit confirmation.
    """
    link = session.get(UpstreamLink, plan.link_id)
    owner, name = link.artifact.repository.owner, link.artifact.repository.name

    log.info("replacing %s/%s: %s", owner, name, plan.path)
    await provider.update_file(
        owner,
        name,
        plan.path,
        plan.content,
        message=f"Update from github.com/{plan.upstream_repo}",
        sha=plan.old_sha,  # Gitea will refuse if the file was edited in the meantime
        branch=link.artifact.repository.default_branch or "main",
    )

    # Verify that exactly the right thing landed. The digest is computed from the
    # content, so it must match the source. If it doesn't — Gitea did something to
    # the file along the way (line-ending conversion, for example), and the mark
    # would have lied.
    ref = _repo_ref(link)
    head = await provider.get_head_sha(ref)
    ours = await provider.blob_shas(ref, head)
    got = ours.get(plan.path, "")
    if got != plan.new_sha:
        raise RuntimeError(
            f"wrote it, but got the wrong thing: ours is {got[:8]}, the source's "
            f"is {plan.new_sha[:8]}. Not moving the mark — otherwise it would lie."
        )

    now = datetime.now(UTC)
    link.baseline_local_sha = got
    link.baseline_upstream_sha = plan.new_sha
    link.baseline_at = now
    link.last_local_sha = got
    link.last_upstream_sha = plan.new_sha
    link.last_checked_at = now
    link.status = "in-sync"
    link.check_error = None
    return got
