"""What appeared, changed, disappeared, and what went stale.

Changes are recorded at scan time, not computed after the fact.
Otherwise, once a repository is deleted, there's no way to know it ever existed.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vivatlas.models import Artifact, Change, Repository

log = logging.getLogger(__name__)

# Untouched this long — treat as stale. A year was chosen because a tool
# you haven't come back to in a year is most likely no longer needed.
STALE_DAYS = 365

KIND_NAMES = {
    "added": "appeared",
    "updated": "changed",
    "removed": "gone",
    "renamed": "renamed",
}

KIND_MARKS = {
    "added": "+",
    "updated": "~",
    "removed": "−",
    "renamed": "→",
}


@dataclass
class StaleItem:
    artifact: Artifact
    days: int
    reason: str


# The default for the visibility-scoping argument. Distinct from None (which means
# "anonymous — only shared cards"): _UNSET means "no scoping at all", for trusted
# local callers like the CLI. Every user-facing caller MUST pass a real user_id (int
# or None) so a person never sees change entries for someone else's private cards.
_UNSET = object()


def _scope_changes(query, user_id):
    """Limit a Change query to rows whose artifact the user may see."""
    if user_id is _UNSET:
        return query
    from vivatlas.filters import visible_ids

    return query.where(Change.artifact_id.in_(visible_ids(None if user_id is None else user_id)))


def record(
    session: Session,
    kind: str,
    repository_id: int,
    artifact_id: int | None = None,
    title: str = "",
    details: str = "",
    scan_run_id: int | None = None,
) -> Change:
    change = Change(
        kind=kind,
        repository_id=repository_id,
        artifact_id=artifact_id,
        title=title,
        details=details,
        scan_run_id=scan_run_id,
    )
    session.add(change)
    return change


def recent(session: Session, limit: int = 50, kind: str = "", user_id=_UNSET) -> list[Change]:
    query = select(Change).order_by(Change.created_at.desc(), Change.id.desc())
    if kind:
        query = query.where(Change.kind == kind)
    query = _scope_changes(query, user_id)
    return list(session.scalars(query.limit(limit)))


def since(session: Session, days: int = 30, user_id=_UNSET) -> list[Change]:
    edge = datetime.now(UTC) - timedelta(days=days)
    query = select(Change).where(Change.created_at >= edge).order_by(Change.created_at.desc())
    query = _scope_changes(query, user_id)
    return list(session.scalars(query))


def summary(session: Session, days: int = 30, user_id=_UNSET) -> dict[str, int]:
    edge = datetime.now(UTC) - timedelta(days=days)
    query = select(Change.kind, func.count()).where(Change.created_at >= edge)
    query = _scope_changes(query, user_id).group_by(Change.kind)
    rows = session.execute(query).all()
    return {kind: count for kind, count in rows}


def stale(session: Session, days: int = STALE_DAYS, user_id=_UNSET) -> list[StaleItem]:
    """What hasn't been touched longer than the threshold.

    We go by the date of the last commit in the repository, not by our scan
    date: we care about when the thing was last touched, not when we last
    read it.
    """
    edge = datetime.now(UTC) - timedelta(days=days)
    now = datetime.now(UTC)
    out: list[StaleItem] = []

    query = (
        select(Artifact)
        .join(Repository)
        .where(Repository.gone_at.is_(None))
        .order_by(Repository.remote_updated_at)
    )
    if user_id is not _UNSET:
        from vivatlas.filters import visible_ids

        query = query.where(Artifact.id.in_(visible_ids(None if user_id is None else user_id)))
    rows = session.scalars(query).all()

    for a in rows:
        updated = a.repository.remote_updated_at
        if updated is None:
            continue
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        if updated >= edge:
            continue

        age = (now - updated).days
        reasons = [f"untouched for {age} days"]
        if a.repository.is_archived:
            reasons.append("repository archived")
        out.append(StaleItem(artifact=a, days=age, reason=", ".join(reasons)))

    out.sort(key=lambda s: -s.days)
    return out


def oldest_and_newest(session: Session) -> tuple[int | None, int | None]:
    """Age of the oldest and newest — to tell whether it's even worth
    looking for stale items at all."""
    now = datetime.now(UTC)
    dates = [
        r.remote_updated_at
        for r in session.scalars(select(Repository).where(Repository.gone_at.is_(None)))
        if r.remote_updated_at
    ]
    if not dates:
        return None, None
    dates = [d.replace(tzinfo=UTC) if d.tzinfo is None else d for d in dates]
    return (now - min(dates)).days, (now - max(dates)).days
