from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import Response

from vivatlas import auth, security, twofactor
from vivatlas.config import settings
from vivatlas.models import User


@pytest.fixture
def session(make_session):
    with make_session() as s:
        yield s


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "test-secret-key-long-enough-for-the-door")


def make_user(session, email="boris@example.com", password="correct horse battery staple", **kw):
    u = User(
        email=email,
        display_name="Boris",
        password_hash=security.hash_password(password),
        **kw,
    )
    session.add(u)
    session.flush()
    return u


# --- sign-in ---


def test_right_password_passes(session):
    make_user(session)
    r = auth.check_login(session, "boris@example.com", "correct horse battery staple")
    assert r.ok and not r.needs_totp


def test_wrong_password_fails(session):
    make_user(session)
    r = auth.check_login(session, "boris@example.com", "totally the wrong password")
    assert not r.ok


def test_unknown_email_and_wrong_password_give_the_same_answer(session):
    # The response must not reveal whether the email is registered or not.
    make_user(session)
    a = auth.check_login(session, "boris@example.com", "wrong password here")
    b = auth.check_login(session, "stranger@example.com", "wrong password here")
    assert a.error == b.error
    assert not a.ok and not b.ok


def test_email_is_case_insensitive(session):
    make_user(session, email="boris@example.com")
    r = auth.check_login(session, "Boris@Example.COM", "correct horse battery staple")
    assert r.ok


def test_lockout_after_too_many_tries(session):
    make_user(session)
    for _ in range(auth.MAX_FAILS):
        auth.check_login(session, "boris@example.com", "nope")
    # Now locked out — even the correct password is refused until the lock cools off.
    r = auth.check_login(session, "boris@example.com", "correct horse battery staple")
    assert not r.ok
    assert r.locked_minutes > 0


def test_good_login_resets_the_counter(session):
    u = make_user(session)
    for _ in range(3):
        auth.check_login(session, "boris@example.com", "nope")
    assert u.failed_logins == 3
    auth.check_login(session, "boris@example.com", "correct horse battery staple")
    assert u.failed_logins == 0


def test_inactive_user_cannot_enter(session):
    make_user(session, is_active=False)
    r = auth.check_login(session, "boris@example.com", "correct horse battery staple")
    assert not r.ok


def test_two_factor_stops_at_second_step(session):
    from datetime import UTC, datetime

    make_user(session, totp_enabled_at=datetime.now(UTC))
    r = auth.check_login(session, "boris@example.com", "correct horse battery staple")
    assert r.ok and r.needs_totp


# --- second code ---


def test_totp_code_verifies(session):
    u = make_user(session)
    secret = twofactor.new_secret()
    u.totp_secret_enc = security.encrypt_secret(secret)
    import pyotp

    code = pyotp.TOTP(secret).now()
    assert twofactor.verify_totp(u, code)


def test_same_totp_code_is_not_accepted_twice(session):
    # A shoulder-surfed code lives 30 seconds — we don't accept it a second time.
    u = make_user(session)
    secret = twofactor.new_secret()
    u.totp_secret_enc = security.encrypt_secret(secret)
    import pyotp

    code = pyotp.TOTP(secret).now()
    assert twofactor.verify_totp(u, code)
    assert not twofactor.verify_totp(u, code)


def test_wrong_totp_code_refused(session):
    u = make_user(session)
    u.totp_secret_enc = security.encrypt_secret(twofactor.new_secret())
    assert not twofactor.verify_totp(u, "000000")
    assert not twofactor.verify_totp(u, "letters")


# --- backup codes ---


def test_backup_codes_are_issued_and_hashed(session):
    u = make_user(session)
    codes = twofactor.make_backup_codes(session, u)
    assert len(codes) == twofactor.BACKUP_CODES_COUNT
    # Only hashes in the database — the codes themselves aren't stored.
    for row in u.backup_codes:
        assert row.code_hash.startswith("$argon2id$")


def test_backup_code_works_once(session):
    u = make_user(session)
    codes = twofactor.make_backup_codes(session, u)
    assert twofactor.use_backup_code(session, u, codes[0])
    # The same code won't work a second time.
    assert not twofactor.use_backup_code(session, u, codes[0])


def test_reissue_cancels_old_codes(session):
    u = make_user(session)
    old = twofactor.make_backup_codes(session, u)
    twofactor.make_backup_codes(session, u)  # reissue
    assert not twofactor.use_backup_code(session, u, old[0])


def test_unused_count(session):
    u = make_user(session)
    codes = twofactor.make_backup_codes(session, u)
    twofactor.use_backup_code(session, u, codes[0])
    assert twofactor.unused_backup_count(u) == twofactor.BACKUP_CODES_COUNT - 1


# --- sessions: 30 days from the last use, not from sign-in ---


class _Req:
    """Enough of a Request for auth: cookies, headers, scheme, client ip."""

    def __init__(self, cookies=None):
        self.cookies = cookies or {}
        self.headers = {}
        self.url = SimpleNamespace(scheme="https")
        self.client = SimpleNamespace(host="1.2.3.4")


@pytest.fixture
def clock(monkeypatch):
    """auth's own clock, moved by hand: clock.now += timedelta(days=...). It starts at
    the real time, because the row's last_seen_at default comes from the real clock."""
    c = SimpleNamespace(now=datetime.now(UTC))
    monkeypatch.setattr(auth, "_now", lambda: c.now)
    return c


def _signed_in(session):
    token = auth.open_session(session, make_user(session), _Req(), Response())
    session.flush()
    return _Req(cookies={auth.COOKIE_NAME: token})


def test_a_session_in_use_outlives_30_days_from_sign_in(session, clock):
    req = _signed_in(session)
    for _ in range(4):  # once every three weeks: 84 days after sign-in
        clock.now += timedelta(days=21)
        assert auth.current_user(session, req) is not None


def test_a_session_unused_for_30_days_ends(session, clock):
    req = _signed_in(session)
    clock.now += timedelta(days=29)
    assert auth.current_user(session, req) is not None
    clock.now += timedelta(days=auth.SESSION_DAYS, minutes=1)
    assert auth.current_user(session, req) is None


def test_renewal_rides_on_the_last_seen_write(session, clock):
    # Within two minutes of the last write nothing is written, the expiry included.
    req = _signed_in(session)
    clock.now += timedelta(days=1)
    auth.current_user(session, req)
    row = session.query(auth.UserSession).one()
    renewed = auth._aware(row.expires_at)  # SQLite hands the date back without a zone
    assert renewed == clock.now + timedelta(days=auth.SESSION_DAYS)
    clock.now += timedelta(minutes=1)
    auth.current_user(session, req)
    assert auth._aware(row.expires_at) == renewed


def test_a_revoked_session_is_not_renewed_back(session, clock):
    req = _signed_in(session)
    row = session.query(auth.UserSession).one()
    row.revoked_at = clock.now
    clock.now += timedelta(days=1)
    assert auth.current_user(session, req) is None
