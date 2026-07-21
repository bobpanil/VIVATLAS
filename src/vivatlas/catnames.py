"""Auto-translation of category-folder names into three languages.

Create a folder in any language — the other two are filled in via AI (the same one
that describes cards). No Google AI key — we just repeat the entered name in
all languages; the catalogue works, and translations appear as soon as the key is
entered (like with email). Translation is an optional luxury, we mustn't crash over it.
"""

import asyncio
import json
import logging

from vivatlas.ai import build_text_model
from vivatlas.config import settings

log = logging.getLogger(__name__)

_LANGS = ("en", "ru", "he")
_SCHEMA = {
    "type": "object",
    "properties": {c: {"type": "string"} for c in _LANGS},
    "required": list(_LANGS),
}


def translate_category_name(name: str) -> str:
    """JSON string {en,ru,he} for the name. No key or an error — the original name
    in all three languages: the catalogue must not break because of translation."""
    name = (name or "").strip()
    fallback = json.dumps({c: name for c in _LANGS}, ensure_ascii=False)
    if not name or not settings.google_api_key:
        return fallback

    async def _run() -> dict:
        model = build_text_model()
        try:
            prompt = (
                "Translate this short catalogue folder name into English, Russian "
                "and Hebrew. Keep it a short noun phrase, no quotes, no explanation. "
                f"Name: {name}"
            )
            r = await model.generate_json(prompt, _SCHEMA)
            return {c: (str(r.get(c) or name)).strip() or name for c in _LANGS}
        finally:
            await model.aclose()

    try:
        return json.dumps(asyncio.run(_run()), ensure_ascii=False)
    except Exception as exc:
        log.warning("category name %r failed to translate: %s", name, exc)
        return fallback


def label(names_json: str | None, name: str, lang: str) -> str:
    """Folder name in the requested language. No translation — the original name."""
    if names_json:
        try:
            got = json.loads(names_json).get(lang)
            if got:
                return got
        except (ValueError, AttributeError):
            pass
    return name
