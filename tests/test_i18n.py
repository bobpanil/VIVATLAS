"""Три языка: нормализация, направление, подстановка, перевод категорий."""
import json

from vivatlas import catnames, i18n
from vivatlas.config import settings


def test_lang_normalize():
    assert i18n.normalize("en") == "en"
    assert i18n.normalize("RU") == "ru"
    assert i18n.normalize("he-IL") == "he"  # берём первые две буквы
    assert i18n.normalize("zz") == "en"  # неизвестный -> английский
    assert i18n.normalize(None) == "en"
    assert i18n.normalize("") == "en"


def test_dir_for():
    assert i18n.dir_for("he") == "rtl"
    assert i18n.dir_for("en") == "ltr"
    assert i18n.dir_for("ru") == "ltr"
    assert i18n.dir_for("zz") == "ltr"


def test_translate_fallback_chain():
    assert i18n.translate("nav.catalog", "en") == "Catalogue"
    assert i18n.translate("nav.catalog", "ru") == "Каталог"
    assert i18n.translate("nav.catalog", "he") == "קטלוג"
    # нет такого языка в записи -> английский
    assert i18n.translate("nav.catalog", "zz") == "Catalogue"
    # нет ключа -> сам ключ (в разработке сразу видно пропажу)
    assert i18n.translate("no.such.key", "en") == "no.such.key"


def test_translate_format():
    out = i18n.translate("foot.counts", "en", cards=5, tags=2)
    assert "5" in out and "2" in out


def test_catnames_label():
    j = json.dumps({"en": "Design", "ru": "Дизайн", "he": "עיצוב"})
    assert catnames.label(j, "Дизайн", "he") == "עיצוב"
    assert catnames.label(j, "Дизайн", "en") == "Design"
    assert catnames.label("", "Дизайн", "he") == "Дизайн"  # нет перевода -> исходное
    assert catnames.label("не json", "Дизайн", "he") == "Дизайн"  # мусор -> исходное


def test_catnames_translate_fallback_without_key(monkeypatch):
    # Нет ключа Google — имя во всех трёх языках, без падения.
    monkeypatch.setattr(settings, "google_api_key", "")
    j = catnames.translate_category_name("Дизайн")
    assert json.loads(j) == {"en": "Дизайн", "ru": "Дизайн", "he": "Дизайн"}
