"""A card in all three languages — its name as well as its descriptions.

Switching the interface to English and finding a Russian title over English prose is
incoherent, so the whole card travels together: name, and the three descriptions.
Written once when the card is described, not on the way to the screen — the reader
waits for nothing, and the AI is asked once per card rather than once per viewer.

What does NOT get translated is the vocabulary this catalogue is about. Names stay
names — GitHub is GitHub in Hebrew — and so do the words used here as terms of art
(skills, agents, MCP), anything that is code, and file paths. Translating those would
turn a searchable term into a guess at one, which is worse than leaving it in English.

Translation is a luxury: without a model, or when the model fails, the card keeps the
text it already has in every language. It must never be the reason a card is lost.
"""

import json
import logging

from vivatlas.ai.base import TextModel

log = logging.getLogger(__name__)

LANGS = ("en", "ru", "he")
FIELDS = ("name", "summary_short", "summary_normal", "summary_technical")

_ONE = {
    "type": "object",
    "properties": {f: {"type": "string"} for f in FIELDS},
    "required": list(FIELDS),
}
SCHEMA = {
    "type": "object",
    "properties": {lang: _ONE for lang in LANGS},
    "required": list(LANGS),
}

_PROMPT = """Below is one card from a catalogue of developer tools, in whatever
language it was written. Give it back in all three languages: English, Russian and
Hebrew.

Leave in English, exactly as written, and do NOT translate:
- names of products, tools, companies and repositories (GitHub, Docker, Figma, Playwright)
- the catalogue's own terms of art: skill, skills, agent, agents, MCP, prompt, repository
- anything that is code: identifiers, commands, flags, file names, paths, extensions
- abbreviations that are read as such: API, CLI, SDK, UI, CSS, HTML, JSON, WCAG, AI

Translate the prose around them. A sentence in Hebrew that keeps "GitHub Actions" in
English is correct; a sentence that renders it in Hebrew letters is wrong.

Keep each field the same KIND of thing it already is: name stays a short title, not a
sentence. If a field is empty, return it empty — don't invent one. Where the text is
already in the target language, return it unchanged rather than paraphrasing it.

name: {name}
short description: {summary_short}
description: {summary_normal}
technical description: {summary_technical}"""


def _clean(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


async def translate_card(
    model: TextModel,
    name: str,
    summary_short: str = "",
    summary_normal: str = "",
    summary_technical: str = "",
) -> str:
    """The card in en/ru/he as a JSON string, ready for `Artifact.translations_json`.

    Returns "" when there's nothing worth asking about or the model can't answer —
    the caller then simply shows the text it already has.
    """
    if not (name or summary_short or summary_normal or summary_technical):
        return ""
    prompt = _PROMPT.format(
        name=name or "",
        summary_short=summary_short or "",
        summary_normal=summary_normal or "",
        summary_technical=summary_technical or "",
    )
    source = {
        "name": name or "",
        "summary_short": summary_short or "",
        "summary_normal": summary_normal or "",
        "summary_technical": summary_technical or "",
    }
    try:
        answer = await model.generate_json(prompt, SCHEMA)
    except Exception as exc:  # noqa: BLE001 — a card without translations is still a card
        log.warning("card %r didn't translate: %s", name[:60], exc)
        return ""
    out: dict[str, dict[str, str]] = {}
    for lang in LANGS:
        got = answer.get(lang)
        if not isinstance(got, dict):
            continue
        # Never let a translation blank a field that had text: fall back per field.
        out[lang] = {f: _clean(got.get(f), source[f]) for f in FIELDS}
    return json.dumps(out, ensure_ascii=False) if out else ""


async def fill_translations(model: TextModel | None, artifact) -> None:
    """Translate a card that has just been described, in place. Best-effort by design:
    a missing model or a refusal leaves the card exactly as it was."""
    if model is None:
        return
    stored = await translate_card(
        model,
        artifact.name or "",
        artifact.summary_short or "",
        artifact.summary_normal or "",
        artifact.summary_technical or "",
    )
    if stored:
        artifact.translations_json = stored


def localized(artifact, lang: str) -> dict:
    """The card's name and descriptions in this language, falling back field by field
    to what the card was written with. Always returns every field, so callers can use
    it without checking."""
    base = {
        "name": artifact.name or "",
        "summary_short": artifact.summary_short or "",
        "summary_normal": artifact.summary_normal or "",
        "summary_technical": artifact.summary_technical or "",
    }
    raw = getattr(artifact, "translations_json", "") or ""
    if not raw or lang not in LANGS:
        return base
    try:
        got = json.loads(raw).get(lang)
    except (ValueError, AttributeError):
        return base
    if not isinstance(got, dict):
        return base
    return {f: (str(got.get(f) or "").strip() or base[f]) for f in FIELDS}
