"""Default avatar set: the preset module and the backfill migration."""

from sqlalchemy import create_engine, text

from vivatlas import usericons
from vivatlas.migrate import backfill_avatar_presets
from vivatlas.models import Base, User


def test_presets_present_and_named():
    # The set is present and named following the avatar-NN scheme.
    assert usericons.PRESETS, "no avatars found in static/usericons"
    assert all(k.startswith("avatar-") for k in usericons.PRESETS)
    # Stable ordering (affects how they appear in settings).
    assert usericons.PRESETS == sorted(usericons.PRESETS)


def test_is_valid():
    assert usericons.is_valid(usericons.PRESETS[0])
    assert not usericons.is_valid("../secrets")
    assert not usericons.is_valid("")
    assert not usericons.is_valid("avatar-999")


def test_random_preset_is_valid():
    # Many times over — always from the set (guards against going out of range).
    for _ in range(50):
        assert usericons.is_valid(usericons.random_preset())


def test_read_bytes_is_webp_for_valid():
    data = usericons.read_bytes(usericons.PRESETS[0])
    assert data is not None
    # webp signature: RIFF....WEBP
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def test_read_bytes_none_for_unknown():
    assert usericons.read_bytes("nope") is None
    assert usericons.path("../etc/passwd") is None


def test_backfill_assigns_and_is_idempotent():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (email, display_name, password_hash, is_owner,"
                " is_active, avatar_preset, totp_secret_enc, totp_last_code,"
                " failed_logins, created_at) VALUES "
                "('a@x','a','h',0,1,'',' ','',0,CURRENT_TIMESTAMP),"
                "('b@x','b','h',0,1,'',' ','',0,CURRENT_TIMESTAMP)"
            )
        )
        # first run — assigns one to both
        assert backfill_avatar_presets(conn) == 2
        presets = [r[0] for r in conn.execute(text("SELECT avatar_preset FROM users")).fetchall()]
        assert all(usericons.is_valid(p) for p in presets)
        # rerun — leaves everything untouched
        assert backfill_avatar_presets(conn) == 0


def test_new_user_gets_preset_via_default_only_if_set():
    # Model default is ''; the creation code (auth_web) sets the random one, not the model.
    u = User(email="c@x", display_name="c", password_hash="h")
    assert u.avatar_preset == "" or u.avatar_preset is None
