"""Ollama — the cloud (ollama.com) or your own server.

Here to take the everyday text work off Google. Google AI Studio's free tier is
tight, and it is also the only thing that can embed: every vector already stored
is 1536 numbers from gemini-embedding-2, and a vector from another model doesn't
sit in the same space, so switching would silently break search. Descriptions and
translations have no such tie — any competent model writes them. So they move
here and the quota is spent where it can't be replaced.

One caveat shapes this file. Ollama's cloud does NOT support structured outputs:
the `format` json-schema parameter is a local-only feature. So against the cloud
we ask for JSON in the prompt and check what comes back — while still sending
`format` when talking to a local server, which honours it. Either way the reply is
parsed defensively, validated against the schema's required keys, retried once,
and if it still isn't usable the caller falls back to Google (see ai/__init__).
The address is configurable for exactly this reason: point it at your own Ollama
later and schema enforcement starts working with no code change.
"""

import json
import logging
import re

import httpx

log = logging.getLogger(__name__)

DEFAULT_URL = "https://ollama.com"


class OllamaError(RuntimeError):
    pass


def _required_keys(schema: dict) -> list[str]:
    req = schema.get("required")
    if isinstance(req, list):
        return [k for k in req if isinstance(k, str)]
    props = schema.get("properties")
    return list(props) if isinstance(props, dict) else []


def extract_json(text: str) -> dict | None:
    """The object out of a reply that may be wrapped in prose or a ``` fence.

    Without schema enforcement a model is free to add "Here's the JSON:" or fence
    the block, and both are common. Rather than fail on that, take the outermost
    {...} and parse it; None if there's nothing parseable.
    """
    if not text:
        return None
    cleaned = re.sub(r"^\s*```(?:json)?|```\s*$", "", text.strip(), flags=re.I | re.M).strip()
    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _schema_prompt(prompt: str, schema: dict) -> str:
    """The prompt plus the shape we need back. This is what stands in for schema
    enforcement on the cloud, so it is deliberately blunt about wanting only JSON."""
    return (
        f"{prompt}\n\n"
        "Reply with a single JSON object and nothing else — no explanation, no "
        "markdown fence. It must match this JSON Schema exactly, including every "
        "required field:\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )


class OllamaTextModel:
    """Ollama's chat endpoint behind the same interface as the Google model.

    `generate_json_with_media` is deliberately absent from the useful set: reels are
    understood by watching the clip, and the cloud text models don't take video. The
    caller routes media to Google (ai/__init__), so it raises here rather than
    pretending.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = DEFAULT_URL,
        timeout: float = 120.0,
    ) -> None:
        if not model:
            raise OllamaError("no Ollama model chosen — set one in Admin → AI")
        base = (base_url or DEFAULT_URL).rstrip("/")
        # A local Ollama needs no key; the cloud does. Don't force one on a LAN server.
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self.model = model
        self._local = "ollama.com" not in base
        self._client = httpx.AsyncClient(base_url=base, timeout=timeout, headers=headers)

    async def generate_json(self, prompt: str, schema: dict) -> dict:
        """A filled-in form, whatever it takes: ask for JSON, and if the reply comes
        back unusable, say so plainly once more before giving up on this provider."""
        last = ""
        for attempt in range(2):
            text = await self._chat(_schema_prompt(prompt, schema), schema, attempt)
            parsed = extract_json(text)
            if parsed is not None:
                missing = [k for k in _required_keys(schema) if k not in parsed]
                if not missing:
                    return parsed
                last = f"missing fields {missing}"
                log.warning("ollama %s: %s, asking again", self.model, last)
            else:
                last = f"reply wasn't JSON: {text[:160]}"
                log.warning("ollama %s: %s", self.model, last)
        raise OllamaError(f"{self.model}: {last}")

    async def generate_json_with_media(
        self, prompt: str, schema: dict, mime_type: str, data_base64: str
    ) -> dict:
        raise OllamaError("Ollama isn't used for audio or video here — that goes to Google")

    async def _chat(self, prompt: str, schema: dict, attempt: int) -> str:
        payload: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            # Deterministic-ish: we want the same card described the same way twice.
            "options": {"temperature": 0.2},
        }
        # A local server enforces the schema properly; the cloud ignores/refuses it,
        # so there we lean on the prompt alone.
        if self._local:
            payload["format"] = schema
        elif attempt:
            payload["format"] = "json"  # second try: at least ask for JSON mode
        response = await self._client.post("/api/chat", json=payload)
        if response.status_code >= 400:
            raise OllamaError(f"{self.model}: HTTP {response.status_code} {response.text[:200]}")
        data = response.json()
        return (data.get("message") or {}).get("content", "") or ""

    async def aclose(self) -> None:
        await self._client.aclose()
