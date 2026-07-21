"""User management (Phase B): invitations and deleting a user.

Route logic (register/join/admin) is verified live in the browser; here we cover
the critical destructive core: transferring shared cards to the admin, deleting
private ones with a notification to favourites, cascading private folders/sources."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vivatlas import auth
from vivatlas.admin_web import _purge_user
from vivatlas.migrate import create_fts_table
from vivatlas.models import (
    Artifact,
    ArtifactCategory,
    Base,
    Category,
    Favorite,
    Invite,
    RemovedNotice,
    Repository,
    Source,
    User,
)


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    from vivatlas.config import settings

    monkeypatch.setattr(settings, "secret_key", "test-key-of-sufficient-length-for-signing")


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi, _rec):
        cur = dbapi.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        create_fts_table(conn)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _user(s, email, owner=False):
    u = User(email=email, display_name=email, password_hash="h", is_owner=owner)
    s.add(u)
    s.flush()
    return u


def _art(s, owner_id, shared):
    src = s.scalar(select(Source))
    if src is None:
        src = Source(kind="gitea", base_url="http://x", display_name="S", owner_user_id=owner_id)
        s.add(src)
        s.flush()
    repo = Repository(
        source_id=src.id, external_id=f"r{owner_id}-{shared}", owner="o",
        name=f"repo{owner_id}{shared}", default_branch="main",
    )
    s.add(repo)
    s.flush()
    a = Artifact(
        repository_id=repo.id, name=f"art-{owner_id}-{shared}", artifact_type="tool",
        owner_user_id=owner_id, shared=shared,
    )
    s.add(a)
    s.flush()
    return a


# --- invitations -----------------------------------------------------------


def test_invite_make_read_consume(session):
    admin = _user(session, "a@x", owner=True)
    raw = auth.make_invite(session, "guest@x", admin.id)
    session.flush()
    inv = auth.read_invite(session, raw)
    assert inv is not None
    assert inv.email == "guest@x"
    assert auth.read_invite(session, "wrong-code") is None

    user = _user(session, "guest@x")
    assert auth.consume_invite(session, inv, user) is True
    session.flush()
    # single-use: an accepted invitation can no longer be read
    assert auth.read_invite(session, raw) is None


def test_consume_invite_atomic_second_fails(session):
    """Race: the same invitation cannot be accepted twice. The second time rowcount==0."""
    admin = _user(session, "a@x", owner=True)
    raw = auth.make_invite(session, "", admin.id)
    session.flush()
    inv = auth.read_invite(session, raw)
    u1 = _user(session, "one@x")
    assert auth.consume_invite(session, inv, u1) is True
    u2 = _user(session, "two@x")
    # used_at is already set — the conditional UPDATE won't fire
    assert auth.consume_invite(session, inv, u2) is False


def test_invite_expired(session):
    admin = _user(session, "a@x", owner=True)
    raw = auth.make_invite(session, "", admin.id)
    session.flush()
    inv = session.scalar(select(Invite))
    inv.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    session.flush()
    assert auth.read_invite(session, raw) is None


# --- user deletion ---------------------------------------------------------


def test_purge_user_transfers_shared_deletes_private(session):
    admin = _user(session, "admin@x", owner=True)
    victim = _user(session, "victim@x")
    fan = _user(session, "fan@x")

    shared = _art(session, victim.id, shared=True)  # shared — goes to the admin
    private = _art(session, victim.id, shared=False)  # private — gets deleted

    # the fan keeps the victim's PRIVATE card in favourites → should get a notification
    session.add(Favorite(user_id=fan.id, artifact_id=private.id))
    # the victim's private folder with both cards
    cat = Category(name="Mine", owner_user_id=victim.id, names_json="")
    session.add(cat)
    session.flush()
    session.add(ArtifactCategory(artifact_id=shared.id, category_id=cat.id))
    session.add(ArtifactCategory(artifact_id=private.id, category_id=cat.id))
    session.flush()

    shared_id, private_id, cat_id = shared.id, private.id, cat.id
    _purge_user(session, victim, admin.id)
    session.commit()  # commit + expire, to see DB cascades (not the session cache)

    # the shared card stayed in the catalogue, now owned by the admin
    kept = session.get(Artifact, shared_id)
    assert kept is not None and kept.owner_user_id == admin.id and kept.shared is True
    # the private card is deleted entirely
    assert session.get(Artifact, private_id) is None
    # the victim's private folder was cascade-deleted
    assert session.get(Category, cat_id) is None
    # the fan gets a "something disappeared" for the private card; not for the
    # shared one (it stayed)
    notices = session.scalars(select(RemovedNotice).where(RemovedNotice.user_id == fan.id)).all()
    assert len(notices) == 1
    # the user itself is deleted
    assert session.get(User, victim.id) is None


def test_purge_user_clears_legacy_private_to_user_id(session):
    """A shared card transferred to the admin must not be cascade-deleted via the
    legacy private_to_user_id (FK CASCADE), which migration rows still have set."""
    admin = _user(session, "admin@x", owner=True)
    victim = _user(session, "victim@x")
    art = _art(session, victim.id, shared=True)
    art.private_to_user_id = victim.id  # as on a row migrated from the old zone
    session.flush()
    art_id = art.id

    _purge_user(session, victim, admin.id)
    session.commit()

    kept = session.get(Artifact, art_id)
    assert kept is not None  # not wiped by the cascade
    assert kept.owner_user_id == admin.id
    assert kept.private_to_user_id is None  # the link to the departed user is severed
    assert session.get(User, victim.id) is None


def test_purge_user_reassigns_sources_clears_token(session):
    admin = _user(session, "admin@x", owner=True)
    victim = _user(session, "victim@x")
    src = Source(
        kind="gitea", base_url="http://x", display_name="S",
        owner_user_id=victim.id, token_enc="encrypted-token",
    )
    session.add(src)
    session.flush()
    # the source has a repository — deleting the source directly would fail on the FK
    session.add(
        Repository(source_id=src.id, external_id="r1", owner="o", name="r1", default_branch="main")
    )
    session.flush()
    src_id = src.id

    _purge_user(session, victim, admin.id)
    session.commit()  # commit + expire, to see DB cascades (not the session cache)

    moved = session.get(Source, src_id)
    assert moved is not None  # source not deleted (the repository would have blocked it)
    assert moved.owner_user_id == admin.id  # transferred to the admin
    assert moved.token_enc == ""  # someone else's token is cleared
    assert session.get(User, victim.id) is None


# --- registration toggle ---------------------------------------------------


def test_registration_toggle(session):
    from vivatlas import runtime_settings

    assert runtime_settings.registration_open(session) is True  # open by default
    runtime_settings.set_bool(session, runtime_settings.REGISTRATION_OPEN, False)
    session.flush()
    assert runtime_settings.registration_open(session) is False
