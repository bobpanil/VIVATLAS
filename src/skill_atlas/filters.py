"""Отбор карточек по признакам.

Фильтры строятся из того, что в базе правда есть, а не из выдуманного списка.
Категории тегов пришли от модели при разметке: назначение, платформа, язык,
формат, запуск. Пустых фильтров не показываем — выбор, который ничего не
находит, только раздражает.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from skill_atlas.models import Artifact, ArtifactTag, Repository, Tag, UpstreamLink

# Порядок важен: сначала то, чем пользуются чаще.
CATEGORY_ORDER = ["назначение", "платформа", "язык", "формат", "запуск", "тип", "прочее"]

PERIODS = {
    "7": ("за неделю", 7),
    "30": ("за месяц", 30),
    "90": ("за три месяца", 90),
}


@dataclass
class Filters:
    type: str = ""
    tag: str = ""
    days: str = ""
    status: str = ""
    owner: str = ""

    def active(self) -> bool:
        return any((self.type, self.tag, self.days, self.status, self.owner))

    def as_query(self, drop: str = "", **override) -> dict:
        """Для сборки ссылок: те же фильтры, но один снят или заменён."""
        out = {
            "type": self.type,
            "tag": self.tag,
            "days": self.days,
            "status": self.status,
            "owner": self.owner,
        }
        out.update(override)
        if drop:
            out[drop] = ""
        return {k: v for k, v in out.items() if v}


@dataclass
class Option:
    value: str
    label: str
    count: int


@dataclass
class FilterGroup:
    key: str
    title: str
    options: list[Option] = field(default_factory=list)


def apply(query: Select, f: Filters) -> Select:
    if f.type:
        query = query.where(Artifact.artifact_type == f.type)
    if f.owner:
        query = query.join(Repository, isouter=False).where(Repository.owner == f.owner)
    if f.tag:
        query = query.where(
            Artifact.id.in_(select(ArtifactTag.artifact_id).join(Tag).where(Tag.slug == f.tag))
        )
    if f.days and f.days in PERIODS:
        edge = datetime.now(UTC) - timedelta(days=PERIODS[f.days][1])
        query = query.where(
            Artifact.id.in_(
                select(Artifact.id).join(Repository).where(Repository.remote_updated_at >= edge)
            )
        )
    if f.status:
        query = query.where(
            Artifact.id.in_(select(UpstreamLink.artifact_id).where(UpstreamLink.status == f.status))
        )
    return query


def count_matching(session: Session, f: Filters) -> int:
    return session.scalar(apply(select(func.count(Artifact.id)), f)) or 0


def tag_groups(session: Session, limit_per_group: int = 8) -> list[FilterGroup]:
    """Теги, разложенные по категориям. Только те, что реально стоят на карточках."""
    rows = session.execute(
        select(Tag.category, Tag.slug, func.count(ArtifactTag.id))
        .join(ArtifactTag, ArtifactTag.tag_id == Tag.id)
        .group_by(Tag.id)
        .order_by(func.count(ArtifactTag.id).desc())
    ).all()

    by_cat: dict[str, list[Option]] = {}
    for category, slug, count in rows:
        by_cat.setdefault(category, []).append(Option(slug, slug, count))

    groups: list[FilterGroup] = []
    for category in CATEGORY_ORDER:
        options = by_cat.get(category)
        if not options:
            continue
        # "источник" и "тип" уже есть отдельными фильтрами — не дублируем.
        if category in ("тип",):
            continue
        groups.append(FilterGroup(key=category, title=category, options=options[:limit_per_group]))
    return groups


def type_options(session: Session) -> list[Option]:
    rows = session.execute(
        select(Artifact.artifact_type, func.count())
        .group_by(Artifact.artifact_type)
        .order_by(func.count().desc())
    ).all()
    return [Option(t, t, n) for t, n in rows]


def owner_options(session: Session) -> list[Option]:
    rows = session.execute(
        select(Repository.owner, func.count(Artifact.id))
        .join(Artifact, Artifact.repository_id == Repository.id)
        .group_by(Repository.owner)
        .order_by(func.count(Artifact.id).desc())
    ).all()
    return [Option(o, o, n) for o, n in rows]


def status_options(session: Session) -> list[Option]:
    """Состояния источника. Показываем только непустые: выбор, который ничего
    не находит, только раздражает."""
    from skill_atlas.upstream import STATUS_NAMES

    rows = session.execute(
        select(UpstreamLink.status, func.count()).group_by(UpstreamLink.status)
    ).all()
    order = {"update-available": 0, "diverged": 1, "locally-modified": 2, "in-sync": 3}
    out = [Option(s, STATUS_NAMES.get(s, s), n) for s, n in rows if n]
    out.sort(key=lambda o: order.get(o.value, 9))
    return out


def period_options(session: Session) -> list[Option]:
    out: list[Option] = []
    now = datetime.now(UTC)
    for key, (label, days) in PERIODS.items():
        edge = now - timedelta(days=days)
        n = session.scalar(
            select(func.count(Artifact.id))
            .join(Repository)
            .where(Repository.remote_updated_at >= edge)
        )
        if n:
            out.append(Option(key, label, n))
    return out
