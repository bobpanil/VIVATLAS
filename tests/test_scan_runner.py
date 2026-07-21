"""The concurrent scan runner (web._scan_one_source).

The scan was rewritten to build several cards at once: a parallel COMPUTE phase
(download + AI, holding no DB write lock) feeding a short, serialized PERSIST phase.
These tests pin the behaviour that matters — shared cards with embeddings and tags
come out the far end, and the unchanged fast-path still skips the download — against
a file-backed SQLite so the real multi-session, WAL locking path is exercised.
"""

import inspect

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from tests.test_archive import make_tar
from vivatlas import db, web
from vivatlas.migrate import create_fts_table
from vivatlas.models import Artifact, ArtifactTag, Base, Embedding, Source
from vivatlas.providers.base import RepoRef


def _ref(i: int, name: str) -> RepoRef:
    return RepoRef(
        external_id=str(i),
        owner="skills-lib",
        name=name,
        default_branch="main",
        is_private=False,
        is_archived=False,
        is_empty=False,
        html_url=f"https://x/skills-lib/{name}",
        clone_url=f"https://x/skills-lib/{name}.git",
        size_kb=10,
    )


class FakeProvider:
    name = "fake"

    def __init__(self, refs: list[RepoRef], blob: bytes) -> None:
        self.refs = refs
        self.blob = blob
        self.archive_calls = 0

    async def list_repositories(self) -> list[RepoRef]:
        return self.refs

    async def get_head_sha(self, repo: RepoRef) -> str:
        return "sha-" + repo.name

    async def download_archive(self, repo: RepoRef, ref: str) -> bytes:
        self.archive_calls += 1
        return self.blob

    async def blob_shas(self, repo: RepoRef, ref: str) -> dict[str, str]:
        return {}

    async def aclose(self) -> None: ...


class FakeText:
    model = "fake-model"

    async def generate_json(self, prompt: str, schema: dict) -> dict:
        # The tagger and the summarizer share this method; tell them apart by schema.
        if "tags" in (schema.get("properties") or {}):
            return {"tags": [{"slug": "python", "category": "language", "confidence": 0.9}]}
        return {"summary_short": "S", "summary_normal": "N", "summary_technical": "T"}

    async def aclose(self) -> None: ...


class FakeEmbed:
    model = "fake-embed"
    dim = 4

    async def embed(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3, 0.4]

    async def aclose(self) -> None: ...


@pytest.fixture
def scan_env(tmp_path, monkeypatch):
    """A shared source, two repos, and the AI/provider swapped for fakes. The DB is a
    real file (not :memory:) so the runner's separate compute/persist sessions see one
    another's writes and hit the true WAL locking path."""
    engine = create_engine(f"sqlite:///{tmp_path / 'scan.db'}", future=True)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi, _record):  # mirror production db.py
        cur = dbapi.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        create_fts_table(conn)

    Local = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(db, "SessionLocal", Local)

    provider = FakeProvider(
        [_ref(1, "brandkit"), _ref(2, "taste")],
        make_tar({"SKILL.md": b"# Brandkit\nA reusable skill."}),
    )
    monkeypatch.setattr(web, "_provider_for", lambda *a, **k: provider)
    monkeypatch.setattr(web, "build_text_model", lambda: FakeText())
    monkeypatch.setattr(web, "build_embedding_model", lambda: FakeEmbed())

    with Local() as s:
        src = Source(
            kind="fake", base_url="https://x", display_name="Fake",
            owner_user_id=None, token_enc="",
        )
        s.add(src)
        s.commit()
        source_id = src.id
    return source_id, provider, Local


async def test_scan_creates_shared_cards_with_embeddings_and_tags(scan_env):
    source_id, provider, Local = scan_env
    progress = {"total": 0, "done": 0, "added": 0}

    await web._scan_one_source(source_id, progress)

    # Every repo counted once, all landed as new cards.
    assert progress == {"total": 2, "done": 2, "added": 2}
    with Local() as s:
        arts = s.scalars(select(Artifact)).all()
        assert len(arts) == 2
        # A shared source (no owner) yields shared, owner-less cards.
        assert all(a.shared and a.owner_user_id is None for a in arts)
        assert all(a.artifact_type == "skill" for a in arts)
        assert all(a.summary_short == "S" for a in arts)
        assert all(a.is_new and not a.hidden for a in arts)
        # The AI calls actually persisted: one embedding per card, and tags applied.
        assert s.scalar(select(func.count()).select_from(Embedding)) == 2
        assert s.scalar(select(func.count()).select_from(ArtifactTag)) >= 2


async def test_scan_second_run_is_unchanged_and_skips_download(scan_env):
    source_id, provider, Local = scan_env
    await web._scan_one_source(source_id, {"total": 0, "done": 0, "added": 0})
    assert provider.archive_calls == 2

    progress = {"total": 0, "done": 0, "added": 0}
    await web._scan_one_source(source_id, progress)

    # Same commit and the summary is in place: no re-download, no new cards.
    assert provider.archive_calls == 2
    assert progress == {"total": 2, "done": 2, "added": 0}
    with Local() as s:
        assert s.scalar(select(func.count()).select_from(Artifact)) == 2


async def test_scan_failed_summary_still_produces_a_card(scan_env, monkeypatch):
    source_id, provider, Local = scan_env

    class DyingText(FakeText):
        async def generate_json(self, prompt: str, schema: dict) -> dict:
            raise RuntimeError("out of quota")

    monkeypatch.setattr(web, "build_text_model", lambda: DyingText())
    progress = {"total": 0, "done": 0, "added": 0}

    await web._scan_one_source(source_id, progress)

    # The card survives without a summary, and the reason is recorded, not faked.
    assert progress["added"] == 2
    with Local() as s:
        arts = s.scalars(select(Artifact)).all()
        assert len(arts) == 2
        assert all(a.summary_short == "" for a in arts)
        assert all("quota" in (a.summary_error or "") for a in arts)
        assert all(a.artifact_type == "skill" for a in arts)  # type still detected
        # An embedding is still built (from the name), search shouldn't go blind.
        assert s.scalar(select(func.count()).select_from(Embedding)) == 2


def test_scan_launching_endpoints_are_async():
    """launch_user_scan/launch_global_scan schedule the crawl with
    asyncio.create_task, which requires the running event loop. Their endpoints must
    therefore be `async def` — a sync endpoint runs in a threadpool with no loop and
    the scan dies with "no running event loop". This pins both against that regression."""
    from vivatlas.admin_web import admin_scan
    from vivatlas.settings_web import source_scan

    assert inspect.iscoroutinefunction(admin_scan)
    assert inspect.iscoroutinefunction(source_scan)
