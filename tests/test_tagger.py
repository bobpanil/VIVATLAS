import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from skill_atlas.models import Artifact, ArtifactTag, Base, Repository, Source, Tag, TagSuppression
from skill_atlas.tagger import (
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
                summary_normal="Собирает бренд-наборы",
                summary_technical="Использует токены",
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


# --- правила ---


def test_derives_tags_from_files_and_type(session):
    art = session.scalar(select(Artifact))
    slugs = {s for s, _c, _conf in derive_tags(art)}
    assert "python" in slugs  # из pyproject.toml и .py
    assert "claude" in slugs  # из типа claude-skill
    assert "skills-lib" in slugs  # владелец


def test_derive_is_deterministic(session):
    art = session.scalar(select(Artifact))
    assert sorted(derive_tags(art)) == sorted(derive_tags(art))


# --- ГЛАВНОЕ: удалённый тег не возвращается ---


async def test_deleted_auto_tag_never_comes_back(session):
    art = session.scalar(select(Artifact))

    await tag_artifact(session, art, model=None)
    session.commit()
    assert "python" in tags_of(session, art.id)

    # Пользователь говорит: это не питон.
    remove_tag(session, art.id, "python", reason="не питон")
    session.commit()
    assert "python" not in tags_of(session, art.id)

    # Пересканировали ещё дважды — тег обязан остаться удалённым.
    await tag_artifact(session, art, model=None)
    session.commit()
    await tag_artifact(session, art, model=None)
    session.commit()

    assert "python" not in tags_of(session, art.id), "удалённый тег вернулся"


async def test_suppression_also_blocks_ai_tags(session):
    # Запрет обязан действовать на теги от модели, а не только на правила.
    art = session.scalar(select(Artifact))
    remove_tag(session, art.id, "pdf", reason="нет тут pdf")
    session.commit()

    model = FakeModel([{"slug": "pdf", "category": "формат", "confidence": 0.99}])
    await tag_artifact(session, art, model=model)
    session.commit()

    assert "pdf" not in tags_of(session, art.id)


def test_suppression_record_is_created(session):
    art = session.scalar(select(Artifact))
    add_manual_tag(session, art.id, "python")
    session.commit()

    remove_tag(session, art.id, "python", reason="ошибка")
    session.commit()

    ban = session.scalar(select(TagSuppression).where(TagSuppression.artifact_id == art.id))
    assert ban is not None
    assert ban.reason == "ошибка"


# --- ручное главнее ---


async def test_manual_tag_is_not_overwritten_by_auto(session):
    art = session.scalar(select(Artifact))
    add_manual_tag(session, art.id, "python", category="язык")
    session.commit()

    await tag_artifact(session, art, model=None)
    session.commit()

    link = session.scalar(
        select(ArtifactTag).join(Tag).where(ArtifactTag.artifact_id == art.id, Tag.slug == "python")
    )
    assert link.source == "manual"  # не перезаписан на derived
    assert link.confidence == 1.0
    assert link.manually_confirmed is True


def test_manual_tag_lifts_suppression(session):
    # Передумал: сначала удалил, потом поставил руками. Запрет должен сняться.
    art = session.scalar(select(Artifact))
    remove_tag(session, art.id, "python")
    session.commit()

    add_manual_tag(session, art.id, "python")
    session.commit()

    assert "python" in tags_of(session, art.id)
    assert (
        session.scalar(select(TagSuppression).where(TagSuppression.artifact_id == art.id)) is None
    )


# --- порог ---


def test_weak_tags_are_not_applied(session):
    art = session.scalar(select(Artifact))
    applied, rejected, weak = apply_tags(
        session,
        art,
        [("uncertain", "прочее", 0.3), ("sure", "прочее", 0.9)],
        source="ai",
        origin="test",
    )
    session.commit()

    assert applied == 1
    assert weak == 1
    assert "sure" in tags_of(session, art.id)
    assert "uncertain" not in tags_of(session, art.id)


# --- теги от модели ---


async def test_ai_tags_with_bad_slugs_are_dropped(session):
    art = session.scalar(select(Artifact))
    model = FakeModel(
        [
            {"slug": "Хороший Тег С Пробелами", "category": "прочее", "confidence": 0.9},
            {"slug": "table-extraction", "category": "назначение", "confidence": 0.9},
            {"slug": "", "category": "прочее", "confidence": 0.9},
        ]
    )
    await tag_artifact(session, art, model=model)
    session.commit()

    slugs = tags_of(session, art.id)
    assert "table-extraction" in slugs
    assert not any(" " in s for s in slugs)


async def test_tag_source_is_recorded(session):
    art = session.scalar(select(Artifact))
    model = FakeModel([{"slug": "brand-colors", "category": "назначение", "confidence": 0.9}])
    await tag_artifact(session, art, model=model)
    session.commit()

    link = session.scalar(select(ArtifactTag).join(Tag).where(Tag.slug == "brand-colors"))
    assert link.source == "ai"
    assert link.origin == "fake"

    link = session.scalar(select(ArtifactTag).join(Tag).where(Tag.slug == "claude"))
    assert link.source == "derived"
    assert link.origin == "правило"
