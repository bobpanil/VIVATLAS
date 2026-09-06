"""Signing a phone in by a scanned code.

The code IS the credential, so what matters is not that it works but that it stops
working: once used, once expired, and once a newer one has been shown. Each of those
gets its own test, because each is the only thing standing between a photographed
screen and someone else's account.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import Response

from vivatlas import qrlogin, runtime_settings, security
from vivatlas.config import settings
from vivatlas.models import QrLogin, User


class _FakeReq:
    """Enough of a Request for auth and qrlogin: headers, scheme, client, base_url."""

    def __init__(self, headers=None, base_url="http://127.0.0.1:8710/", scheme="https",
                 host="127.0.0.1"):
        self.cookies = {}
        self.headers = headers or {}
        self.url = SimpleNamespace(scheme=scheme, hostname=host)
        self.client = SimpleNamespace(host="9.9.9.9")
        self.base_url = base_url


@pytest.fixture
def user(make_session, monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "test-secret-key-long-enough-for-the-door")
    session = make_session()
    u = User(email="a@x.com", display_name="A", password_hash=security.hash_password("pw"))
    session.add(u)
    session.flush()
    return session, u


def test_a_fresh_code_opens_a_session_for_its_owner(user):
    session, u = user
    raw = qrlogin.mint(session, u)
    session.flush()

    claimed = qrlogin.claim(session, raw, _FakeReq(headers={"user-agent": "phone"}), Response())
    assert claimed is not None
    token, signed_in = claimed
    assert token                      # a real session key came back
    assert signed_in.id == u.id       # and it is the person who showed the code


def test_the_raw_code_is_never_stored(user):
    """Only the hash goes in — whoever reads the database finds no live passes."""
    session, u = user
    raw = qrlogin.mint(session, u)
    session.flush()

    row = session.query(QrLogin).one()
    assert row.token_hash != raw
    assert row.token_hash == security.token_hash(raw)


def test_a_code_works_once_and_then_never_again(user):
    """The whole point: a shoulder-surfed code is already spent."""
    session, u = user
    raw = qrlogin.mint(session, u)
    session.flush()

    assert qrlogin.claim(session, raw, _FakeReq(), Response()) is not None
    assert qrlogin.claim(session, raw, _FakeReq(), Response()) is None


def test_an_expired_code_is_refused(user):
    session, u = user
    raw = qrlogin.mint(session, u)
    session.flush()

    row = session.query(QrLogin).one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.flush()

    assert qrlogin.claim(session, raw, _FakeReq(), Response()) is None


def test_showing_a_new_code_retires_the_old_one(user):
    """Every refresh of the page must not leave another live pass behind."""
    session, u = user
    first = qrlogin.mint(session, u)
    session.flush()
    second = qrlogin.mint(session, u)
    session.flush()

    assert qrlogin.claim(session, first, _FakeReq(), Response()) is None
    assert qrlogin.claim(session, second, _FakeReq(), Response()) is not None


def test_a_made_up_code_is_refused(user):
    session, _ = user
    assert qrlogin.claim(session, "not-a-real-token", _FakeReq(), Response()) is None
    assert qrlogin.claim(session, "", _FakeReq(), Response()) is None


def test_a_deactivated_person_cannot_be_signed_in_by_an_old_code(user):
    """Turning someone off has to close this door too, not just the password one."""
    session, u = user
    raw = qrlogin.mint(session, u)
    u.is_active = False
    session.flush()

    assert qrlogin.claim(session, raw, _FakeReq(), Response()) is None


def test_who_claimed_it_is_recorded(user):
    """This is the one sign-in the account holder didn't type — the row must say
    where it came from, so a stranger's use is visible afterwards."""
    session, u = user
    raw = qrlogin.mint(session, u)
    session.flush()

    qrlogin.claim(
        session,
        raw,
        _FakeReq(headers={"user-agent": "VIVATLAS-Android", "x-forwarded-for": "5.6.7.8"}),
        Response(),
    )
    row = session.query(QrLogin).one()
    assert row.used_at is not None
    assert row.used_ip == "5.6.7.8"
    assert row.used_user_agent == "VIVATLAS-Android"


def test_on_our_own_host_the_request_address_will_do(user):
    """One scan has to tell the phone *which* VIVATLAS as well as who. On a LAN or
    loopback address the request says so truthfully, and a home install should not
    have to configure anything to sign a phone in."""
    session, _ = user
    req = _FakeReq(base_url="http://10.0.0.5:8710/", host="10.0.0.5")
    url = qrlogin.code_url(session, req, "TOK")
    assert url == "http://10.0.0.5:8710/qr/TOK"


def test_the_configured_site_address_wins_over_the_request(user):
    """Behind a TLS-terminating proxy the request arrives as plain http on an
    internal address, so base_url names somewhere the phone must not be sent: an
    http code for an https site walks the app into a 301 it will not follow, and
    the sign-in fails with nothing on screen to explain it."""
    session, _ = user
    runtime_settings.set(session, runtime_settings.SITE_URL, "https://atlas.example.com")
    session.flush()

    req = _FakeReq(base_url="http://172.17.0.4:8710/", host="172.17.0.4", scheme="http")
    assert qrlogin.code_url(session, req, "TOK") == "https://atlas.example.com/qr/TOK"
    assert qrlogin.code_origin(session, req) == "https://atlas.example.com"


def test_no_code_at_all_for_a_public_host_with_no_site_address(user):
    """The Host header is the client's word. Building a code from it on a public
    address is how a reset link gets led off to a foreign domain — and this one
    carries a credential the phone will hand straight back. Nothing to name means
    no code: the page tells the owner what to set instead.

    Deliberately not solved by TRUSTED_PROXIES. That makes the scheme truthful,
    but it is optional, so most installs behind a tunnel would be quietly wrong."""
    session, _ = user
    req = _FakeReq(base_url="http://atlas.example.com/", host="atlas.example.com", scheme="http")
    assert qrlogin.code_origin(session, req) is None
    assert qrlogin.code_url(session, req, "TOK") is None


def test_sweep_clears_stale_passes_but_leaves_live_ones(user):
    session, u = user
    live = qrlogin.mint(session, u)
    session.flush()
    session.add(
        QrLogin(
            user_id=u.id,
            token_hash=security.token_hash("stale"),
            expires_at=datetime.now(UTC) - timedelta(minutes=5),
        )
    )
    session.flush()
    assert session.query(QrLogin).count() == 2

    qrlogin.sweep(session)
    session.flush()

    rows = session.query(QrLogin).all()
    assert len(rows) == 1
    assert rows[0].token_hash == security.token_hash(live)
