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

from vivatlas import caticons
from vivatlas.categories import visible_category_ids
from vivatlas.models import (
    Artifact,
    ArtifactCategory,
    ArtifactTag,
    Category,
    Repository,
    Tag,
    UpstreamLink,
)

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
    draft: str = ""
    zone: str = ""  # "private" | "common" — отбор по зоне карточки
    sort: str = ""  # "" | "name" | "updated" | "added" — порядок в каталоге

    def active(self) -> bool:
        # Сортировка — не фильтр: она ничего не прячет, поэтому в счётчик
        # активных фильтров (бейдж) и в «снять всё» не входит.
        return any(
            (self.type, self.tag, self.days, self.status, self.owner, self.fav, self.cat, self.zone)
        )

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
            "draft": self.draft,
            "zone": self.zone,
            "sort": self.sort,
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
    icon: str = ""
    color: str = ""
    owned: bool = False  # для папок: личная (True) или общая (False)


@dataclass
class FilterGroup:
    key: str
    title: str
    options: list[Option] = field(default_factory=list)


def visible_ids(user_id: int | None) -> Select:
    """id карточек, которые вправе видеть этот человек. Видно, если карточка
    общая (shared) ИЛИ этот человек — её владелец. Чужое личное — никогда.

    Владение и видимость раздельны: расшаренная карточка остаётся за своим
    владельцем, а он всё равно видит и свои неразделённые. Граница зон в одном
    месте, чтобы её нельзя было забыть на каком-то экране.

    Аноним (user_id пуст) видит только общие: сравнивать владельца не с чем, а
    `owner == None` в SQL превратилось бы в «у кого владелец пуст» и показало бы
    бесхозные личные — поэтому эту ветку добавляем только при известном человеке.
    """
    visible = Artifact.shared.is_(True)
    if user_id is not None:
        visible = visible | (Artifact.owner_user_id == user_id)
    return select(Artifact.id).where(Artifact.hidden.is_(False), visible)


def apply(
    query: Select, f: Filters, fav_ids: set[int] | None = None, user_id: int | None = None
) -> Select:
    # Зона — всегда: даже без фильтров человек видит только своё и общее.
    query = query.where(Artifact.id.in_(visible_ids(user_id)))
    # Черновики — свой раздел: в общем каталоге их не показываем, а по draft=1
    # показываем только их. Так недоделанное не мешается с готовым.
    if f.draft:
        query = query.where(Artifact.artifact_type == "draft")
    else:
        query = query.where(Artifact.artifact_type != "draft")
    if f.fav:
        # Избранное — личное: без известного пользователя показывать нечего.
        query = query.where(Artifact.id.in_(fav_ids if fav_ids is not None else set()))
    if f.zone == "private":
        query = query.where(Artifact.shared.is_(False))
    elif f.zone == "common":
        query = query.where(Artifact.shared.is_(True))
    if f.cat and f.cat.isdigit():
        # Членство теперь в связке. Папку, которую человек не вправе видеть (чужая
        # личная), в отбор не пускаем: иначе по пустоте/непустоте результата можно
        # было бы прощупать её существование и что в ней из общего.
        query = query.where(
            Artifact.id.in_(
                select(ArtifactCategory.artifact_id).where(
                    ArtifactCategory.category_id == int(f.cat),
                    ArtifactCategory.category_id.in_(visible_category_ids(user_id)),
                )
            )
        )
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


def sort_order(sort: str) -> list:
    """ORDER BY для просмотра каталога. По умолчанию — по имени (А→Я). «updated» —
    свежеобновлённые сверху, «added» — недавно заведённые. Дату источника берём
    подзапросом, а не join, чтобы не спорить с возможным join по владельцу."""
    if sort == "updated":
        upd = (
            select(Repository.remote_updated_at)
            .where(Repository.id == Artifact.repository_id)
            .scalar_subquery()
        )
        # В SQLite NULL при DESC уходит вниз сам — карточки без даты будут в конце.
        return [upd.desc(), Artifact.name]
    if sort == "added":
        return [Artifact.created_at.desc(), Artifact.name]
    return [Artifact.name]


def tag_groups(
    session: Session, limit_per_group: int = 8, user_id: int | None = None, lang: str = "en"
) -> list[FilterGroup]:
    """Теги, разложенные по категориям. Только те, что реально стоят на карточках."""
    from vivatlas import i18n

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
        groups.append(
            FilterGroup(
                key=category,
                title=i18n.label("tagcat", category, lang),
                options=options[:limit_per_group],
            )
        )
    return groups


def category_options(
    session: Session, user_id: int | None = None, lang: str | None = None
) -> list[Option]:
    """Папки со счётчиком: общие + свои личные (чужие личные — никогда, даже
    администратору). Показываем все, включая пустые: в пустую надо иметь
    возможность перетащить первую карточку. Сначала общие, потом свои личные.
    Название — на языке интерфейса (перевод папки), если он задан."""
    from vivatlas import catnames

    vis = visible_ids(user_id)
    vcats = visible_category_ids(user_id)
    # Счёт по связке и только по видимым карточкам И видимым папкам: карточка в
    # чужой личной папке не должна попадать ни в чей чужой счётчик.
    counts = dict(
        session.execute(
            select(ArtifactCategory.category_id, func.count())
            .where(
                ArtifactCategory.artifact_id.in_(vis),
                ArtifactCategory.category_id.in_(vcats),
            )
            .group_by(ArtifactCategory.category_id)
        ).all()
    )
    cats = session.scalars(
        select(Category)
        .where(Category.id.in_(vcats))
        # Общие (owner пуст) первыми, затем личные — каждая группа по position.
        .order_by(Category.owner_user_id.is_not(None), Category.position, Category.name)
    ).all()
    return [
        Option(
            str(c.id),
            catnames.label(c.names_json, c.name, lang) if lang else c.name,
            counts.get(c.id, 0),
            c.icon,
            caticons.category_color(c.id),
            owned=c.owner_user_id is not None,
        )
        for c in cats
    ]


def type_options(session: Session, user_id: int | None = None) -> list[Option]:
    # Черновики — отдельный раздел, среди типов их не показываем.
    rows = session.execute(
        select(Artifact.artifact_type, func.count())
        .where(Artifact.id.in_(visible_ids(user_id)), Artifact.artifact_type != "draft")
        .group_by(Artifact.artifact_type)
        .order_by(func.count().desc())
    ).all()
    return [Option(t, t, n) for t, n in rows]


def zone_counts(session: Session, user_id: int | None = None) -> dict:
    """Сколько видимых карточек в каждой зоне — для пилюль «частная»/«общая».
    Черновики не в счёт (у них свой раздел)."""
    vis = visible_ids(user_id)
    base = select(func.count()).select_from(Artifact).where(
        Artifact.id.in_(vis), Artifact.artifact_type != "draft"
    )
    return {
        "private": session.scalar(base.where(Artifact.shared.is_(False))) or 0,
        "common": session.scalar(base.where(Artifact.shared.is_(True))) or 0,
    }


def draft_count(session: Session, user_id: int | None = None) -> int:
    """Сколько черновиков у этого человека — для постоянного раздела «Черновики»."""
    return session.scalar(
        select(func.count())
        .select_from(Artifact)
        .where(Artifact.id.in_(visible_ids(user_id)), Artifact.artifact_type == "draft")
    ) or 0


def owner_options(session: Session, user_id: int | None = None) -> list[Option]:
    rows = session.execute(
        select(Repository.owner, func.count(Artifact.id))
        .join(Artifact, Artifact.repository_id == Repository.id)
        .where(Artifact.id.in_(visible_ids(user_id)))
        .group_by(Repository.owner)
        .order_by(func.count(Artifact.id).desc())
    ).all()
    return [Option(o, o, n) for o, n in rows]


def status_options(
    session: Session, user_id: int | None = None, lang: str = "en"
) -> list[Option]:
    """Состояния источника. Показываем только непустые: выбор, который ничего
    не находит, только раздражает."""
    from vivatlas import i18n

    rows = session.execute(
        select(UpstreamLink.status, func.count())
        .where(UpstreamLink.artifact_id.in_(visible_ids(user_id)))
        .group_by(UpstreamLink.status)
    ).all()
    order = {"update-available": 0, "diverged": 1, "locally-modified": 2, "in-sync": 3}
    out = [Option(s, i18n.label("status", s, lang), n) for s, n in rows if n]
    out.sort(key=lambda o: order.get(o.value, 9))
    return out


def period_options(
    session: Session, user_id: int | None = None, lang: str = "en"
) -> list[Option]:
    from vivatlas import i18n

    out: list[Option] = []
    now = datetime.now(UTC)
    vis = visible_ids(user_id)
    for key, (_label, days) in PERIODS.items():
        edge = now - timedelta(days=days)
        n = session.scalar(
            select(func.count(Artifact.id))
            .join(Repository)
            .where(Repository.remote_updated_at >= edge, Artifact.id.in_(vis))
        )
        if n:
            out.append(Option(key, i18n.label("period", key, lang), n))
    return out
