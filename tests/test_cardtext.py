"""A card is read as a whole, so it travels as a whole: name and descriptions in the
reader's language, with the vocabulary the catalogue is about left in English."""

import json

import pytest

from vivatlas import cardtext


class _Model:
    """A model that hands back whatever it was told to, and remembers the prompt."""

    model = "fake"

    def __init__(self, answer, fail: bool = False):
        self.answer = answer
        self.fail = fail
        self.prompt = ""

    async def generate_json(self, prompt, schema):
        self.prompt = prompt
        if self.fail:
            raise RuntimeError("model said no")
        return self.answer

    async def aclose(self):
        return None


class _Card:
    def __init__(self, **kw):
        self.name = kw.get("name", "")
        self.summary_short = kw.get("summary_short", "")
        self.summary_normal = kw.get("summary_normal", "")
        self.summary_technical = kw.get("summary_technical", "")
        self.translations_json = kw.get("translations_json", "")


def _answer(**over):
    base = {
        lang: {
            "name": f"name-{lang}",
            "summary_short": f"short-{lang}",
            "summary_normal": f"normal-{lang}",
            "summary_technical": f"tech-{lang}",
        }
        for lang in cardtext.LANGS
    }
    base.update(over)
    return base


# --- writing the translations ----------------------------------------------


async def test_a_card_comes_back_in_all_three_languages():
    model = _Model(_answer())
    stored = await cardtext.translate_card(model, "Whisper", "speech to text", "n", "t")
    got = json.loads(stored)
    assert set(got) == set(cardtext.LANGS)
    assert got["he"]["name"] == "name-he"
    assert got["ru"]["summary_short"] == "short-ru"


async def test_the_prompt_protects_the_vocabulary():
    """Names and terms of art must survive translation, or a searchable term becomes a
    guess at one."""
    model = _Model(_answer())
    await cardtext.translate_card(model, "GitHub Actions", "s", "n", "t")
    for term in ("GitHub", "skill", "agent", "MCP", "API"):
        assert term in model.prompt


async def test_an_empty_field_is_not_invented():
    model = _Model(_answer())
    await cardtext.translate_card(model, "Whisper", "", "", "")
    assert "short description: \n" in model.prompt


async def test_a_blanked_field_falls_back_to_the_original():
    """A translation that drops a field must not empty the card."""
    model = _Model(_answer(ru={"name": "", "summary_short": "", "summary_normal": "",
                               "summary_technical": ""}))
    stored = await cardtext.translate_card(model, "Whisper", "speech to text", "n", "t")
    got = json.loads(stored)
    assert got["ru"]["name"] == "Whisper"
    assert got["ru"]["summary_short"] == "speech to text"


async def test_nothing_to_say_asks_nothing():
    model = _Model(_answer())
    assert await cardtext.translate_card(model, "", "", "", "") == ""
    assert model.prompt == ""


async def test_a_refusal_leaves_the_card_alone():
    """Translation is a luxury — it must never be why a card is lost."""
    card = _Card(name="Whisper", summary_short="speech to text")
    await cardtext.fill_translations(_Model(None, fail=True), card)
    assert card.translations_json == ""
    # No model at all is fine too.
    await cardtext.fill_translations(None, card)
    assert card.translations_json == ""


async def test_fill_translations_writes_onto_the_card():
    card = _Card(name="Whisper", summary_short="speech to text")
    await cardtext.fill_translations(_Model(_answer()), card)
    assert json.loads(card.translations_json)["he"]["name"] == "name-he"


# --- reading them back ------------------------------------------------------


@pytest.mark.parametrize("lang,expected", [("en", "name-en"), ("ru", "name-ru"), ("he", "name-he")])
def test_the_card_is_read_in_the_asked_for_language(lang, expected):
    card = _Card(name="Whisper", summary_short="s",
                 translations_json=json.dumps(_answer(), ensure_ascii=False))
    assert cardtext.localized(card, lang)["name"] == expected


def test_without_a_translation_the_card_keeps_its_own_words():
    card = _Card(name="Whisper", summary_short="speech to text")
    for lang in cardtext.LANGS:
        got = cardtext.localized(card, lang)
        assert got["name"] == "Whisper"
        assert got["summary_short"] == "speech to text"


def test_broken_or_partial_translations_never_blank_the_card():
    card = _Card(name="Whisper", summary_short="speech to text",
                 translations_json="{not json at all")
    assert cardtext.localized(card, "ru")["name"] == "Whisper"

    # A language present but missing a field falls back for that field only.
    partial = {"ru": {"name": "Шёпот"}}
    card.translations_json = json.dumps(partial, ensure_ascii=False)
    got = cardtext.localized(card, "ru")
    assert got["name"] == "Шёпот"
    assert got["summary_short"] == "speech to text"

    # A language that was never stored falls back wholesale.
    assert cardtext.localized(card, "he")["name"] == "Whisper"
