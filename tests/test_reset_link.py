"""Ссылка в письме о сбросе не должна собираться из чужого Host-заголовка."""
import types

import pytest

from vivatlas import auth_web
from vivatlas import runtime_settings as rs
from vivatlas.config import settings


@pytest.fixture
def session(make_session):
    with make_session() as s:
        yield s


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "ключ-для-тестов-двери-длинный")


def _req(host, base):
    return types.SimpleNamespace(url=types.SimpleNamespace(hostname=host), base_url=base)


def test_is_local_host():
    assert auth_web._is_local_host("localhost")
    assert auth_web._is_local_host("127.0.0.1")
    assert auth_web._is_local_host("192.168.1.5")
    assert auth_web._is_local_host("10.0.0.3")
    assert not auth_web._is_local_host("evil.attacker.example")
    assert not auth_web._is_local_host("8.8.8.8")  # публичный адрес — не свой
    assert not auth_web._is_local_host("")


def test_configured_site_url_wins_over_spoofed_host(session):
    rs.set(session, rs.SITE_URL, "https://vivatlas.example.com")
    req = _req("evil.attacker.example", "http://evil.attacker.example/")
    assert auth_web._reset_link_base(session, req) == "https://vivatlas.example.com"


def test_local_host_fallback_when_no_site_url(session):
    req = _req("127.0.0.1", "http://127.0.0.1:8710/")
    assert auth_web._reset_link_base(session, req) == "http://127.0.0.1:8710"


def test_foreign_host_refused_when_no_site_url(session):
    # Ключевой случай: site_url не задан, Host подделан на чужой домен —
    # ссылку не собираем вовсе, иначе это reset poisoning.
    req = _req("evil.attacker.example", "http://evil.attacker.example/")
    assert auth_web._reset_link_base(session, req) is None
