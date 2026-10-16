"""Demo mode (demo.py): throwaway visitor accounts that can't share, can't grow
past their cap, can't lock the next person out, and go away on time."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import Response
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from vivatlas import auth, captures, db, demo, runtime_settings, security
from vivatlas.config import settings
from vivatlas.migrate import create_fts_table
from vivatlas.models import Artifact, Base, CaptureJob, Repository, Source, User


@pytest.fixture
def local(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'demo.db'}", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        create_fts_table(conn)
    Local = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(db, "SessionLocal", Local)
    monkeypatch.setattr(settings, "secret_key", "test-secret-key-long-enough-for-the-door")
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "demo_reviewer_email", "demo")
    monkeypatch.setattr(settings, "demo_reviewer_password", "demo")
    monkeypatch.setattr(settings, "demo_max_saves", 3)
    monkeypatch.setattr(settings, "demo_max_visitors", 5)
    monkeypatch.setattr(settings, "demo_starts_per_hour", 5)
    demo._starts.clear()
    with Local() as s:
        demo.ensure_accounts(s)
        s.commit()
    return Local


@pytest.fixture
def client(local):
    from fastapi.testclient import TestClient

    from vivatlas.api import app

    return TestClient(app, follow_redirects=False)


def _art(s, name, owner_id, *, shared=False, created_at=None):
    src = s.scalar(select(Source))
    if src is None:
        src = Source(kind="f", base_url="https://x", display_name="F")
        s.add(src)
        s.flush()
    repo = Repository(
        source_id=src.id, external_id=name, owner="o", name=name, default_branch="main"
    )
    s.add(repo)
    s.flush()
    art = Artifact(
        repository_id=repo.id,
        name=name,
        artifact_type="skill",
        owner_user_id=owner_id,
        shared=shared,
    )
    if created_at is not None:
        art.created_at = created_at
    s.add(art)
    s.flush()
    return art


def _start(client):
    r = client.post("/demo/start")
    assert r.status_code == 303 and r.headers["location"] == "/"
    client.cookies.set(auth.COOKIE_NAME, r.cookies[auth.COOKIE_NAME])
    return r


def _visitor(local):
    with local() as s:
        return s.scalar(select(User).where(User.email.like("visitor-%")))


# --- accounts ----------------------------------------------------------------------


def test_startup_makes_the_owner_and_the_reviewer_once(local):
    with local() as s:
        assert demo.ensure_accounts(s) == []  # second run: nothing to do
        owner = s.scalar(select(User).where(User.is_owner.is_(True)))
        reviewer = s.scalar(select(User).where(User.email == "demo"))
        assert owner.email == "owner@demo.invalid"
        assert reviewer is not None and not reviewer.is_owner and not reviewer.is_admin
        assert security.verify_password("demo", reviewer.password_hash)
        # The owner's password is random: "demo" and empty don't open it.
        assert not security.verify_password("demo", owner.password_hash)


def test_a_changed_reviewer_password_is_put_back(local):
    with local() as s:
        reviewer = s.scalar(select(User).where(User.email == "demo"))
        reviewer.password_hash = security.hash_password("something else")
        s.flush()
        assert demo.ensure_accounts(s) == ["reset the reviewers' password for demo"]
        assert security.verify_password("demo", reviewer.password_hash)


def test_registration_is_closed_in_demo_mode(local, monkeypatch):
    with local() as s:
        assert runtime_settings.registration_open(s) is False
        monkeypatch.setattr(settings, "demo_mode", False)
        assert runtime_settings.registration_open(s) is True


def test_the_reviewer_signs_in_with_a_plain_name(client):
    r = client.post("/login", data={"email": "demo", "password": "demo"})
    assert r.status_code == 303


# --- trying it ---------------------------------------------------------------------


def test_login_page_offers_try_it_and_takes_a_plain_name(client):
    r = client.get("/login")
    assert 'action="/demo/start"' in r.text
    assert '<input type="text" name="email"' in r.text


def test_try_it_signs_a_visitor_in_with_a_private_account(client, local):
    _start(client)
    v = _visitor(local)
    assert demo.is_visitor(v.email) and v.email.endswith("@demo.invalid")
    assert not v.is_owner and not v.is_admin
    page = client.get("/")
    assert page.status_code == 200
    assert 'class="demo-banner"' in page.text


def test_try_it_is_off_without_demo_mode(client, local, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    r = client.post("/demo/start")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert _visitor(local) is None


def test_one_address_starts_only_so_many(client, monkeypatch):
    monkeypatch.setattr(settings, "demo_starts_per_hour", 2)
    for _ in range(2):
        assert client.post("/demo/start").status_code == 303
        client.cookies.clear()  # a fresh browser each time, same address
    assert client.post("/demo/start").status_code == 429


def test_no_more_visitors_than_the_cap(client, local, monkeypatch):
    monkeypatch.setattr(settings, "demo_max_visitors", 1)
    assert client.post("/demo/start").status_code == 303
    client.cookies.clear()
    assert client.post("/demo/start").status_code == 503


# --- what a visitor may not do -----------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/settings/account/password",
        "/settings/account/email",
        "/settings/account/photo",
        "/settings/2fa/start",
        "/settings/sources",
    ],
)
def test_settings_that_could_lock_others_out_are_closed(client, path):
    _start(client)
    r = client.post(path, data={})
    assert r.status_code == 403
    assert "closed in the demo" in r.text


def test_a_visitor_cannot_share_a_card(client, local):
    _start(client)
    v = _visitor(local)
    with local() as s:
        art = _art(s, "mine", v.id)
        s.commit()
    r = client.post(f"/artifact/{art.id}/visibility", headers={"accept": "application/json"})
    assert r.status_code == 403 and r.json()["ok"] is False
    with local() as s:
        assert s.get(Artifact, art.id).shared is False


def test_a_note_saved_as_shared_stays_private_for_a_visitor(client, local):
    _start(client)
    r = client.post("/add/note", data={"name": "My note", "heard": "words", "zone": "shared"})
    assert r.status_code == 303
    with local() as s:
        art = s.scalar(select(Artifact).where(Artifact.name == "My note"))
        assert art is not None and art.shared is False


def test_the_add_form_stops_at_the_cap_too(client, local):
    _start(client)
    v = _visitor(local)
    with local() as s:
        for i in range(3):
            _art(s, f"c{i}", v.id)
        s.commit()
    r = client.post("/add/link", data={"url": "https://example.com", "zone": "private"})
    assert r.status_code == 403
    assert "up to 3 cards" in r.text
    with local() as s:
        assert s.scalar(select(CaptureJob).where(CaptureJob.user_id == v.id)) is None


def test_queued_saves_are_private_unless_the_owner_made_them(local):
    with local() as s:
        owner = s.scalar(select(User).where(User.is_owner.is_(True)))
        reviewer = s.scalar(select(User).where(User.email == "demo"))
    base = {
        "url": "https://example.com",
        "title": "",
        "text": "",
        "text_kind": "page",
        "via": "test",
        "comments_json": "",
        "status": "pending",
        "shared": True,
    }
    mine, _ = captures._insert({**base, "user_id": reviewer.id})
    theirs, _ = captures._insert({**base, "user_id": owner.id})
    with local() as s:
        assert s.get(CaptureJob, mine).shared is False
        assert s.get(CaptureJob, theirs).shared is True


def test_saves_stop_at_the_cap(client, local):
    _start(client)
    v = _visitor(local)
    with local() as s:
        for i in range(2):
            _art(s, f"c{i}", v.id)
        s.add(CaptureJob(url="https://q", user_id=v.id, status="pending"))  # queued counts
        s.commit()
    r = client.post("/api/ext/add", json={"url": "https://example.com"})
    assert r.status_code == 403
    assert "up to 3 cards" in r.json()["error"]


def test_the_owner_is_not_restricted(local):
    with local() as s:
        owner = s.scalar(select(User).where(User.is_owner.is_(True)))
        assert not demo.restricted(owner)
        assert demo.may_share(s, owner.id)


# --- clearing out ------------------------------------------------------------------


def test_purge_deletes_old_visitors_and_old_reviewer_cards_only(local):
    now = datetime.now(UTC)
    old = now - timedelta(hours=25)
    with local() as s:
        owner = s.scalar(select(User).where(User.is_owner.is_(True)))
        reviewer = s.scalar(select(User).where(User.email == "demo"))
        gone = demo.create_visitor(s)
        gone.created_at = old
        stays = demo.create_visitor(s)
        _art(s, "gone-card", gone.id)
        _art(s, "stays-card", stays.id)
        _art(s, "reviewer-old", reviewer.id, created_at=old)
        _art(s, "reviewer-new", reviewer.id)
        _art(s, "starter", owner.id, shared=True, created_at=old)
        s.commit()
        gone_id, stays_id = gone.id, stays.id

    with local() as s:
        assert demo.purge(s, now) == {"visitors": 1, "cards": 1}
        s.commit()

    with local() as s:
        assert s.get(User, gone_id) is None
        assert s.get(User, stays_id) is not None
        assert s.scalar(select(User).where(User.email == "demo")) is not None
        names = set(s.scalars(select(Artifact.name)))
        assert names == {"stays-card", "reviewer-new", "starter"}


def test_seed_list_reads_links_and_skips_comments(tmp_path):
    f = tmp_path / "links.txt"
    f.write_text(
        "# starter catalogue\n\nhttps://a.example/x#readme  note\nnot a link\nhttp://b.example\n",
        encoding="utf-8",
    )
    assert demo._read_list(str(f)) == ["https://a.example/x#readme", "http://b.example"]


def test_seed_refuses_outside_demo_mode(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    with pytest.raises(SystemExit):
        demo.seed(["https://example.com"])


def test_bearer_session_of_a_visitor_is_bound_too(local):
    """The extension signs in with a Bearer token: the same rules apply to it."""
    with local() as s:
        v = demo.create_visitor(s)
        req = SimpleNamespace(
            cookies={},
            headers={"user-agent": "ext"},
            url=SimpleNamespace(scheme="https"),
            client=SimpleNamespace(host="1.2.3.4"),
        )
        token = auth.open_session(s, v, req, Response())
        s.commit()
    from fastapi.testclient import TestClient

    from vivatlas.api import app

    c = TestClient(app, follow_redirects=False)
    r = c.post(
        "/artifact/1/visibility",
        headers={"authorization": f"Bearer {token}", "accept": "application/json"},
    )
    assert r.status_code == 403
