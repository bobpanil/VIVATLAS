import pytest

from vivatlas import runtime_settings as rs
from vivatlas.config import settings


@pytest.fixture
def session(make_session):
    with make_session() as s:
        yield s


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "key-for-tests-door-long")


# --- raw access ---


def test_get_default_when_absent(session):
    assert rs.get(session, "no-such-key", "default-value") == "default-value"


def test_set_then_get(session):
    rs.set(session, "k", "v")
    assert rs.get(session, "k") == "v"


def test_set_updates_existing(session):
    rs.set(session, "k", "v1")
    rs.set(session, "k", "v2")
    assert rs.get(session, "k") == "v2"


def test_bool_roundtrip(session):
    rs.set_bool(session, "flag", True)
    assert rs.get_bool(session, "flag", default=False) is True
    rs.set_bool(session, "flag", False)
    assert rs.get_bool(session, "flag", default=True) is False


def test_int_default_on_garbage(session):
    rs.set(session, "port", "not a number")
    assert rs.get_int(session, "port", 587) == 587


def test_registration_open_default_true(session):
    assert rs.registration_open(session) is True


def test_site_url_strips_trailing_slash(session):
    rs.set(session, rs.SITE_URL, "https://x.example.com/")
    assert rs.site_url(session) == "https://x.example.com"


# --- SMTP ---


def _save(session, **over):
    base = dict(
        host="smtp.example.com",
        port=587,
        security_mode="starttls",
        username="user",
        from_addr="from@example.com",
        from_name="VIVATLAS",
        password="secret-mail-password",
    )
    base.update(over)
    rs.save_smtp(session, **base)


def test_smtp_password_encrypted_at_rest(session):
    _save(session)
    stored = rs.get(session, rs.SMTP_PASSWORD_ENC)
    assert stored
    assert "secret-mail-password" not in stored  # only ciphertext in the database
    assert rs.get_smtp(session).password == "secret-mail-password"


def test_smtp_blank_password_keeps_previous(session):
    _save(session, password="first-password")
    _save(session, host="smtp.other.com", password=None)  # change everything except the password
    cfg = rs.get_smtp(session)
    assert cfg.password == "first-password"
    assert cfg.host == "smtp.other.com"


def test_smtp_security_sanitized(session):
    _save(session, security_mode="garbage")
    assert rs.get_smtp(session).security == "starttls"


def test_is_configured_needs_host_and_from(session):
    assert rs.get_smtp(session).is_configured is False
    rs.save_smtp(
        session, host="h", port=25, security_mode="none",
        username="", from_addr="", from_name="", password=None,
    )
    assert rs.get_smtp(session).is_configured is False  # no return address
    rs.save_smtp(
        session, host="h", port=25, security_mode="none",
        username="", from_addr="from@x", from_name="", password=None,
    )
    assert rs.get_smtp(session).is_configured is True


def test_effective_from_falls_back_to_username(session):
    rs.save_smtp(
        session, host="h", port=25, security_mode="none",
        username="login@x", from_addr="", from_name="", password=None,
    )
    assert rs.get_smtp(session).effective_from == "login@x"


def test_password_mask_hides_middle(session):
    _save(session, password="very-secret-password")
    mask = rs.smtp_password_mask(session)
    assert "very-secret-password" not in mask
    assert "•" in mask
