"""The browser-extension backend: Bearer-token auth and the capture (add) helper.

We test the pieces directly rather than through the ASGI app: the token round-trip on
auth, and ext_capture against a real file-backed SQLite (so _create_draft's FTS write
and the separate sessions behave as in production).
"""

from types import SimpleNamespace

import pytest
from fastapi import Response
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from vivatlas import auth, db, security
from vivatlas.config import settings
from vivatlas.migrate import create_fts_table
from vivatlas.models import Artifact, Base, User
from vivatlas.web import _is_github_repo_url, ext_capture


class _FakeReq:
    """Enough of a Request for auth: cookies, headers, scheme, client ip."""

    def __init__(self, cookies=None, headers=None):
        self.cookies = cookies or {}
        self.headers = headers or {}
        self.url = SimpleNamespace(scheme="https")
        self.client = SimpleNamespace(host="1.2.3.4")


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://github.com/bobpanil/vivatlas", True),
        ("https://www.github.com/o/r", True),
        ("https://github.com/o/r/tree/main", True),
        ("https://github.com/bobpanil", False),        # a profile, not a repo
        ("https://github.com/settings/keys", False),   # a reserved page
        ("https://example.com/a/b", False),            # not GitHub
        ("nonsense", False),
    ],
)
def test_is_github_repo_url(url, expected):
    assert _is_github_repo_url(url) is expected


def test_bearer_token_authenticates_like_the_cookie(make_session, monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "test-secret-key-long-enough-for-the-door")
    session = make_session()
    user = User(email="b@x.com", display_name="B", password_hash=security.hash_password("pw"))
    session.add(user)
    session.flush()

    token = auth.open_session(session, user, _FakeReq(headers={"user-agent": "ext"}), Response())
    assert token

    # The same token as a Bearer header authenticates.
    by_bearer = auth.current_user(
        session, _FakeReq(headers={"authorization": f"Bearer {token}"})
    )
    assert by_bearer is not None and by_bearer.id == user.id

    # And as the cookie.
    by_cookie = auth.current_user(session, _FakeReq(cookies={auth.COOKIE_NAME: token}))
    assert by_cookie is not None and by_cookie.id == user.id

    # A wrong token is nobody.
    assert auth.current_user(session, _FakeReq(headers={"authorization": "Bearer nope"})) is None
    assert auth.current_user(session, _FakeReq()) is None


@pytest.fixture
def capture_db(tmp_path, monkeypatch):
    """A file-backed DB wired into session_scope, with no Gitea token so a captured
    GitHub URL falls to the draft path (no background import in a test)."""
    engine = create_engine(f"sqlite:///{tmp_path / 'ext.db'}", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        create_fts_table(conn)
    Local = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(db, "SessionLocal", Local)
    monkeypatch.setattr(settings, "gitea_token", "")
    # No AI key: the capture still lands as a real card, just without a summary — and
    # the test makes no network calls.
    monkeypatch.setattr(settings, "google_api_key", "")
    with Local() as s:
        u = User(email="b@x.com", display_name="B", password_hash="h")
        s.add(u)
        s.commit()
        uid = u.id
    return Local, uid


async def _drain_captures():
    """ext_capture fires the processing off as a background task; let it finish."""
    import asyncio

    from vivatlas import web

    pending = [t for t in list(web._SCAN_TASKS) if not t.done()]
    if pending:
        await asyncio.gather(*pending)


async def test_capture_processes_a_page_into_the_library(capture_db):
    Local, uid = capture_db
    res = await ext_capture(
        "https://example.com/cool-tool", "Cool Tool", "the captured page text", uid, shared=False
    )
    # Returns fast; the work happens in the background — not left as a draft.
    assert res["kind"] == "processing"
    await _drain_captures()
    with Local() as s:
        art = s.scalar(select(Artifact).where(Artifact.name == "Cool Tool"))
        assert art is not None
        assert art.artifact_type == "page"  # a real card, not a draft
        assert art.doc_text == "the captured page text"  # the grabbed DOM is kept
        assert art.owner_user_id == uid and art.shared is False
        assert art.is_new is True and art.hidden is False


async def test_capture_respects_the_shared_flag(capture_db):
    Local, uid = capture_db
    res = await ext_capture("https://example.com/x", "X", "", uid, shared=True)
    assert res["kind"] == "processing"
    await _drain_captures()
    with Local() as s:
        art = s.scalar(select(Artifact).where(Artifact.name == "X"))
        assert art is not None and art.shared is True


async def test_capture_github_without_gitea_is_added_not_dropped(capture_db):
    # A repo URL, but nothing to import into — added as a page card rather than lost.
    Local, uid = capture_db
    res = await ext_capture("https://github.com/o/r", "o/r", "", uid, shared=False)
    assert res["kind"] == "processing"
    await _drain_captures()
    with Local() as s:
        art = s.scalar(select(Artifact).where(Artifact.name == "o/r"))
        assert art is not None and art.artifact_type == "page"


async def test_reprocess_draft_without_ai_keeps_it_a_draft_with_a_reason(capture_db):
    # "Rescan with AI" on a draft, but no AI key: it must NOT silently graduate — it stays
    # a draft, with the reason recorded so the person can see why.
    from vivatlas import web

    Local, uid = capture_db
    with Local() as s:
        aid = web._create_draft(s, uid, "https://example.com/x", "X", "", "captured text")
        s.commit()

    assert await web.reprocess_draft(aid) is True
    with Local() as s:
        art = s.get(Artifact, aid)
        assert art.artifact_type == "draft"
        assert art.summary_error


async def test_reprocess_draft_with_ai_promotes_to_a_page(capture_db, monkeypatch):
    # With a working AI the draft graduates to a real page card carrying the summary.
    from vivatlas import web

    class FakeModel:
        model = "fake-model"

        async def aclose(self): ...

    async def fake_summarize(model, **kw):
        return {"summary_short": "s", "summary_normal": "n", "summary_technical": "t"}

    async def fake_tag(session, art, model): ...

    def no_embed():
        raise RuntimeError("no embedding key in the test")

    monkeypatch.setattr(web, "build_text_model", lambda: FakeModel())
    monkeypatch.setattr(web, "build_embedding_model", no_embed)
    monkeypatch.setattr(web, "summarize", fake_summarize)
    monkeypatch.setattr(web, "tag_artifact", fake_tag)

    Local, uid = capture_db
    with Local() as s:
        aid = web._create_draft(s, uid, "https://example.com/y", "Y", "", "some captured text")
        s.commit()

    assert await web.reprocess_draft(aid) is True
    with Local() as s:
        art = s.get(Artifact, aid)
        assert art.artifact_type == "page"
        assert art.summary_short == "s" and art.summary_error is None
        assert art.is_new is True
