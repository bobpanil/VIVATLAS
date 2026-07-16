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

from vivatlas.models import Artifact, ArtifactTag, Category, Repository, Source, Tag, UpstreamLink

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
    fav: str = ""
    cat: str = ""

    def active(self) -> bool:
        return any((self.type, self.tag, self.days, self.status, self.owner, self.fav, self.cat))

    def as_query(self, drop: str = "", **override) -> dict:
        """Для сборки ссылок: те же фильтры, но один снят или заменён."""
        out = {
            "type": self.type,
            "tag": self.tag,
            "days": self.days,
            "status": self.status,
            "owner": self.owner,
            "fav": self.fav,
            "cat": self.cat,
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


def visible_ids(user_id: int | None) -> Select:
    """id карточек, которые вправе видеть этот человек: всё из общей зоны плюс
    своё частное. Чужое частное — никогда. Граница зон в одном месте, чтобы её
    нельзя было забыть на каком-то экране."""
    return (
        select(Artifact.id)
        .join(Repository, Artifact.repository_id == Repository.id)
        .join(Source, Repository.source_id == Source.id)
        .where((Source.owner_user_id.is_(None)) | (Source.owner_user_id == user_id))
    )


def apply(
    query: Select, f: Filters, fav_ids: set[int] | None = None, user_id: int | None = None
) -> Select:
    # Зона — всегда: даже без фильтров человек видит только своё и общее.
    query = query.where(Artifact.id.in_(visible_ids(user_id)))
    if f.fav:
        # Избранное — личное: без известного пользователя показывать нечего.
        query = query.where(Artifact.id.in_(fav_ids if fav_ids is not None else set()))
    if f.cat and f.cat.isdigit():
        query = query.where(Artifact.category_id == int(f.cat))
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


def tag_groups(
    session: Session, limit_per_group: int = 8, user_id: int | None = None
) -> list[FilterGroup]:
    """Теги, разложенные по категориям. Только те, что реально стоят на карточках."""
    vis = visible_ids(user_id)
    rows = session.execute(
        select(Tag.category, Tag.slug, func.count(ArtifactTag.id))
        .join(ArtifactTag, ArtifactTag.tag_id == Tag.id)
        .where(ArtifactTag.artifact_id.in_(vis))
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


def category_options(session: Session, user_id: int | None = None) -> list[Option]:
    """Свои категории-папки со счётчиком. Показываем все, включая пустые:
    в пустую надо иметь возможность перетащить первую карточку."""
    vis = visible_ids(user_id)
    counts = dict(
        session.execute(
            select(Artifact.category_id, func.count())
            .where(Artifact.category_id.is_not(None), Artifact.id.in_(vis))
            .group_by(Artifact.category_id)
        ).all()
    )
    cats = session.scalars(select(Category).order_by(Category.position, Category.name)).all()
    return [Option(str(c.id), c.name, counts.get(c.id, 0)) for c in cats]


def type_options(session: Session, user_id: int | None = None) -> list[Option]:
    rows = session.execute(
        select(Artifact.artifact_type, func.count())
        .where(Artifact.id.in_(visible_ids(user_id)))
        .group_by(Artifact.artifact_type)
        .order_by(func.count().desc())
    ).all()
    return [Option(t, t, n) for t, n in rows]


def owner_options(session: Session, user_id: int | None = None) -> list[Option]:
    rows = session.execute(
        select(Repository.owner, func.count(Artifact.id))
        .join(Artifact, Artifact.repository_id == Repository.id)
        .where(Artifact.id.in_(visible_ids(user_id)))
        .group_by(Repository.owner)
        .order_by(func.count(Artifact.id).desc())
    ).all()
    return [Option(o, o, n) for o, n in rows]


def status_options(session: Session, user_id: int | None = None) -> list[Option]:
    """Состояния источника. Показываем только непустые: выбор, который ничего
    не находит, только раздражает."""
    from vivatlas.upstream import STATUS_NAMES

    rows = session.execute(
        select(UpstreamLink.status, func.count())
        .where(UpstreamLink.artifact_id.in_(visible_ids(user_id)))
        .group_by(UpstreamLink.status)
    ).all()
    order = {"update-available": 0, "diverged": 1, "locally-modified": 2, "in-sync": 3}
    out = [Option(s, STATUS_NAMES.get(s, s), n) for s, n in rows if n]
    out.sort(key=lambda o: order.get(o.value, 9))
    return out


def period_options(session: Session, user_id: int | None = None) -> list[Option]:
    out: list[Option] = []
    now = datetime.now(UTC)
    vis = visible_ids(user_id)
    for key, (label, days) in PERIODS.items():
        edge = now - timedelta(days=days)
        n = session.scalar(
            select(func.count(Artifact.id))
            .join(Repository)
            .where(Repository.remote_updated_at >= edge, Artifact.id.in_(vis))
        )
        if n:
            out.append(Option(key, label, n))
    return out
