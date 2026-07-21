from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from vivatlas import filters
from vivatlas.models import Artifact, ArtifactTag, Repository, Source, Tag, UpstreamLink


@pytest.fixture
def catalog(make_session):
    s = make_session()
    source = Source(kind="f", base_url="https://x", display_name="F")
    s.add(source)
    s.flush()

    def add(name, owner, kind, days_old, tags, status=None):
        repo = Repository(
            source_id=source.id,
            external_id=name,
            owner=owner,
            name=name,
            default_branch="main",
            remote_updated_at=datetime.now(UTC) - timedelta(days=days_old),
        )
        s.add(repo)
        s.flush()
        art = Artifact(repository_id=repo.id, name=name, artifact_type=kind, shared=True)
        s.add(art)
        s.flush()
        for slug, cat in tags:
            tag = s.scalar(select(Tag).where(Tag.slug == slug))
            if tag is None:
                tag = Tag(slug=slug, label=slug, category=cat)
                s.add(tag)
                s.flush()
            s.add(ArtifactTag(artifact_id=art.id, tag_id=tag.id, source="ai", confidence=0.9))
        if status:
            s.add(
                UpstreamLink(
                    artifact_id=art.id, kind="github-file", upstream_repo="a/b", status=status
                )
            )
        return art

    add("airbnb", "design-lib", "design-kit", 2, [("typography", "purpose")], "in-sync")
    add(
        "stripe", "design-lib", "design-kit", 40, [("typography", "purpose")], "update-available"
    )
    add("scanner", "skills-lib", "skill", 5, [("security", "purpose"), ("python", "language")])
    add("old-tool", "skills-lib", "skill", 200, [("python", "language")], "locally-modified")
    s.commit()
    return s


def names(session, f):
    return sorted(a.name for a in session.scalars(filters.apply(select(Artifact), f)))


# --- by a single attribute ---


def test_no_filters_returns_everything(catalog):
    assert len(names(catalog, filters.Filters())) == 4


def test_by_type(catalog):
    assert names(catalog, filters.Filters(type="skill")) == ["old-tool", "scanner"]


def test_by_owner(catalog):
    assert names(catalog, filters.Filters(owner="design-lib")) == ["airbnb", "stripe"]


def test_by_tag(catalog):
    assert names(catalog, filters.Filters(tag="python")) == ["old-tool", "scanner"]


def test_by_period(catalog):
    assert names(catalog, filters.Filters(days="7")) == ["airbnb", "scanner"]
    assert names(catalog, filters.Filters(days="90")) == ["airbnb", "scanner", "stripe"]


def test_by_upstream_status(catalog):
    assert names(catalog, filters.Filters(status="update-available")) == ["stripe"]


# --- combined ---


def test_filters_combine_as_and_not_or(catalog):
    # A skill AND in python AND recent — only one.
    f = filters.Filters(type="skill", tag="python", days="7")
    assert names(catalog, f) == ["scanner"]


def test_impossible_combination_returns_nothing(catalog):
    f = filters.Filters(type="design-kit", tag="python")
    assert names(catalog, f) == []


def test_count_matches_the_list(catalog):
    f = filters.Filters(type="skill")
    assert filters.count_matching(catalog, f) == len(names(catalog, f))


# --- options to display ---


def test_only_real_tags_are_offered(catalog):
    # A tag that nobody has been assigned can't be offered: a choice that
    # finds nothing is just annoying.
    catalog.add(Tag(slug="never-used", label="never-used", category="purpose"))
    catalog.commit()

    groups = filters.tag_groups(catalog)
    offered = {o.value for g in groups for o in g.options}
    assert "never-used" not in offered
    assert "python" in offered


def test_tag_groups_are_split_by_category(catalog):
    groups = {g.key: [o.value for o in g.options] for g in filters.tag_groups(catalog)}
    assert "python" in groups["language"]
    assert "security" in groups["purpose"]


def test_status_options_hide_empty_ones(catalog):
    values = {o.value for o in filters.status_options(catalog)}
    assert values == {"in-sync", "update-available", "locally-modified"}
    assert "diverged" not in values  # nobody has this one


def test_status_options_put_actionable_first(catalog):
    # "A new version is out" — the only one that needs action. So it comes first.
    assert filters.status_options(catalog)[0].value == "update-available"


def test_counts_are_real(catalog):
    types = {o.value: o.count for o in filters.type_options(catalog)}
    assert types == {"design-kit": 2, "skill": 2}


# --- building links ---


def test_query_drops_one_filter_keeps_others():
    f = filters.Filters(type="skill", tag="python", days="7")
    assert f.as_query(drop="tag") == {"type": "skill", "days": "7"}


def test_query_skips_empty_values():
    assert filters.Filters(type="skill").as_query() == {"type": "skill"}


def test_active_knows_when_nothing_is_set():
    assert filters.Filters().active() is False
    assert filters.Filters(tag="x").active() is True


# --- purpose: a derived "what it's for", filtered by resolving it from tags ---


@pytest.fixture
def pcatalog(make_session):
    """Cards whose tags resolve to a clear purpose. detect() needs at least two
    matching signals, so each card carries two purpose tags (or none)."""
    s = make_session()
    source = Source(kind="f", base_url="https://x", display_name="F")
    s.add(source)
    s.flush()

    def add(name, slugs, artifact_type="skill"):
        repo = Repository(
            source_id=source.id, external_id=name, owner="lib", name=name, default_branch="main"
        )
        s.add(repo)
        s.flush()
        art = Artifact(repository_id=repo.id, name=name, artifact_type=artifact_type, shared=True)
        s.add(art)
        s.flush()
        for slug in slugs:
            tag = s.scalar(select(Tag).where(Tag.slug == slug))
            if tag is None:
                tag = Tag(slug=slug, label=slug, category="purpose")
                s.add(tag)
                s.flush()
            s.add(ArtifactTag(artifact_id=art.id, tag_id=tag.id, source="ai", confidence=0.9))
        return art

    add("tokens", ["design-system", "typography"])   # -> design
    add("palette-kit", ["color-palette", "css"])      # -> design
    add("scanner", ["security-scanning", "sast"])     # -> security
    add("misc", ["python"])                            # -> unknown (no purpose signal)
    s.commit()
    return s


def _pnames(session, f):
    q = filters.apply(select(Artifact), f, session=session)
    return sorted(a.name for a in session.scalars(q))


def test_filter_by_purpose_design(pcatalog):
    assert _pnames(pcatalog, filters.Filters(purpose="design")) == ["palette-kit", "tokens"]


def test_filter_by_purpose_security(pcatalog):
    assert _pnames(pcatalog, filters.Filters(purpose="security")) == ["scanner"]


def test_purpose_options_count_and_hide_unknown(pcatalog):
    opts = {o.value: o.count for o in filters.purpose_options(pcatalog)}
    assert opts == {"design": 2, "security": 1}
    assert "unknown" not in opts  # an unclassifiable card is never offered as a facet


def test_purpose_options_ordered_as_in_purposes(pcatalog):
    # PURPOSES lists security before design, so the facet follows that order.
    assert [o.value for o in filters.purpose_options(pcatalog)] == ["security", "design"]


def test_purpose_without_session_is_skipped(pcatalog):
    # apply() with no session can't resolve a purpose (it's derived) — it must not
    # crash or silently drop everything; it simply doesn't apply the purpose filter.
    q = filters.apply(select(Artifact), filters.Filters(purpose="design"))
    got = sorted(a.name for a in pcatalog.scalars(q))
    assert got == ["misc", "palette-kit", "scanner", "tokens"]


def test_purpose_belongs_in_query_and_counts_as_active():
    f = filters.Filters(purpose="design")
    assert f.active() is True
    assert f.as_query() == {"purpose": "design"}
