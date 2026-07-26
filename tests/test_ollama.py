import json

import httpx
import pytest

from vivatlas.ai import FallbackTextModel
from vivatlas.ai.ollama import OllamaError, OllamaTextModel, extract_json

SCHEMA = {
    "type": "object",
    "properties": {"summary_short": {"type": "string"}, "summary_normal": {"type": "string"}},
    "required": ["summary_short", "summary_normal"],
}


def _model(replies: list[str], url: str = "https://ollama.com") -> tuple[OllamaTextModel, list]:
    """An Ollama model whose transport hands back canned assistant replies."""
    seen: list[dict] = []
    remaining = list(replies)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            {
                "url": str(request.url),
                "auth": request.headers.get("authorization"),
                "body": json.loads(request.content),
            }
        )
        return httpx.Response(200, json={"message": {"content": remaining.pop(0)}})

    model = OllamaTextModel(api_key="k", model="gpt-oss:120b", base_url=url)
    model._client = httpx.AsyncClient(
        base_url=url.rstrip("/"),
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer k"},
    )
    return model, seen


# --- the parser ------------------------------------------------------------


def test_extract_json_reads_plain_and_dressed_up_replies():
    want = {"a": 1}
    assert extract_json('{"a": 1}') == want
    assert extract_json('```json\n{"a": 1}\n```') == want
    assert extract_json('Here you go:\n{"a": 1}\nHope that helps!') == want
    assert extract_json("no json here") is None
    assert extract_json("") is None
    assert extract_json("[1, 2]") is None  # a list isn't the form we asked for


# --- the provider ----------------------------------------------------------


async def test_clean_json_is_returned():
    model, seen = _model(['{"summary_short": "s", "summary_normal": "n"}'])
    try:
        assert await model.generate_json("describe", SCHEMA) == {
            "summary_short": "s",
            "summary_normal": "n",
        }
        assert seen[0]["url"].endswith("/api/chat")
        assert seen[0]["auth"] == "Bearer k"
        # The schema goes in the prompt, since the cloud can't enforce it.
        assert "summary_short" in seen[0]["body"]["messages"][0]["content"]
    finally:
        await model.aclose()


async def test_fenced_reply_still_parses():
    model, _ = _model(['```json\n{"summary_short": "s", "summary_normal": "n"}\n```'])
    try:
        assert (await model.generate_json("d", SCHEMA))["summary_short"] == "s"
    finally:
        await model.aclose()


async def test_a_missing_field_is_asked_again_then_gives_up():
    # First reply is short of a required field, second is fine.
    model, seen = _model(
        ['{"summary_short": "s"}', '{"summary_short": "s", "summary_normal": "n"}']
    )
    try:
        assert (await model.generate_json("d", SCHEMA))["summary_normal"] == "n"
        assert len(seen) == 2
    finally:
        await model.aclose()

    model, _ = _model(['not json at all', 'still not json'])
    try:
        with pytest.raises(OllamaError):
            await model.generate_json("d", SCHEMA)
    finally:
        await model.aclose()


async def test_a_local_server_gets_the_schema_enforced():
    """Against our own Ollama the format parameter works, so we send it — that's the
    whole reason the address is configurable."""
    model, seen = _model(
        ['{"summary_short": "s", "summary_normal": "n"}'], url="http://nas.local:11434"
    )
    try:
        await model.generate_json("d", SCHEMA)
        assert seen[0]["body"]["format"] == SCHEMA
    finally:
        await model.aclose()

    model, seen = _model(['{"summary_short": "s", "summary_normal": "n"}'])
    try:
        await model.generate_json("d", SCHEMA)
        assert "format" not in seen[0]["body"]  # the cloud refuses it
    finally:
        await model.aclose()


async def test_video_is_not_offered_to_ollama():
    model, _ = _model([])
    try:
        with pytest.raises(OllamaError):
            await model.generate_json_with_media("d", SCHEMA, "video/mp4", "AA==")
    finally:
        await model.aclose()


# --- the fallback ----------------------------------------------------------


class _Boom:
    model = "ollama-broken"

    async def generate_json(self, prompt, schema):
        raise OllamaError("no usable reply")

    async def aclose(self):
        return None


class _Google:
    model = "gemini-x"

    def __init__(self):
        self.calls = 0

    async def generate_json(self, prompt, schema):
        self.calls += 1
        return {"summary_short": "from google", "summary_normal": "n"}

    async def generate_json_with_media(self, prompt, schema, mime_type, data_base64):
        self.calls += 1
        return {"summary_short": "watched", "summary_normal": "n"}

    async def aclose(self):
        return None


async def test_google_steps_in_when_ollama_cannot_answer(monkeypatch):
    google = _Google()
    monkeypatch.setattr("vivatlas.ai._build_google_text", lambda: google)
    model = FallbackTextModel(_Boom())
    out = await model.generate_json("d", SCHEMA)
    assert out["summary_short"] == "from google"
    assert google.calls == 1
    # The card should say who actually described it.
    assert model.model == "gemini-x"
    await model.aclose()


async def test_a_working_ollama_never_touches_google(monkeypatch):
    google = _Google()
    monkeypatch.setattr("vivatlas.ai._build_google_text", lambda: google)
    primary, _ = _model(['{"summary_short": "s", "summary_normal": "n"}'])
    model = FallbackTextModel(primary)
    assert (await model.generate_json("d", SCHEMA))["summary_short"] == "s"
    assert google.calls == 0        # the whole point: Google's quota is untouched
    await model.aclose()


async def test_clips_always_go_to_google(monkeypatch):
    google = _Google()
    monkeypatch.setattr("vivatlas.ai._build_google_text", lambda: google)
    model = FallbackTextModel(_Boom())
    out = await model.generate_json_with_media("d", SCHEMA, "video/mp4", "AA==")
    assert out["summary_short"] == "watched"
    assert google.calls == 1
    await model.aclose()


# --- picking the provider --------------------------------------------------


def test_provider_choice_follows_config(monkeypatch):
    from vivatlas import ai
    from vivatlas.config import settings

    monkeypatch.setattr(settings, "google_api_key", "g")
    monkeypatch.setattr(settings, "text_provider", "google")
    assert not isinstance(ai.build_text_model(), FallbackTextModel)

    monkeypatch.setattr(settings, "text_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_model", "gpt-oss:120b")
    assert isinstance(ai.build_text_model(), FallbackTextModel)

    # Ollama chosen but not set up — fall back to Google rather than break the catalogue.
    monkeypatch.setattr(settings, "ollama_model", "")
    assert not isinstance(ai.build_text_model(), FallbackTextModel)
