"""Filter cards by attributes.

Filters are built from what actually exists in the database, not a made-up list.
Tag categories came from the model during tagging: purpose, platform, language,
format, runtime. We don't show empty filters — a choice that finds nothing
is just annoying.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from vivatlas import caticons, purposes
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

# Order matters: the most-used ones first.
CATEGORY_ORDER = ["purpose", "platform", "language", "format", "runtime", "type", "other"]

PERIODS = {
    "7": ("past week", 7),
    "30": ("past month", 30),
    "90": ("past three months", 90),
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
    purpose: str = ""  # the derived "what it's for" (design, security, testing, …)
    draft: str = ""
    zone: str = ""  # "private" | "common" — filter by card zone
    sort: str = ""  # "" | "name" | "updated" | "added" — order in the catalogue

    def active(self) -> bool:
        # Sorting is not a filter: it hides nothing, so it's not counted in the
        # active-filter count (badge) or in "clear all".
        return any(
            (self.type, self.tag, self.days, self.status, self.owner, self.fav,
             self.cat, self.purpose, self.zone)
        )

    def as_query(self, drop: str = "", **override) -> dict:
        """For building links: the same filters, but one dropped or replaced."""
        out = {
            "type": self.type,
            "tag": self.tag,
            "days": self.days,
            "status": self.status,
            "owner": self.owner,
            "fav": self.fav,
            "cat": self.cat,
            "purpose": self.purpose,
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
    owned: bool = False  # for folders: private (True) or shared (False)


@dataclass
class FilterGroup:
    key: str
    title: str
    options: list[Option] = field(default_factory=list)


def visible_ids(user_id: int | None) -> Select:
    """IDs of the cards this user is allowed to see. Visible if the card is
    shared OR this user is its owner. Someone else's private card — never.

    Ownership and visibility are separate: a shared card still belongs to its
    owner, and they still see their own unshared ones too. The zone boundary is
    in one place, so it can't be forgotten on some screen.

    An anonymous user (user_id empty) sees only shared ones: there's nothing to
    compare the owner against, and `owner == None` in SQL would become "whose
    owner is empty" and would show ownerless private cards — so we add that branch
    only when the user is known.
    """
    visible = Artifact.shared.is_(True)
    if user_id is not None:
        visible = visible | (Artifact.owner_user_id == user_id)
    return select(Artifact.id).where(Artifact.hidden.is_(False), visible)


def apply(
    query: Select,
    f: Filters,
    fav_ids: set[int] | None = None,
    user_id: int | None = None,
    session: Session | None = None,
) -> Select:
    # Zone always applies: even with no filters a user sees only their own and shared.
    query = query.where(Artifact.id.in_(visible_ids(user_id)))
    # Drafts get their own section: we don't show them in the main catalogue, and
    # draft=1 shows only them. That keeps unfinished work out of the finished.
    if f.draft:
        query = query.where(Artifact.artifact_type == "draft")
    else:
        query = query.where(Artifact.artifact_type != "draft")
    if f.fav:
        # Favourites are personal: with no known user there's nothing to show.
        query = query.where(Artifact.id.in_(fav_ids if fav_ids is not None else set()))
    if f.zone == "private":
        query = query.where(Artifact.shared.is_(False))
    elif f.zone == "common":
        query = query.where(Artifact.shared.is_(True))
    if f.cat and f.cat.isdigit():
        # Membership now lives in the link table. A folder the user may not see
        # (someone else's private) is kept out of the filter: otherwise an empty vs
        # non-empty result could probe its existence and which shared cards it holds.
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
    if f.purpose and session is not None:
        # Purpose isn't a stored column — it's derived from each card's tags + name,
        # the same way the card chip shows it. We resolve the matching ids here so the
        # filter can never disagree with what's on the card. Needs a session; without
        # one (a pure query build) the purpose filter is simply skipped.
        query = query.where(Artifact.id.in_(purpose_matching_ids(session, f.purpose, user_id)))
    return query


def count_matching(session: Session, f: Filters) -> int:
    return session.scalar(apply(select(func.count(Artifact.id)), f, session=session)) or 0


def sort_order(sort: str) -> list:
    """ORDER BY for browsing the catalogue. Default is by name (A→Z). "updated" —
    freshly updated on top, "added" — recently created. We take the source date via
    a subquery, not a join, so it won't clash with a possible join on owner."""
    if sort == "updated":
        upd = (
            select(Repository.remote_updated_at)
            .where(Repository.id == Artifact.repository_id)
            .scalar_subquery()
        )
        # In SQLite NULL sinks to the bottom on DESC by itself — undated cards end up last.
        return [upd.desc(), Artifact.name]
    if sort == "added":
        return [Artifact.created_at.desc(), Artifact.name]
    return [Artifact.name]


def tag_groups(
    session: Session, limit_per_group: int = 8, user_id: int | None = None, lang: str = "en"
) -> list[FilterGroup]:
    """Tags grouped by category. Only those actually attached to cards."""
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
        # "source" and "type" already exist as separate filters — don't duplicate them.
        if category in ("type",):
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
    """Folders with a count: shared + your own private (someone else's private —
    never, even for an admin). We show all, including empty ones: you need to be
    able to drag the first card into an empty one. Shared first, then your private.
    Name is in the interface language (folder translation), if one is set."""
    from vivatlas import catnames

    vis = visible_ids(user_id)
    vcats = visible_category_ids(user_id)
    # Count via the link table and only over visible cards AND visible folders: a
    # card in someone else's private folder must not land in anyone else's count.
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
        # Shared (owner empty) first, then private — each group by position.
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
    # Drafts are a separate section; we don't show them among the types.
    rows = session.execute(
        select(Artifact.artifact_type, func.count())
        .where(Artifact.id.in_(visible_ids(user_id)), Artifact.artifact_type != "draft")
        .group_by(Artifact.artifact_type)
        .order_by(func.count().desc())
    ).all()
    return [Option(t, t, n) for t, n in rows]


def zone_counts(session: Session, user_id: int | None = None) -> dict:
    """How many visible cards are in each zone — for the "private"/"shared" pills.
    Drafts don't count (they have their own section)."""
    vis = visible_ids(user_id)
    base = select(func.count()).select_from(Artifact).where(
        Artifact.id.in_(vis), Artifact.artifact_type != "draft"
    )
    return {
        "private": session.scalar(base.where(Artifact.shared.is_(False))) or 0,
        "common": session.scalar(base.where(Artifact.shared.is_(True))) or 0,
    }


def draft_count(session: Session, user_id: int | None = None) -> int:
    """How many drafts this user has — for the permanent "Drafts" section."""
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
    """Source statuses. We show only non-empty ones: a choice that finds nothing
    is just annoying."""
    from vivatlas import i18n

    rows = session.execute(
        select(UpstreamLink.status, func.count())
        .where(UpstreamLink.artifact_id.in_(visible_ids(user_id)))
        .group_by(UpstreamLink.status)
    ).all()
    order = {"update-available": 0, "diverged": 1, "locally-modified": 2, "in-sync": 3}
    # "unknown" means the source was found but never compared yet — "nothing to
    # compare with". It's the absence of a status, not a state worth filtering by, so
    # we never offer it as a facet (it would just read as noise in the filter list).
    out = [Option(s, i18n.label("status", s, lang), n) for s, n in rows if n and s != "unknown"]
    out.sort(key=lambda o: order.get(o.value, 9))
    return out


def _purpose_map(session: Session, user_id: int | None = None) -> dict[int, str]:
    """The derived purpose key for every visible, non-draft card, computed from its
    tags + name exactly as the card chip does. One pass over two batched queries, so
    the filter and the chip can never disagree. Drafts are excluded — they have their
    own section and no purpose to speak of."""
    vis = visible_ids(user_id)
    names = dict(
        session.execute(
            select(Artifact.id, Artifact.name).where(
                Artifact.id.in_(vis), Artifact.artifact_type != "draft"
            )
        ).all()
    )
    tags: dict[int, list[str]] = {}
    for aid, slug in session.execute(
        select(ArtifactTag.artifact_id, Tag.slug)
        .join(Tag, Tag.id == ArtifactTag.tag_id)
        .where(ArtifactTag.artifact_id.in_(vis))
    ).all():
        tags.setdefault(aid, []).append(slug)
    return {
        aid: purposes.detect(tags.get(aid, []), name or "")[0].key for aid, name in names.items()
    }


def purpose_matching_ids(
    session: Session, purpose_key: str, user_id: int | None = None
) -> list[int]:
    """Ids of visible cards whose derived purpose is `purpose_key`."""
    return [aid for aid, key in _purpose_map(session, user_id).items() if key == purpose_key]


def purpose_options(
    session: Session, user_id: int | None = None, lang: str = "en"
) -> list[Option]:
    """Purposes with a count, ordered as in purposes.PURPOSES. "unknown" (a card we
    couldn't classify) is never offered — like an empty filter, it's just noise."""
    from vivatlas import i18n

    counts: dict[str, int] = {}
    for key in _purpose_map(session, user_id).values():
        if key == "unknown":
            continue
        counts[key] = counts.get(key, 0) + 1
    order = {p.key: i for i, (p, _) in enumerate(purposes.PURPOSES)}
    out = [Option(k, i18n.label("purpose", k, lang), n) for k, n in counts.items()]
    out.sort(key=lambda o: order.get(o.value, 99))
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
