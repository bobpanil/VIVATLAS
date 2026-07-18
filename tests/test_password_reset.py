import pytest

from vivatlas import auth, security
from vivatlas.config import settings
from vivatlas.models import User


@pytest.fixture
def session(make_session):
    with make_session() as s:
        yield s


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "ключ-для-тестов-двери-длинный")


def make_user(session, email="boris@example.com", password="мама мыла раму синей", **kw):
    u = User(
        email=email,
        display_name="Boris",
        password_hash=security.hash_password(password),
        **kw,
    )
    session.add(u)
    session.flush()
    return u


def test_token_roundtrip(session):
    u = make_user(session)
    tok = auth.make_reset_token(u)
    assert auth.read_reset_token(session, tok) is u


def test_token_void_after_password_change(session):
    # Смена пароля должна убивать ссылку: иначе одна ссылка меняла бы пароль
    # сколько угодно раз, в том числе после того, как им уже воспользовались.
    u = make_user(session)
    tok = auth.make_reset_token(u)
    u.password_hash = security.hash_password("совсем другой длинный пароль")
    session.flush()
    assert auth.read_reset_token(session, tok) is None


def test_expired_token_rejected(session):
    u = make_user(session)
    tok = auth.make_reset_token(u)
    assert auth.read_reset_token(session, tok, max_age=-1) is None


def test_garbage_token_rejected(session):
    make_user(session)
    assert auth.read_reset_token(session, "не токен вовсе") is None
    assert auth.read_reset_token(session, "") is None


def test_inactive_user_token_rejected(session):
    u = make_user(session, is_active=False)
    tok = auth.make_reset_token(u)
    assert auth.read_reset_token(session, tok) is None


def test_token_signed_with_other_key_rejected(session, monkeypatch):
    u = make_user(session)
    tok = auth.make_reset_token(u)
    # Сменили главный ключ — прежние подписи должны перестать проходить.
    monkeypatch.setattr(settings, "secret_key", "совсем другой длинный ключ подписи")
    assert auth.read_reset_token(session, tok) is None
