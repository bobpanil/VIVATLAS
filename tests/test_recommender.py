import pytest
from sqlalchemy import select

from vivatlas.embeddings import to_blob
from vivatlas.models import Artifact, Embedding, Repository, Source
from vivatlas.recommender import NO_MATCH_THRESHOLD, recommend


@pytest.fixture
def session(make_session):
    with make_session() as s:
        source = Source(kind="fake", base_url="https://x", display_name="Fake")
        s.add(source)
        s.flush()
        for i, name in enumerate(["airbnb", "starbucks", "scanner"], 1):
            repo = Repository(
                source_id=source.id,
                external_id=str(i),
                owner="lib",
                name=name,
                default_branch="main",
            )
            s.add(repo)
            s.flush()
            art = Artifact(
                repository_id=repo.id,
                name=name,
                artifact_type="design-kit",
                summary_short=f"{name} kit",
                summary_normal=f"{name} description",
            )
            s.add(art)
            s.flush()
            s.add(
                Embedding(
                    artifact_id=art.id,
                    model="fake-embed",
                    dim=2,
                    vector=to_blob([1.0, 0.0]),
                    source_hash="h",
                )
            )
        s.commit()
        yield s


class FakeEmbed:
    model = "fake-embed"
    dim = 2

    def __init__(self, similarity: float = 0.9) -> None:
        # Card vector is [1,0]. We pick the query so the cosine comes out
        # as needed: [cos, sin].
        import math

        angle = math.acos(max(-1.0, min(1.0, similarity)))
        self.q = [math.cos(angle), math.sin(angle)]

    async def embed(self, text):
        return self.q

    async def aclose(self): ...


class FakeText:
    model = "fake-text"

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    async def generate_json(self, prompt, schema):
        self.calls += 1
        return self.payload

    async def aclose(self): ...


def good_payload(best_id: int) -> dict:
    return {
        "best": {"id": best_id, "why": "a good fit", "limitations": "none"},
        "alternatives": [],
        "rejected": [],
        "chain": [],
        "confidence": 0.9,
        "basis": "documentation",
    }


# --- the threshold decides, not the model ---


async def test_low_similarity_means_no_match_without_asking_the_model(session):
    # We don't even ask the model: if we did, it would pick something.
    text = FakeText(good_payload(1))
    r = await recommend(session, "convert video", FakeEmbed(0.3), text)

    assert r.no_match is True
    assert r.best is None
    assert text.calls == 0, "the model must not be called if the threshold is not passed"
    assert r.suggestions


async def test_high_similarity_asks_the_model(session):
    text = FakeText(good_payload(1))
    r = await recommend(session, "Airbnb design", FakeEmbed(0.9), text)

    assert r.no_match is False
    assert r.best is not None
    assert text.calls == 1


async def test_threshold_boundary(session):
    just_below = FakeEmbed(NO_MATCH_THRESHOLD - 0.02)
    just_above = FakeEmbed(NO_MATCH_THRESHOLD + 0.02)

    assert (await recommend(session, "x", just_below, FakeText(good_payload(1)))).no_match is True
    assert (await recommend(session, "x", just_above, FakeText(good_payload(1)))).no_match is False


# --- protection against invented tools ---


async def test_invented_id_is_dropped(session):
    # The model referenced a tool it was never shown.
    text = FakeText(
        {
            "best": {"id": 999, "why": "an excellent tool", "limitations": "none"},
            "alternatives": [],
            "rejected": [],
            "chain": [],
            "confidence": 0.95,
            "basis": "documentation",
        }
    )
    r = await recommend(session, "design", FakeEmbed(0.9), text)

    assert r.dropped_ids == 1
    assert r.best is None
    assert r.no_match is True, "an invented tool must not become the answer"


async def test_invented_alternative_is_dropped_but_best_survives(session):
    real_id = session.scalar(select(Artifact.id))
    text = FakeText(
        {
            "best": {"id": real_id, "why": "ok", "limitations": "none"},
            "alternatives": [{"id": 777, "why": "made up", "limitations": "none"}],
            "rejected": [],
            "chain": [],
            "confidence": 0.9,
            "basis": "documentation",
        }
    )
    r = await recommend(session, "design", FakeEmbed(0.9), text)

    assert r.best is not None
    assert r.alternatives == []
    assert r.dropped_ids == 1


async def test_best_is_not_duplicated_in_alternatives(session):
    real_id = session.scalar(select(Artifact.id))
    text = FakeText(
        {
            "best": {"id": real_id, "why": "ok", "limitations": "none"},
            "alternatives": [{"id": real_id, "why": "the same one", "limitations": "none"}],
            "rejected": [],
            "chain": [],
            "confidence": 0.9,
            "basis": "documentation",
        }
    )
    r = await recommend(session, "design", FakeEmbed(0.9), text)
    assert r.alternatives == []


async def test_only_two_alternatives_kept(session):
    ids = list(session.scalars(select(Artifact.id)))
    text = FakeText(
        {
            "best": {"id": ids[0], "why": "ok", "limitations": "none"},
            "alternatives": [{"id": i, "why": "x", "limitations": "y"} for i in ids[1:] + ids[1:]],
            "rejected": [],
            "chain": [],
            "confidence": 0.9,
            "basis": "documentation",
        }
    )
    r = await recommend(session, "design", FakeEmbed(0.9), text)
    assert len(r.alternatives) <= 2


async def test_confidence_is_clamped(session):
    real_id = session.scalar(select(Artifact.id))
    payload = good_payload(real_id)
    payload["confidence"] = 5.0
    r = await recommend(session, "design", FakeEmbed(0.9), FakeText(payload))
    assert r.confidence == 1.0


async def test_empty_catalog_is_no_match(session):
    session.query(Embedding).delete()
    session.query(Artifact).delete()
    session.commit()

    r = await recommend(session, "anything", FakeEmbed(0.9), FakeText(good_payload(1)))
    assert r.no_match is True
