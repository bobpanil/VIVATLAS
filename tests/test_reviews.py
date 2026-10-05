"""Reviews on cards: the MCP set_review tool and the card page's Review block.

A review is usually written by an AI agent that has just read untrusted pages, so the
server checks it the same way for everyone (a fixed list of verdicts, a capped plain
note, a few short project names), stamps the author itself, keeps it out of the card's
own fields, and the page shows it escaped.
"""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import Response
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from vivatlas import auth, db, mcp_server
from vivatlas.config import settings
from vivatlas.migrate import create_fts_table
from vivatlas.models import (
    Artifact,
    ArtifactReview,
    Base,
    OAuthClient,
    Repository,
    Source,
    User,
)


class _Tok:
    def __init__(self, subject, client_id="cli-1"):
        self.subject = subject
        self.client_id = client_id


@pytest.fixture
def world(make_session, monkeypatch):
    """An owner (admin), a plain member, a shared card, the owner's private card and a
    member's private card, plus a registered OAuth client with a hostile-looking name."""
    session = make_session()
    owner = User(id=1, email="o@x.com", display_name="Boris", password_hash="h", is_owner=True)
    member = User(id=2, email="m@x.com", display_name="Mia", password_hash="h")
    session.add_all([owner, member])
    src = Source(kind="fake", base_url="https://x", display_name="Fake")
    session.add(src)
    session.flush()
    cards = {}
    for key, owner_id, shared in (("shared", None, True), ("boris", 1, False), ("mia", 2, False)):
        repo = Repository(source_id=src.id, external_id=key, owner="o", name=key,
                          default_branch="main", html_url=f"https://git.example.com/o/{key}")
        session.add(repo)
        session.flush()
        a = Artifact(repository_id=repo.id, name=key, artifact_type="skill", confidence=0.9,
                     summary_short=f"{key} card", owner_user_id=owner_id, shared=shared)
        session.add(a)
        session.flush()
        cards[key] = a
    session.add(OAuthClient(client_id="cli-1", info_json=json.dumps(
        {"client_name": "Claude <b>Code</b>\x07"})))
    session.commit()

    @contextmanager
    def scope():
        yield session

    monkeypatch.setattr(mcp_server, "session_scope", scope)
    return session, cards


async def call(name, args):
    result = await mcp_server.mcp.call_tool(name, args)
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


def as_user(monkeypatch, uid):
    token = None if uid is None else _Tok(str(uid))
    monkeypatch.setattr(mcp_server, "get_access_token", lambda: token)


async def test_review_needs_a_signed_in_caller(world, monkeypatch):
    _s, cards = world
    as_user(monkeypatch, None)
    with pytest.raises(Exception, match="Sign in first"):  # noqa: B017 - FastMCP wraps the error type
        await call("set_review", {"artifact_id": cards["shared"].id, "verdict": "candidate"})


async def test_owner_reviews_a_shared_card_and_a_second_replaces_the_first(world, monkeypatch):
    session, cards = world
    as_user(monkeypatch, 1)
    aid = cards["shared"].id
    d = await call("set_review", {"artifact_id": aid, "verdict": "Candidate",
                                  "note": "Fits ChronoTetris.\nTry the CLI.",
                                  "projects": ["ChronoTetris", "chronotetris", " Maestro "]})
    assert d["ok"] and d["verdict"] == "candidate" and d["by"] == "Boris"
    assert d["projects"] == ["ChronoTetris", "Maestro"]
    # The client's chosen name is kept as plain text: control characters gone, markup inert.
    assert d["via"] == "Claude <b>Code</b>"
    d2 = await call("set_review", {"artifact_id": aid, "verdict": "park"})
    assert d2["verdict"] == "park" and d2["note"] == ""
    n = session.scalar(select(func.count()).select_from(ArtifactReview).where(
        ArtifactReview.artifact_id == aid))
    assert n == 1
    # The card's own text is untouched by a review.
    assert session.get(Artifact, aid).summary_short == "shared card"


async def test_review_shows_in_card_details_and_as_my_verdict_in_lists(world, monkeypatch):
    _s, cards = world
    as_user(monkeypatch, 1)
    aid = cards["shared"].id
    await call("set_review", {"artifact_id": aid, "verdict": "skip", "note": "dup"})
    card = await call("get_artifact", {"artifact_id": aid})
    assert card["reviews"][0]["verdict"] == "skip" and card["reviews"][0]["note"] == "dup"
    listed = await call("list_artifacts", {"limit": 100})
    mine = {i["id"]: i["my_review"] for i in listed["items"]}
    assert mine[aid] == "skip" and mine[cards["boris"].id] is None


@pytest.mark.parametrize(
    "args, needle",
    [
        ({"verdict": "great"}, "verdict must be one of"),
        ({"verdict": "candidate", "note": "x" * 2001}, "limit is 2000"),
        ({"verdict": "candidate", "projects": [f"p{i}" for i in range(11)]}, "at most 10"),
        ({"verdict": "candidate", "projects": ["p" * 61]}, "longer than 60"),
    ],
)
async def test_bad_reviews_are_refused_with_a_reason(world, monkeypatch, args, needle):
    session, cards = world
    as_user(monkeypatch, 1)
    d = await call("set_review", {"artifact_id": cards["shared"].id, **args})
    assert needle in d["error"]
    assert session.scalar(select(func.count()).select_from(ArtifactReview)) == 0


async def test_who_may_review_what(world, monkeypatch):
    _s, cards = world
    as_user(monkeypatch, 2)  # a plain member
    d = await call("set_review", {"artifact_id": cards["shared"].id, "verdict": "candidate"})
    assert d["error"] == "not allowed to review this card"
    # Someone else's private card is answered as if it didn't exist.
    d = await call("set_review", {"artifact_id": cards["boris"].id, "verdict": "candidate"})
    assert "not found" in d["error"]
    # One's own private card is fine.
    d = await call("set_review", {"artifact_id": cards["mia"].id, "verdict": "vague"})
    assert d["ok"]


# --- the card page ----------------------------------------------------------


@pytest.fixture
def site(tmp_path, monkeypatch):
    """The whole app on a file-backed database, with a signed-in owner and a card whose
    review holds markup-looking text."""
    from fastapi.testclient import TestClient

    from vivatlas.api import app

    engine = create_engine(f"sqlite:///{tmp_path / 'web.db'}", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        create_fts_table(conn)
    Local = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(db, "SessionLocal", Local)
    monkeypatch.setattr(settings, "secret_key", "test-secret-key-long-enough-for-the-door")
    with Local() as s:
        owner = User(email="o@x.com", display_name="Boris", password_hash="h", is_owner=True)
        s.add(owner)
        src = Source(kind="fake", base_url="https://x", display_name="Fake")
        s.add(src)
        s.flush()
        repo = Repository(source_id=src.id, external_id="1", owner="o", name="tool",
                          default_branch="main", html_url="https://git.example.com/o/tool")
        s.add(repo)
        s.flush()
        art = Artifact(repository_id=repo.id, name="tool", artifact_type="skill",
                       confidence=0.9, summary_short="a tool", shared=True)
        s.add(art)
        s.flush()
        review = ArtifactReview(
            artifact_id=art.id,
            author_user_id=owner.id,
            verdict="candidate",
            note='<script>alert(1)</script>\n<a href="https://evil.example">x</a>',
            projects_json=json.dumps(["<img src=x onerror=alert(2)>"]),
            via="<b>bot</b>",
        )
        s.add(review)
        req = SimpleNamespace(cookies={}, headers={"user-agent": "test"},
                              url=SimpleNamespace(scheme="https"),
                              client=SimpleNamespace(host="1.2.3.4"))
        token = auth.open_session(s, owner, req, Response())
        s.commit()
        ids = (art.id, review.id)
    client = TestClient(app)
    client.cookies.set(auth.COOKIE_NAME, token)
    return client, Local, ids


def test_card_page_shows_the_review_as_plain_text(site):
    client, _Local, (aid, _rid) = site
    r = client.get(f"/a/{aid}")
    assert r.status_code == 200, r.status_code
    html = r.text
    assert "art-reviews" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert 'href="https://evil.example"' not in html
    assert "<img src=x" not in html and "<b>bot</b>" not in html
    assert "review-candidate" in html


def test_owner_can_remove_a_review(site):
    client, Local, (aid, rid) = site
    r = client.post(f"/artifact/{aid}/review/{rid}/delete", follow_redirects=False)
    assert r.status_code == 303, r.status_code
    assert r.headers["location"] == f"/a/{aid}"  # back to the card page
    with Local() as s:
        assert s.get(ArtifactReview, rid) is None


def test_removing_a_card_removes_its_reviews(site):
    client, Local, (aid, rid) = site
    r = client.post(f"/artifact/{aid}/delete", follow_redirects=False)
    assert r.status_code == 303, r.status_code
    with Local() as s:
        assert s.get(Artifact, aid) is None and s.get(ArtifactReview, rid) is None
