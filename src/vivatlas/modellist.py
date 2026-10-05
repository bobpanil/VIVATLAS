"""The models an admin can choose from, kept by the server and refreshed by itself.

The AI tab used to fill its dropdowns from the browser after the page had loaded,
which left the app's custom dropdown showing a single model, and the Ollama model had
to be typed by hand. Now the server asks Google and Ollama which models exist, keeps
the answer in the settings table, refreshes it in the background every few hours (and
whenever the keys or the Ollama address change), and the page lists every model from
the moment it opens.

A failed refresh never empties a list: the provider that failed keeps the models it
had last time, and its error is kept beside them so the page can say what went wrong.
"""

import json
import logging
import re
from datetime import UTC, datetime, timedelta

import httpx

from vivatlas import runtime_settings
from vivatlas.config import settings
from vivatlas.db import session_scope

log = logging.getLogger(__name__)

KEY = "models_catalog"  # one JSON document in the settings table
REFRESH_EVERY = timedelta(hours=6)

_EMPTY = {
    "text": [],
    "embedding": [],
    "ollama": [],
    "checked_at": "",
    "errors": {},
}


# What each dropdown should offer. Google lists every model a key can reach: music
# (Lyria), images (Nano Banana, Imagen), video (Veo), speech, live voice, robotics,
# agents. None of those can write a card. A description needs a general Gemini text
# model (it reads the card's text, pictures, video and audio and answers in JSON);
# search needs an embedding model; Ollama's list loses its embedding models.
_TEXT_SKIP = (
    "image",
    "tts",
    "audio",
    "live",
    "robotics",
    "computer-use",
    "transcribe",
    "translate",
    "customtools",
    "omni",
    "embedding",
)


def suitable_text(name: str) -> bool:
    n = name.lower()
    return n.startswith("gemini-") and not any(word in n for word in _TEXT_SKIP)


def suitable_embedding(name: str) -> bool:
    return "embedding" in name.lower()


def suitable_ollama(name: str) -> bool:
    return "embed" not in name.lower()


def _newest_first(names: list[str]) -> list[str]:
    """The "-latest" aliases first, then by version, newest at the top."""

    def key(name: str):
        n = name.lower()
        m = re.search(r"(\d+(?:\.\d+)?)", n)
        version = float(m.group(1)) if m else -1.0
        return (0 if n.endswith("-latest") else 1, -version, n)

    return sorted(set(names), key=key)


def for_dropdowns(data: dict) -> dict:
    """The lists as the AI tab shows them: only models fit for each job."""
    return {
        **data,
        "text": _newest_first([n for n in data.get("text", []) if suitable_text(n)]),
        "embedding": _newest_first(
            [n for n in data.get("embedding", []) if suitable_embedding(n)]
        ),
        "ollama": sorted(n for n in data.get("ollama", []) if suitable_ollama(n)),
    }


def cached() -> dict:
    """What the server knows right now, without asking anyone."""
    with session_scope() as session:
        raw = runtime_settings.get(session, KEY, "")
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    out = {**_EMPTY, **{k: v for k, v in data.items() if k in _EMPTY}}
    out["errors"] = dict(out.get("errors") or {})
    return out


def is_stale(data: dict) -> bool:
    stamp = data.get("checked_at") or ""
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return datetime.now(UTC) - when > REFRESH_EVERY


def mark_stale() -> None:
    """The keys or the Ollama address changed: the next look refreshes the lists."""
    data = cached()
    data["checked_at"] = ""
    _store(data)


def _store(data: dict) -> None:
    with session_scope() as session:
        runtime_settings.set(session, KEY, json.dumps(data, ensure_ascii=False))


async def list_ollama(
    base_url: str,
    api_key: str = "",
    timeout: float = 20.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[str]:
    """Model names from an Ollama server's /api/tags: the models on a self-hosted
    server, or the cloud models on ollama.com (the key is sent when there is one)."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("no Ollama address")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, transport=transport
    ) as client:
        r = await client.get(f"{base}/api/tags", headers=headers)
        r.raise_for_status()
        models = r.json().get("models") or []
    names = {(m.get("name") or m.get("model") or "").strip() for m in models}
    return sorted(n for n in names if n)


async def refresh() -> dict:
    """Ask Google and Ollama which models exist and keep the answer."""
    data = cached()
    errors: dict[str, str] = {}

    if settings.google_api_key:
        from vivatlas.ai.google import list_available_models

        try:
            google = await list_available_models(
                settings.google_api_key, settings.http_timeout_seconds
            )
            data["text"] = google.get("text", [])
            data["embedding"] = google.get("embedding", [])
        except Exception as exc:  # noqa: BLE001 — keep the last good list, say why
            errors["google"] = str(exc)[:200]
            log.warning("model list: Google failed: %s", exc)
    else:
        errors["google"] = "no-key"

    if settings.ollama_url:
        try:
            data["ollama"] = await list_ollama(
                settings.ollama_url, settings.ollama_api_key, settings.http_timeout_seconds
            )
        except Exception as exc:  # noqa: BLE001
            errors["ollama"] = str(exc)[:200]
            log.warning("model list: Ollama failed: %s", exc)

    data["errors"] = errors
    data["checked_at"] = datetime.now(UTC).isoformat()
    _store(data)
    log.info(
        "model list: %d Google text, %d Google embedding, %d Ollama",
        len(data["text"]),
        len(data["embedding"]),
        len(data["ollama"]),
    )
    return data
