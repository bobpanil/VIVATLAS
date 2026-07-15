from skill_atlas import purposes
from skill_atlas.purposes import detect

# --- настоящие карточки из каталога ---


def test_security_scanner():
    p, _ = detect(
        ["security-scanning", "github-actions", "typescript", "static-analysis", "codeql"],
        "crgr-security-scanners",
    )
    assert p.key == "security"


def test_research_skill():
    p, _ = detect(["data-gathering", "trend-analysis", "python"], "last30days")
    assert p.key == "research"


def test_design_kit():
    p, _ = detect(["design-system", "typography", "color-palette", "ui-kit"], "airbnb")
    assert p.key == "design"


# --- имя весит больше тегов ---


def test_name_beats_tags():
    # Настоящий случай: по тегам выходила "проверка" — playwright и web-testing
    # перевесили. Но доступность у инструмента прямо в названии.
    tags = ["playwright", "web-testing", "html-auditing", "web-accessibility", "nodejs"]
    assert detect(tags, "site-accessibility-auditor")[0].key == "accessibility"
    # без имени действительно выходит проверка — значит имя и решило
    assert detect(tags)[0].key == "testing"


def test_performance_auditor():
    p, _ = detect(["lighthouse", "seo-audit", "web-performance"], "site-performance-seo-auditor")
    assert p.key == "performance"


# --- не гадаем ---


def test_single_tag_is_not_enough():
    # Одно совпадение — случайность. Настоящий случай:
    # site-unused-items-auditor выходил "безопасностью" по одному тегу.
    p, score = detect(["static-analysis"], "site-unused-items-auditor")
    assert p.key == "unknown"
    assert score == 1


def test_no_tags_no_purpose():
    p, score = detect([], "whatever")
    assert p.key == "unknown"
    assert score == 0


def test_unrelated_tags_give_unknown():
    p, _ = detect(["сemething", "weird", "unrelated"], "mystery-box")
    assert p.key == "unknown"


def test_two_tags_are_enough():
    p, score = detect(["web-accessibility", "wcag-compliance"], "thing")
    assert p.key == "accessibility"
    assert score == 2


# --- устройство ---


def test_every_purpose_has_an_icon_and_label():
    for p in purposes.all_purposes() + [purposes.UNKNOWN]:
        assert p.label and p.icon
        assert "<" in p.icon  # это разметка, а не текст


def test_purpose_keys_are_unique():
    keys = [p.key for p in purposes.all_purposes()]
    assert len(keys) == len(set(keys))


def test_design_is_last_because_it_is_the_widest():
    # "Оформление" подошло бы почти всему, поэтому при равном счёте должно
    # уступать узким направлениям.
    assert purposes.all_purposes()[-1].key == "design"
    p, _ = detect(["typography", "web-accessibility", "wcag-compliance", "css"], "x")
    assert p.key == "accessibility"  # 2 против 2, но доступность уже


def test_detection_is_stable():
    tags = ["design-system", "typography", "playwright"]
    assert detect(tags, "x")[0].key == detect(tags, "x")[0].key
