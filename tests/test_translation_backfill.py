"""Saying the older cards again in the other two languages.

Cards described before the catalogue spoke three languages hold text in one, so they
read the same whatever the interface is set to. The backfill walks them once — and has
to be safe to run twice, since a long pass over a big catalogue will get interrupted.
"""

import asyncio

import pytest

from vivatlas import web
from vivatlas.models import Artifact, Repository, Source


class _Model:
    model = "fake"

    def __init__(self, fail: bool = False):
        self.calls = 0
        self.fail = fail

    async def generate_json(self, prompt, schema):
        self.calls += 1
        if self.fail:
            raise RuntimeError("no quota")
        return {
            lang: {
                "name": f"name-{lang}",
                "summary_short": f"short-{lang}",
                "summary_normal": f"normal-{lang}",
                "summary_technical": f"tech-{lang}",
            }
            for lang in ("en", "ru", "he")
        }

    async def aclose(self):
        return None


@pytest.fixture
def catalogue(make_session, monkeypatch):
    """A few cards with text and no translations, plus the plumbing the task needs."""
    session = make_session()
    src = Source(kind="fake", base_url="https://x", display_name="Fake")
    session.add(src)
    session.flush()
    for i in range(3):
        repo = Repository(
            source_id=src.id, external_id=f"e{i}", owner="acme", name=f"tool{i}",
            default_branch="main", html_url=f"https://git.example.com/acme/tool{i}",
        )
        session.add(repo)
        session.flush()
        session.add(
            Artifact(
                repository_id=repo.id, name=f"tool{i}", artifact_type="skill",
                summary_short="does a thing", shared=True,
            )
        )
    session.commit()

    from contextlib import contextmanager

    @contextmanager
    def scope():
        yield session

    monkeypatch.setattr(web, "session_scope", scope)
    monkeypatch.setattr(web.settings, "llm_delay_seconds", 0)
    # Start from a clean slate — the progress dict is module-wide.
    web._TRANSLATE.update(state="idle", total=0, done=0, written=0, error="")
    return session


async def _run_to_completion():
    web.launch_translation_backfill()
    for _ in range(400):
        await asyncio.sleep(0.01)
        if web.translate_progress()["state"] != "running":
            return web.translate_progress()
    raise AssertionError("backfill never finished")


async def test_every_untranslated_card_gets_all_three_languages(catalogue, monkeypatch):
    session = catalogue
    model = _Model()
    monkeypatch.setattr(web, "build_text_model", lambda: model)

    progress = await _run_to_completion()

    assert progress["state"] == "done"
    assert progress["total"] == 3
    assert progress["written"] == 3
    assert model.calls == 3  # one per card, no re-asking
    for art in session.query(Artifact).all():
        assert '"he"' in art.translations_json


async def test_running_it_again_costs_nothing(catalogue, monkeypatch):
    """A long pass will be interrupted, so it has to be safe to just start it again."""
    model = _Model()
    monkeypatch.setattr(web, "build_text_model", lambda: model)
    await _run_to_completion()
    assert model.calls == 3

    web._TRANSLATE.update(state="idle")
    progress = await _run_to_completion()
    assert progress["total"] == 0
    assert model.calls == 3  # nothing left to ask about


async def test_a_refusal_leaves_the_cards_alone(catalogue, monkeypatch):
    """The catalogue must survive a model that won't answer — untranslated, not damaged."""
    session = catalogue
    monkeypatch.setattr(web, "build_text_model", lambda: _Model(fail=True))

    progress = await _run_to_completion()

    assert progress["state"] == "done"
    assert progress["done"] == 3      # it walked them all
    assert progress["written"] == 0   # and wrote nothing
    for art in session.query(Artifact).all():
        assert art.translations_json == ""
        assert art.name.startswith("tool")   # text untouched


async def test_a_second_start_while_running_does_not_double_up(catalogue, monkeypatch):
    monkeypatch.setattr(web, "build_text_model", lambda: _Model())
    web._TRANSLATE.update(state="running")
    # Already running: the call is a no-op that just reports where it's got to.
    assert web.launch_translation_backfill()["state"] == "running"
    web._TRANSLATE.update(state="idle")
