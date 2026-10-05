"""The capture queue: a save is written down before the answer goes back, one worker
turns saves into cards in order, and a busy database or a restart loses none of them.

Saves used to be fire-and-forget tasks that held the database locked while they
waited on the AI. Sent close together, they failed with "database is locked" after the
caller had been told "processing". These tests hold the lock the way that code did,
from a coroutine on the same event loop, and check that every save still lands.
"""

import asyncio
import contextlib
import json
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import Response
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from vivatlas import auth, captures, cardtext, db, ext_api, mcp_server, web
from vivatlas.config import settings
from vivatlas.migrate import create_fts_table
from vivatlas.models import (
    Artifact,
    ArtifactCategory,
    ArtifactTag,
    Base,
    CaptureJob,
    Category,
    Embedding,
    Tag,
    User,
)


def _no_model():
    raise RuntimeError("no AI in this test")


async def _no_meta(url, timeout=30.0):
    return {}


@pytest.fixture
def queue_db(tmp_path, monkeypatch):
    """A file-backed WAL database like production, wired into session_scope, with no
    AI and no network. Lock waits are short, so a held lock shows within the test."""
    path = tmp_path / "queue.db"
    engine = create_engine(f"sqlite:///{path}", future=True, connect_args={"timeout": 0.2})
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        create_fts_table(conn)
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    Local = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(db, "SessionLocal", Local)
    monkeypatch.setattr(settings, "gitea_token", "")
    monkeypatch.setattr(web, "build_text_model", _no_model)
    monkeypatch.setattr(web, "build_embedding_model", _no_model)
    monkeypatch.setattr(web, "fetch_page_meta", _no_meta)
    with Local() as s:
        user = User(email="b@x.com", display_name="Boris", password_hash="h")
        other = User(email="m@x.com", display_name="Mia", password_hash="h")
        s.add_all([user, other])
        s.commit()
        ids = (user.id, other.id)
    yield Local, ids, str(path)
    engine.dispose()


async def hold_write_lock(path: str, seconds: float) -> None:
    """A writer that keeps the database locked while it awaits something, the way the
    capture and scan code did while it waited on the AI."""
    conn = sqlite3.connect(path, timeout=0.2, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        await asyncio.sleep(seconds)
        conn.execute("COMMIT")
    finally:
        conn.close()


def can_write(path: str) -> bool:
    conn = sqlite3.connect(path, timeout=0.1, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("COMMIT")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def job(Local, job_id) -> CaptureJob:
    with Local() as s:
        return s.get(CaptureJob, job_id)


# --- saving ------------------------------------------------------------------


async def test_saves_sent_together_all_land_while_the_database_is_held(queue_db):
    Local, (uid, _), path = queue_db
    holder = asyncio.create_task(hold_write_lock(path, 1.5))
    await asyncio.sleep(0.05)  # the holder has the lock now
    results = await asyncio.gather(
        *(
            web.ext_capture(f"https://example.com/reel/{i}", f"Reel {i}", "", uid, shared=False)
            for i in range(25)
        )
    )
    await holder
    assert len({r["job_id"] for r in results}) == 25
    assert all(r["kind"] == "processing" for r in results)

    assert await captures.drain() == 25
    with Local() as s:
        names = set(s.scalars(select(Artifact.name)))
        assert {f"Reel {i}" for i in range(25)} <= names
        done = s.scalar(
            select(func.count()).select_from(CaptureJob).where(CaptureJob.status == "done")
        )
        assert done == 25


async def test_cards_are_made_in_the_order_the_links_came(queue_db):
    Local, (uid, _), _path = queue_db
    for i in range(3):
        await web.ext_capture(f"https://example.com/{i}", f"Link {i}", "", uid, shared=False)
    await captures.drain()
    with Local() as s:
        jobs = s.scalars(select(CaptureJob).order_by(CaptureJob.id)).all()
        assert [j.artifact_id for j in jobs] == sorted(j.artifact_id for j in jobs)
        assert [s.get(Artifact, j.artifact_id).name for j in jobs] == [
            "Link 0",
            "Link 1",
            "Link 2",
        ]


async def test_a_save_that_cannot_be_written_is_refused_out_loud(queue_db):
    Local, (uid, _), path = queue_db
    holder = asyncio.create_task(hold_write_lock(path, 1.5))
    await asyncio.sleep(0.05)
    with pytest.raises(captures.QueueBusy):
        await web.ext_capture("https://example.com/a", "A", "", uid, False, patience=0.3)
    await holder
    with Local() as s:
        assert s.scalar(select(func.count()).select_from(CaptureJob)) == 0


# --- processing --------------------------------------------------------------


async def test_the_ai_is_asked_with_the_database_free(queue_db, monkeypatch):
    """The root of the lost saves: the AI used to be asked inside a write. Every AI
    call here checks that another writer can get in while it runs."""
    Local, (uid, _), path = queue_db
    seen: list[tuple[str, bool]] = []

    class FakeText:
        model = "fake-text"

        async def generate_json(self, prompt, schema):
            seen.append(("tags", can_write(path)))
            return {"tags": [{"slug": "video-editing", "category": "purpose", "confidence": 0.9}]}

        async def aclose(self): ...

    class FakeEmbed:
        model = "fake-embed"
        dim = 3

        async def embed(self, text):
            seen.append(("embed", can_write(path)))
            return [0.1, 0.2, 0.3]

        async def aclose(self): ...

    async def fake_summarize(model, **kw):
        seen.append(("summary", can_write(path)))
        return {"summary_short": "short", "summary_normal": "normal", "summary_technical": "tech"}

    async def fake_translate(model, card):
        seen.append(("translate", can_write(path)))
        card.translations_json = json.dumps({"ru": {"summary_short": "кратко"}})

    monkeypatch.setattr(web, "build_text_model", lambda: FakeText())
    monkeypatch.setattr(web, "build_embedding_model", lambda: FakeEmbed())
    monkeypatch.setattr(web, "summarize", fake_summarize)
    monkeypatch.setattr(cardtext, "fill_translations", fake_translate)

    r = await web.ext_capture(
        "https://example.com/tool", "Tool", "a page about a tool " * 20, uid, shared=False
    )
    await captures.drain()

    assert {name for name, _ in seen} == {"summary", "translate", "embed", "tags"}
    assert all(ok for _, ok in seen), seen
    with Local() as s:
        art = s.get(Artifact, job(Local, r["job_id"]).artifact_id)
        assert art.summary_short == "short" and art.summary_model == "fake-text"
        assert json.loads(art.translations_json)["ru"]["summary_short"] == "кратко"
        assert s.scalar(select(Embedding).where(Embedding.artifact_id == art.id)).dim == 3
        slugs = set(
            s.scalars(
                select(Tag.slug).join(ArtifactTag).where(ArtifactTag.artifact_id == art.id)
            )
        )
        assert "video-editing" in slugs


async def test_a_failing_capture_is_tried_again_later_then_marked_failed(queue_db, monkeypatch):
    Local, (uid, _), _path = queue_db

    async def boom(job):
        raise RuntimeError("the page's server is down")

    monkeypatch.setattr(web, "run_capture", boom)
    r = await web.ext_capture("https://example.com/down", "Down", "", uid, False)
    assert await captures.drain() == 1  # one try now; the next one is later
    j = job(Local, r["job_id"])
    assert j.status == "pending" and j.attempts == 1
    assert "server is down" in j.error
    assert captures._aware(j.next_try_at) > datetime.now(UTC)

    with Local() as s:  # make its last try due now
        j = s.get(CaptureJob, r["job_id"])
        j.attempts = captures.MAX_ATTEMPTS - 1
        j.next_try_at = None
        s.commit()
    assert await captures.drain() == 1
    j = job(Local, r["job_id"])
    assert j.status == "failed" and j.attempts == captures.MAX_ATTEMPTS
    # The link and its text are kept, for whoever looks.
    assert j.url == "https://example.com/down" and j.title == "Down"


async def test_captures_left_running_by_a_stopped_server_are_finished_after_a_restart(queue_db):
    Local, (uid, _), _path = queue_db
    with Local() as s:
        s.add_all(
            [
                CaptureJob(user_id=uid, url="https://example.com/half", title="Half done",
                           status="running", attempts=1),
                CaptureJob(user_id=uid, url="https://example.com/cursed", title="Cursed",
                           status="running", attempts=captures.MAX_ATTEMPTS),
                CaptureJob(user_id=uid, url="https://example.com/waiting", title="Waiting",
                           status="pending"),
            ]
        )
        s.commit()
    assert captures.recover_interrupted() == 2
    assert await captures.drain() == 2
    with Local() as s:
        jobs = {j.title: j for j in s.scalars(select(CaptureJob))}
        assert jobs["Half done"].status == "done" and jobs["Waiting"].status == "done"
        assert jobs["Cursed"].status == "failed" and "stopped" in jobs["Cursed"].error
        names = set(s.scalars(select(Artifact.name)))
        assert {"Half done", "Waiting"} <= names and "Cursed" not in names


async def test_stopping_in_the_middle_of_a_capture_puts_it_back_in_line(queue_db, monkeypatch):
    Local, (uid, _), _path = queue_db
    started = asyncio.Event()

    async def slow(job):
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(web, "run_capture", slow)
    r = await web.ext_capture("https://example.com/slow", "Slow", "", uid, False)
    task = asyncio.create_task(captures.process_next())
    await started.wait()
    assert job(Local, r["job_id"]).status == "running"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    j = job(Local, r["job_id"])
    assert j.status == "pending" and j.attempts == 0


async def test_the_worker_picks_up_a_save_by_itself(queue_db):
    Local, (uid, _), _path = queue_db
    worker = asyncio.create_task(captures.worker_loop())
    try:
        await asyncio.sleep(0.2)  # started, looked, found nothing, waiting
        r = await web.ext_capture("https://example.com/auto", "Auto", "", uid, False)
        for _ in range(100):
            if job(Local, r["job_id"]).status == "done":
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("the worker never got to the save")
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
    assert captures._wake is None


async def test_adding_a_link_again_keeps_one_card_adds_the_note_and_files_it_once(queue_db):
    Local, (uid, _), _path = queue_db
    with Local() as s:
        s.add(Category(name="Video editing", owner_user_id=uid))
        s.commit()
    url = "https://example.com/video-editor"
    first = await web.ext_capture(url, "Video editor", "", uid, shared=False)
    await captures.drain()
    transcript = "He says: write CUT in the comments and I'll send you the editor. " * 4
    second = await web.ext_capture(
        url, "Video editor", transcript, uid, shared=False, text_kind="note", via="mcp"
    )
    await captures.drain()

    a, b = job(Local, first["job_id"]), job(Local, second["job_id"])
    assert a.status == b.status == "done", (a.error, b.error)
    assert a.artifact_id == b.artifact_id
    with Local() as s:
        art = s.get(Artifact, a.artifact_id)
        assert "write CUT in the comments" in art.doc_text
        filed = s.scalar(
            select(func.count())
            .select_from(ArtifactCategory)
            .where(ArtifactCategory.artifact_id == art.id)
        )
        assert filed == 1


# --- the MCP ---------------------------------------------------------------------


class _Tok:
    def __init__(self, subject):
        self.subject = subject
        self.client_id = "cli-1"


def as_user(monkeypatch, uid):
    token = None if uid is None else _Tok(str(uid))
    monkeypatch.setattr(mcp_server, "get_access_token", lambda: token)


async def call(name, args):
    result = await mcp_server.mcp.call_tool(name, args)
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


async def test_an_assistant_can_send_a_transcript_with_a_link(queue_db, monkeypatch):
    """A reel's caption says "comment X"; what X is lives in the audio. The assistant
    transcribes it and sends the transcript along: the AI reads both."""
    Local, (uid, _), _path = queue_db

    async def meta(url, timeout=30.0):
        return {
            "title": "12K views · 300 reactions | Comment STACK and I'll send you the tool",
            "description": "Comment STACK and I'll send you the tool",
            "url": url,
        }

    read: list[str] = []

    async def fake_summarize(model, **kw):
        read.append(kw["doc_text"])
        return {"summary_short": "s", "summary_normal": "n", "summary_technical": "t"}

    class FakeText:
        model = "fake-text"

        async def generate_json(self, prompt, schema):
            return {"tags": []}

        async def aclose(self): ...

    monkeypatch.setattr(web, "fetch_page_meta", meta)
    monkeypatch.setattr(web, "build_text_model", lambda: FakeText())
    monkeypatch.setattr(web, "summarize", fake_summarize)
    as_user(monkeypatch, uid)

    transcript = "In this video I show Remotion, a React library for making videos. " * 4
    d = await call(
        "add_to_library", {"url": "https://www.facebook.com/share/r/abc/", "text": transcript}
    )
    assert d["status"] == "queued" and d["job_id"] and "text_truncated_to" not in d
    await captures.drain()

    with Local() as s:
        art = s.get(Artifact, job(Local, d["job_id"]).artifact_id)
        assert art.name == "Comment STACK and I'll send you the tool"
        assert "Comment STACK" in art.doc_text and "Remotion" in art.doc_text
        assert "Sent with the link:" in art.doc_text
        assert art.shared is False and art.owner_user_id == uid
    assert "Comment STACK" in read[0] and "Remotion" in read[0]


async def test_a_long_text_is_cut_and_the_answer_says_so(queue_db, monkeypatch):
    Local, (uid, _), _path = queue_db
    as_user(monkeypatch, uid)
    d = await call("add_to_library", {"url": "https://example.com/long", "text": "x" * 25_000})
    assert d["text_truncated_to"] == captures.TEXT_MAX
    assert len(job(Local, d["job_id"]).text) == captures.TEXT_MAX


async def test_adding_needs_a_signed_in_caller_and_a_link(queue_db, monkeypatch):
    as_user(monkeypatch, None)
    with pytest.raises(Exception, match="Sign in first"):  # noqa: B017 - FastMCP wraps it
        await call("add_to_library", {"url": "https://example.com/x"})
    _Local, (uid, _), _path = queue_db
    as_user(monkeypatch, uid)
    assert "url is required" in (await call("add_to_library", {"url": "  "}))["error"]


async def test_a_busy_library_tells_the_assistant_nothing_was_saved(queue_db, monkeypatch):
    _Local, (uid, _), _path = queue_db
    as_user(monkeypatch, uid)

    async def busy(**kw):
        raise captures.QueueBusy(captures.BUSY_MESSAGE)

    monkeypatch.setattr(captures, "enqueue", busy)
    d = await call("add_to_library", {"url": "https://example.com/x"})
    assert d["saved"] is False and "nothing was saved" in d["error"]


async def test_list_captures_shows_your_own_saves_and_how_far_they_got(queue_db, monkeypatch):
    Local, (uid, other), _path = queue_db
    as_user(monkeypatch, uid)
    d = await call("add_to_library", {"url": "https://example.com/mine", "title": "Mine"})
    await web.ext_capture("https://example.com/hers", "Hers", "", other, False)

    listed = await call("list_captures", {})
    assert listed["counts"]["pending"] == 1
    assert [i["url"] for i in listed["items"]] == ["https://example.com/mine"]

    await captures.drain()
    listed = await call("list_captures", {"status": "done"})
    assert listed["counts"]["done"] == 1 and listed["counts"]["pending"] == 0
    item = listed["items"][0]
    assert item["job_id"] == d["job_id"] and item["card_id"] and item["via"] == "mcp"
    assert "error" in (await call("list_captures", {"status": "lost"}))


# --- the extension and the phone ---------------------------------------------------


def test_the_phone_and_the_extension_hear_when_a_save_was_refused(queue_db, monkeypatch):
    from fastapi.testclient import TestClient

    from vivatlas.api import app

    Local, (uid, _), _path = queue_db
    monkeypatch.setattr(settings, "secret_key", "test-secret-key-long-enough-for-the-door")
    with Local() as s:
        req = SimpleNamespace(
            cookies={},
            headers={"user-agent": "test"},
            url=SimpleNamespace(scheme="https"),
            client=SimpleNamespace(host="1.2.3.4"),
        )
        token = auth.open_session(s, s.get(User, uid), req, Response())
        s.commit()

    async def busy(*a, **kw):
        raise captures.QueueBusy(captures.BUSY_MESSAGE)

    monkeypatch.setattr(ext_api, "ext_capture", busy)
    client = TestClient(app)
    r = client.post(
        "/api/ext/add",
        json={"url": "https://example.com/x"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 503
    assert r.json()["ok"] is False and "not saved" in r.json()["error"]
