"""The AI model lists the admin picks from: kept by the server, refreshed by itself,
never emptied by a failed refresh, and rendered into the dropdowns from the start."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from vivatlas import modellist, runtime_settings
from vivatlas.config import settings


@pytest.fixture
def store(make_session, monkeypatch):
    session = make_session()

    @contextmanager
    def scope():
        yield session
        session.commit()

    monkeypatch.setattr(modellist, "session_scope", scope)
    return session


async def test_ollama_list_comes_from_api_tags_with_the_key():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"models": [
            {"name": "gpt-oss:120b", "model": "gpt-oss:120b"},
            {"name": "kimi-k3", "model": "kimi-k3"},
            {"model": "glm-5.2"},
            {"name": "gpt-oss:120b"},
        ]})

    names = await modellist.list_ollama("https://ollama.com/", "k-123",
                                        transport=httpx.MockTransport(handler))
    assert names == ["glm-5.2", "gpt-oss:120b", "kimi-k3"]
    assert seen["url"] == "https://ollama.com/api/tags"
    assert seen["auth"] == "Bearer k-123"


async def test_refresh_keeps_the_last_good_list_when_a_provider_fails(store, monkeypatch):
    monkeypatch.setattr(settings, "google_api_key", "g-key")
    monkeypatch.setattr(settings, "ollama_url", "https://ollama.com")
    monkeypatch.setattr(settings, "ollama_api_key", "")

    async def google_ok(key, timeout):
        return {"text": ["gemini-a", "gemini-b"], "embedding": ["emb-1"]}

    async def ollama_ok(url, key, timeout):
        return ["gpt-oss:120b"]

    import vivatlas.ai.google as g

    monkeypatch.setattr(g, "list_available_models", google_ok)
    monkeypatch.setattr(modellist, "list_ollama", ollama_ok)
    first = await modellist.refresh()
    assert first["text"] == ["gemini-a", "gemini-b"] and first["ollama"] == ["gpt-oss:120b"]
    assert first["errors"] == {}

    async def google_down(key, timeout):
        raise RuntimeError("HTTP 503")

    monkeypatch.setattr(g, "list_available_models", google_down)
    second = await modellist.refresh()
    assert second["text"] == ["gemini-a", "gemini-b"]  # kept, not emptied
    assert "503" in second["errors"]["google"]
    assert modellist.cached()["text"] == ["gemini-a", "gemini-b"]


async def test_no_google_key_is_reported_not_crashed(store, monkeypatch):
    monkeypatch.setattr(settings, "google_api_key", "")
    monkeypatch.setattr(settings, "ollama_url", "")
    data = await modellist.refresh()
    assert data["errors"]["google"] == "no-key" and data["text"] == []


def test_staleness(store):
    assert modellist.is_stale(modellist.cached())  # never checked
    fresh = {**modellist.cached(), "checked_at": datetime.now(UTC).isoformat()}
    modellist._store(fresh)
    assert not modellist.is_stale(modellist.cached())
    old = {**fresh, "checked_at": (datetime.now(UTC) - timedelta(hours=7)).isoformat()}
    modellist._store(old)
    assert modellist.is_stale(modellist.cached())
    modellist._store(fresh)
    modellist.mark_stale()
    assert modellist.is_stale(modellist.cached())


def test_cached_survives_garbage(store):
    runtime_settings.set(store, modellist.KEY, "{not json")
    store.commit()
    assert modellist.cached()["text"] == []
    runtime_settings.set(store, modellist.KEY, json.dumps({"text": ["x"], "junk": 1}))
    store.commit()
    data = modellist.cached()
    assert data["text"] == ["x"] and "junk" not in data


# What a Google AI Studio key really listed on 2026-10-05 (generateContent models in
# "text", embedContent ones in "embedding"), and what the dropdowns should keep.
_GOOGLE_TEXT = [
    "gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-preview-tts",
    "gemini-2.5-pro-preview-tts", "gemma-4-26b-a4b-it", "gemma-4-31b-it",
    "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-pro-latest",
    "gemini-2.5-flash-lite", "gemini-2.5-flash-image", "gemini-3-flash-preview",
    "gemini-3.1-pro-preview", "gemini-3.1-pro-preview-customtools",
    "gemini-3.1-flash-lite-preview", "gemini-3.1-flash-lite", "gemini-3-pro-image-preview",
    "gemini-3-pro-image", "nano-banana-pro-preview", "gemini-3.1-flash-image-preview",
    "gemini-3.1-flash-image", "gemini-3.1-flash-lite-image", "gemini-3.5-flash",
    "gemini-3.5-flash-lite", "gemini-omni-flash-preview", "gemini-omni-1.1-flash",
    "gemini-3.5-transcribe", "gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash",
    "lyria-3-clip-preview", "lyria-3-pro-preview", "lyria-3.5",
    "gemini-3.1-flash-tts-preview", "gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts",
    "gemini-robotics-er-2-preview", "gemini-2.5-computer-use-preview-10-2025",
    "antigravity-preview-05-2026", "antigravity-preview-09-2026",
    "antigravity-preview-latest", "deep-research-max-preview-04-2026",
    "deep-research-preview-04-2026", "deep-research-pro-preview-12-2025",
]
_GOOGLE_EMBED = ["gemini-embedding-001", "gemini-embedding-2-preview", "gemini-embedding-2"]


def test_dropdowns_offer_only_models_fit_for_the_job():
    shown = modellist.for_dropdowns(
        {"text": _GOOGLE_TEXT, "embedding": _GOOGLE_EMBED,
         "ollama": ["nomic-embed-text", "gpt-oss:120b", "kimi-k3"]}
    )
    assert shown["text"] == [
        "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-pro-latest",
        "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash",
        "gemini-3.5-flash", "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite", "gemini-3.1-flash-lite-preview", "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro",
    ]
    assert not any(w in " ".join(shown["text"]) for w in ("lyria", "tts", "image", "veo"))
    assert shown["embedding"] == ["gemini-embedding-2", "gemini-embedding-2-preview",
                                  "gemini-embedding-001"]
    assert shown["ollama"] == ["gpt-oss:120b", "kimi-k3"]
