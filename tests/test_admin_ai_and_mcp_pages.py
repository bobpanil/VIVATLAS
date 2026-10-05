"""The admin AI tab lists every model from the start (Ollama too), and the Settings
MCP tab speaks to any assistant and says whose connections it lists."""

import json
import re
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from vivatlas import auth, db, modellist, runtime_settings
from vivatlas.config import settings
from vivatlas.migrate import create_fts_table
from vivatlas.models import Base, User


@pytest.fixture
def owner_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from vivatlas.api import app

    engine = create_engine(f"sqlite:///{tmp_path / 'pages.db'}", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        create_fts_table(conn)
    Local = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(db, "SessionLocal", Local)
    monkeypatch.setattr(settings, "secret_key", "test-secret-key-long-enough-for-the-door")
    monkeypatch.setattr(settings, "llm_model", "gemini-b")
    monkeypatch.setattr(settings, "ollama_model", "gpt-oss:120b")
    with Local() as s:
        owner = User(email="panibor@example.com", display_name="Boris", password_hash="h",
                     is_owner=True)
        s.add(owner)
        s.flush()
        req = SimpleNamespace(cookies={}, headers={"user-agent": "test"},
                              url=SimpleNamespace(scheme="https"),
                              client=SimpleNamespace(host="1.2.3.4"))
        token = auth.open_session(s, owner, req, Response())
        runtime_settings.set(s, modellist.KEY, json.dumps({
            "text": ["gemini-a", "gemini-b", "gemini-c"],
            "embedding": ["emb-1", "emb-2"],
            "ollama": ["glm-5.2", "gpt-oss:120b", "kimi-k3"],
            "checked_at": datetime.now(UTC).isoformat(),
            "errors": {},
        }))
        s.commit()
    client = TestClient(app)
    client.cookies.set(auth.COOKIE_NAME, token)
    return client


def _options(html: str, name: str) -> list[str]:
    m = re.search(r'<select name="' + name + r'"[^>]*>(.*?)</select>', html, re.S)
    assert m, f"no select named {name}"
    return re.findall(r'<option value="([^"]*)"', m.group(1))


def test_ai_tab_lists_every_model_from_the_start(owner_client):
    r = owner_client.get("/admin")
    assert r.status_code == 200, r.status_code
    assert _options(r.text, "llm_model") == ["gemini-a", "gemini-b", "gemini-c"]
    assert _options(r.text, "embedding_model")[-2:] == ["emb-1", "emb-2"]
    # Ollama is a dropdown now, not a box to type the name into.
    assert _options(r.text, "ollama_model") == ["glm-5.2", "gpt-oss:120b", "kimi-k3"]
    assert re.search(r'<option value="gpt-oss:120b" selected>', r.text)
    assert 'id="ai-models-refresh"' in r.text


def test_model_endpoint_answers_from_the_kept_list(owner_client):
    d = owner_client.get("/admin/ai/models").json()
    assert d["ollama"] == ["glm-5.2", "gpt-oss:120b", "kimi-k3"] and d["errors"] == {}


def test_mcp_tab_is_for_any_assistant_and_names_the_account(owner_client, monkeypatch):
    monkeypatch.setattr(settings, "public_url", "https://vivatlas.example.com")
    r = owner_client.get("/settings")
    assert r.status_code == 200, r.status_code
    html = r.text
    assert re.search(r'data-tab="mcp">\s*MCP\s*<', html)
    assert 'data-tab="chatgpt"' not in html
    url = "https://vivatlas.example.com/mcp-server/mcp"
    assert f"claude mcp add --transport http vivatlas {url}" in html
    assert "httpUrl" in html and "Gemini CLI" in html and "ChatGPT" in html
    assert "Connected to this account (panibor@example.com)" in html
