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
        art = Artifact(repository_id=repo.id, name=name, artifact_type=kind)
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

    add("airbnb", "design-lib", "design-kit", 2, [("typography", "назначение")], "in-sync")
    add(
        "stripe", "design-lib", "design-kit", 40, [("typography", "назначение")], "update-available"
    )
    add("scanner", "skills-lib", "skill", 5, [("security", "назначение"), ("python", "язык")])
    add("old-tool", "skills-lib", "skill", 200, [("python", "язык")], "locally-modified")
    s.commit()
    return s


def names(session, f):
    return sorted(a.name for a in session.scalars(filters.apply(select(Artifact), f)))


# --- по одному признаку ---


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


# --- вместе ---


def test_filters_combine_as_and_not_or(catalog):
    # Скилл И на питоне И свежий — только один.
    f = filters.Filters(type="skill", tag="python", days="7")
    assert names(catalog, f) == ["scanner"]


def test_impossible_combination_returns_nothing(catalog):
    f = filters.Filters(type="design-kit", tag="python")
    assert names(catalog, f) == []


def test_count_matches_the_list(catalog):
    f = filters.Filters(type="skill")
    assert filters.count_matching(catalog, f) == len(names(catalog, f))


# --- варианты для показа ---


def test_only_real_tags_are_offered(catalog):
    # Тег, который никому не поставлен, предлагать нельзя: выбор, который
    # ничего не находит, только раздражает.
    catalog.add(Tag(slug="never-used", label="never-used", category="назначение"))
    catalog.commit()

    groups = filters.tag_groups(catalog)
    offered = {o.value for g in groups for o in g.options}
    assert "never-used" not in offered
    assert "python" in offered


def test_tag_groups_are_split_by_category(catalog):
    groups = {g.key: [o.value for o in g.options] for g in filters.tag_groups(catalog)}
    assert "python" in groups["язык"]
    assert "security" in groups["назначение"]


def test_status_options_hide_empty_ones(catalog):
    values = {o.value for o in filters.status_options(catalog)}
    assert values == {"in-sync", "update-available", "locally-modified"}
    assert "diverged" not in values  # такого ни у кого нет


def test_status_options_put_actionable_first(catalog):
    # "Вышла новая версия" — единственное, что требует действия. Оно и первое.
    assert filters.status_options(catalog)[0].value == "update-available"


def test_counts_are_real(catalog):
    types = {o.value: o.count for o in filters.type_options(catalog)}
    assert types == {"design-kit": 2, "skill": 2}


# --- сборка ссылок ---


def test_query_drops_one_filter_keeps_others():
    f = filters.Filters(type="skill", tag="python", days="7")
    assert f.as_query(drop="tag") == {"type": "skill", "days": "7"}


def test_query_skips_empty_values():
    assert filters.Filters(type="skill").as_query() == {"type": "skill"}


def test_active_knows_when_nothing_is_set():
    assert filters.Filters().active() is False
    assert filters.Filters(tag="x").active() is True
