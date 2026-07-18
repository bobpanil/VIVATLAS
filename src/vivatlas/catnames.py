"""Автоперевод названий категорий-папок на три языка.

Завели папку на любом языке — остальные два заполняем через ИИ (тот же, что
описывает карточки). Нет ключа Google AI — просто повторяем введённое имя во
всех языках; каталог работает, а переводы появятся, как только ключ впишут
(как с почтой). Перевод — необязательная роскошь, ронять из-за него нельзя.
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
    """JSON-строка {en,ru,he} для названия. Нет ключа или ошибка — исходное имя
    во всех трёх языках: каталог не должен ломаться из-за перевода."""
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
        log.warning("название категории %r не перевелось: %s", name, exc)
        return fallback


def label(names_json: str | None, name: str, lang: str) -> str:
    """Название папки на нужном языке. Нет перевода — исходное name."""
    if names_json:
        try:
            got = json.loads(names_json).get(lang)
            if got:
                return got
        except (ValueError, AttributeError):
            pass
    return name
