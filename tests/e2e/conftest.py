"""Fixtures for the end-to-end accessibility suite (Playwright + axe-core).

These tests run against a LIVE VIVATLAS server rather than spinning one up, so
they SKIP unless a server is reachable. Start one first, e.g.:

    .venv/Scripts/python -m uvicorn vivatlas.api:app --host 127.0.0.1 --port 8710

then run the suite (optionally pointing it elsewhere):

    VIVATLAS_E2E_URL=http://127.0.0.1:8710 pytest tests/e2e -q

A session is minted straight against the app's database (as the first user), so
no password is needed. One-time setup: `pip install playwright` then
`python -m playwright install chromium`.
"""

import os
import urllib.request
from datetime import timedelta

import pytest

BASE_URL = os.environ.get("VIVATLAS_E2E_URL", "http://127.0.0.1:8710").rstrip("/")


def _server_up(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/health", timeout=2) as r:  # noqa: S310 (local)
            return r.status == 200
    except Exception:
        return False


@pytest.fixture(scope="session")
def base_url() -> str:
    if not _server_up(BASE_URL):
        pytest.skip(f"no VIVATLAS server at {BASE_URL} — start one to run the a11y suite")
    return BASE_URL


@pytest.fixture(scope="session")
def auth_token() -> str:
    """A real session token for the first user, minted directly (no password)."""
    from vivatlas import security
    from vivatlas.auth import SESSION_DAYS, _now
    from vivatlas.db import session_scope
    from vivatlas.models import User, UserSession

    with session_scope() as s:
        user = s.query(User).order_by(User.id).first()
        if user is None:
            pytest.skip("no user in the database to authenticate as")
        raw = security.new_token()
        s.add(
            UserSession(
                user_id=user.id,
                token_hash=security.token_hash(raw),
                user_agent="a11y-e2e",
                ip="127.0.0.1",
                expires_at=_now() + timedelta(days=SESSION_DAYS),
            )
        )
        s.flush()
        return raw


@pytest.fixture(scope="session")
def _browser():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 — browser binary not installed
            pytest.skip(f"chromium not available for Playwright ({exc}); run 'playwright install chromium'")
        yield browser
        browser.close()


@pytest.fixture
def context(_browser, base_url, auth_token):
    from urllib.parse import urlparse

    host = urlparse(base_url).hostname
    ctx = _browser.new_context()
    ctx.add_cookies(
        [{"name": "vivatlas_session", "value": auth_token, "domain": host, "path": "/"}]
    )
    yield ctx
    ctx.close()
