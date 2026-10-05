"""Connecting assistants other than ChatGPT: Claude, Claude Code, Gemini CLI and any
client that follows the MCP authorization spec. An end-to-end run against a live
server lives in tests/e2e/oauth_clients.py; these pin the pieces it found broken."""

import os
import time
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from mcp.shared.auth import OAuthClientInformationFull

from vivatlas import mcp_oauth, mcp_web
from vivatlas.config import settings


def test_token_expiry_is_read_as_utc_whatever_the_machine_timezone():
    """SQLite returns datetimes without a zone; read as local time they made every
    one-hour token look expired on a server three hours east of UTC."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Jerusalem"
    time.tzset()
    try:
        naive = datetime(2026, 10, 5, 9, 0, 0)
        utc = datetime(2026, 10, 5, 9, 0, 0, tzinfo=UTC)
        assert mcp_oauth._epoch(naive) == int(utc.timestamp())
        assert mcp_oauth._epoch(None) is None
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


@pytest.fixture
def oauth_store(make_session, monkeypatch):
    session = make_session()

    @contextmanager
    def scope():
        yield session
        session.commit()

    monkeypatch.setattr(mcp_oauth, "session_scope", scope)
    return mcp_oauth.VivatlasOAuthProvider()


@pytest.mark.parametrize(
    "registered, expected",
    [
        (None, {"vivatlas"}),
        ("", {"vivatlas"}),  # Gemini CLI registers an empty scope
        ("vivatlas", {"vivatlas"}),
        ("openid profile offline_access", {"openid", "profile", "offline_access", "vivatlas"}),
    ],
)
async def test_every_client_may_ask_for_our_scope(oauth_store, registered, expected):
    info = OAuthClientInformationFull(
        client_id=f"c-{registered}", redirect_uris=["http://localhost:7777/oauth/callback"],
        scope=registered, token_endpoint_auth_method="none",
    )
    await oauth_store.register_client(info)
    stored = await oauth_store.get_client(info.client_id)
    assert set(stored.scope.split()) == expected
    assert stored.validate_scope("vivatlas") == ["vivatlas"]


def test_discovery_says_public_clients_and_our_scope_are_supported(monkeypatch):
    monkeypatch.setattr(settings, "public_url", "https://vivatlas.example.com")
    asm = mcp_web.authorization_server_metadata()
    assert "none" in asm["token_endpoint_auth_methods_supported"]
    assert asm["scopes_supported"] == ["vivatlas"]
    prm = mcp_web.protected_resource_metadata()
    assert prm["scopes_supported"] == ["vivatlas"]
    assert prm["resource"] == "https://vivatlas.example.com/mcp-server/mcp"
