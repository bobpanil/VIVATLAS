"""Что появилось, изменилось, пропало и что протухло.

Изменения записываются в момент сканирования, а не вычисляются задним числом.
Иначе после удаления репозитория узнать, что он вообще был, уже неоткуда.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from skill_atlas.models import Artifact, Change, Repository

log = logging.getLogger(__name__)

# Не трогали столько — считаем протухшим. Год выбран потому, что инструмент,
# к которому не возвращались год, скорее всего уже не нужен.
STALE_DAYS = 365

KIND_NAMES = {
    "added": "появилось",
    "updated": "изменилось",
    "removed": "пропало",
    "renamed": "переименовано",
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


def recent(session: Session, limit: int = 50, kind: str = "") -> list[Change]:
    query = select(Change).order_by(Change.created_at.desc(), Change.id.desc())
    if kind:
        query = query.where(Change.kind == kind)
    return list(session.scalars(query.limit(limit)))


def since(session: Session, days: int = 30) -> list[Change]:
    edge = datetime.now(UTC) - timedelta(days=days)
    return list(
        session.scalars(
            select(Change).where(Change.created_at >= edge).order_by(Change.created_at.desc())
        )
    )


def summary(session: Session, days: int = 30) -> dict[str, int]:
    edge = datetime.now(UTC) - timedelta(days=days)
    rows = session.execute(
        select(Change.kind, func.count()).where(Change.created_at >= edge).group_by(Change.kind)
    ).all()
    return {kind: count for kind, count in rows}


def stale(session: Session, days: int = STALE_DAYS) -> list[StaleItem]:
    """Что не трогали дольше срока.

    Считаем по дате последнего коммита в репозитории, а не по дате нашего
    сканирования: нас интересует, когда вещь трогали в последний раз, а не
    когда мы её последний раз читали.
    """
    edge = datetime.now(UTC) - timedelta(days=days)
    now = datetime.now(UTC)
    out: list[StaleItem] = []

    rows = session.scalars(
        select(Artifact)
        .join(Repository)
        .where(Repository.gone_at.is_(None))
        .order_by(Repository.remote_updated_at)
    ).all()

    for a in rows:
        updated = a.repository.remote_updated_at
        if updated is None:
            continue
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        if updated >= edge:
            continue

        age = (now - updated).days
        reasons = [f"не трогали {age} дней"]
        if a.repository.is_archived:
            reasons.append("репозиторий заархивирован")
        out.append(StaleItem(artifact=a, days=age, reason=", ".join(reasons)))

    out.sort(key=lambda s: -s.days)
    return out


def oldest_and_newest(session: Session) -> tuple[int | None, int | None]:
    """Возраст самого старого и самого свежего — чтобы понимать, есть ли смысл
    вообще искать протухшее."""
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
