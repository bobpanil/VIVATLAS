import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from vivatlas.models import Artifact, ArtifactTag, Base, Repository, Source, Tag, TagSuppression
from vivatlas.tagger import (
    add_manual_tag,
    apply_tags,
    derive_tags,
    remove_tag,
    tag_artifact,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        source = Source(kind="fake", base_url="https://x", display_name="Fake")
        s.add(source)
        s.flush()
        repo = Repository(
            source_id=source.id,
            external_id="1",
            owner="skills-lib",
            name="brandkit",
            default_branch="main",
        )
        s.add(repo)
        s.flush()
        s.add(
            Artifact(
                repository_id=repo.id,
                name="brandkit",
                artifact_type="claude-skill",
                summary_normal="Assembles brand kits",
                summary_technical="Uses tokens",
                file_paths=json.dumps(["SKILL.md", "src/main.py", "pyproject.toml"]),
            )
        )
        s.commit()
        yield s


def tags_of(session, artifact_id: int) -> set[str]:
    return {
        t.slug
        for t in session.scalars(
            select(Tag).join(ArtifactTag).where(ArtifactTag.artifact_id == artifact_id)
        )
    }


class FakeModel:
    model = "fake"

    def __init__(self, tags: list[dict]) -> None:
        self.tags = tags

    async def generate_json(self, prompt, schema):
        return {"tags": self.tags}

    async def aclose(self): ...


# --- rules ---


def test_derives_tags_from_files_and_type(session):
    art = session.scalar(select(Artifact))
    slugs = {s for s, _c, _conf in derive_tags(art)}
    assert "python" in slugs  # from pyproject.toml and .py
    assert "claude" in slugs  # from the claude-skill type
    assert "skills-lib" in slugs  # owner


def test_derive_is_deterministic(session):
    art = session.scalar(select(Artifact))
    assert sorted(derive_tags(art)) == sorted(derive_tags(art))


# --- KEY: a removed tag never comes back ---


async def test_deleted_auto_tag_never_comes_back(session):
    art = session.scalar(select(Artifact))

    await tag_artifact(session, art, model=None)
    session.commit()
    assert "python" in tags_of(session, art.id)

    # User says: this isn't python.
    remove_tag(session, art.id, "python", reason="not python")
    session.commit()
    assert "python" not in tags_of(session, art.id)

    # Re-scanned twice more — the tag must stay removed.
    await tag_artifact(session, art, model=None)
    session.commit()
    await tag_artifact(session, art, model=None)
    session.commit()

    assert "python" not in tags_of(session, art.id), "the removed tag came back"


async def test_suppression_also_blocks_ai_tags(session):
    # The suppression must also apply to model tags, not just to rules.
    art = session.scalar(select(Artifact))
    remove_tag(session, art.id, "pdf", reason="no pdf here")
    session.commit()

    model = FakeModel([{"slug": "pdf", "category": "format", "confidence": 0.99}])
    await tag_artifact(session, art, model=model)
    session.commit()

    assert "pdf" not in tags_of(session, art.id)


def test_suppression_record_is_created(session):
    art = session.scalar(select(Artifact))
    add_manual_tag(session, art.id, "python")
    session.commit()

    remove_tag(session, art.id, "python", reason="mistake")
    session.commit()

    ban = session.scalar(select(TagSuppression).where(TagSuppression.artifact_id == art.id))
    assert ban is not None
    assert ban.reason == "mistake"


# --- manual wins ---


async def test_manual_tag_is_not_overwritten_by_auto(session):
    art = session.scalar(select(Artifact))
    add_manual_tag(session, art.id, "python", category="language")
    session.commit()

    await tag_artifact(session, art, model=None)
    session.commit()

    link = session.scalar(
        select(ArtifactTag).join(Tag).where(ArtifactTag.artifact_id == art.id, Tag.slug == "python")
    )
    assert link.source == "manual"  # not overwritten to derived
    assert link.confidence == 1.0
    assert link.manually_confirmed is True


def test_manual_tag_lifts_suppression(session):
    # Changed my mind: removed it first, then set it by hand. The suppression must lift.
    art = session.scalar(select(Artifact))
    remove_tag(session, art.id, "python")
    session.commit()

    add_manual_tag(session, art.id, "python")
    session.commit()

    assert "python" in tags_of(session, art.id)
    assert (
        session.scalar(select(TagSuppression).where(TagSuppression.artifact_id == art.id)) is None
    )


# --- threshold ---


def test_weak_tags_are_not_applied(session):
    art = session.scalar(select(Artifact))
    applied, rejected, weak = apply_tags(
        session,
        art,
        [("uncertain", "other", 0.3), ("sure", "other", 0.9)],
        source="ai",
        origin="test",
    )
    session.commit()

    assert applied == 1
    assert weak == 1
    assert "sure" in tags_of(session, art.id)
    assert "uncertain" not in tags_of(session, art.id)


# --- model tags ---


async def test_ai_tags_with_bad_slugs_are_dropped(session):
    art = session.scalar(select(Artifact))
    model = FakeModel(
        [
            {"slug": "Good Tag With Spaces", "category": "other", "confidence": 0.9},
            {"slug": "table-extraction", "category": "purpose", "confidence": 0.9},
            {"slug": "", "category": "other", "confidence": 0.9},
        ]
    )
    await tag_artifact(session, art, model=model)
    session.commit()

    slugs = tags_of(session, art.id)
    assert "table-extraction" in slugs
    assert not any(" " in s for s in slugs)


async def test_tag_source_is_recorded(session):
    art = session.scalar(select(Artifact))
    model = FakeModel([{"slug": "brand-colors", "category": "purpose", "confidence": 0.9}])
    await tag_artifact(session, art, model=model)
    session.commit()

    link = session.scalar(select(ArtifactTag).join(Tag).where(Tag.slug == "brand-colors"))
    assert link.source == "ai"
    assert link.origin == "fake"

    link = session.scalar(select(ArtifactTag).join(Tag).where(Tag.slug == "claude"))
    assert link.source == "derived"
    assert link.origin == "rule"
