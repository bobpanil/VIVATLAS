from vivatlas import purposes
from vivatlas.purposes import detect

# --- real cards from the catalogue ---


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


# --- name outweighs tags ---


def test_name_beats_tags():
    # Real case: by tags it came out as "testing" — playwright and web-testing
    # tipped the balance. But accessibility is right there in the name.
    tags = ["playwright", "web-testing", "html-auditing", "web-accessibility", "nodejs"]
    assert detect(tags, "site-accessibility-auditor")[0].key == "accessibility"
    # without the name it really does come out as testing — so the name is what decided it
    assert detect(tags)[0].key == "testing"


def test_performance_auditor():
    p, _ = detect(["lighthouse", "seo-audit", "web-performance"], "site-performance-seo-auditor")
    assert p.key == "performance"


# --- no guessing ---


def test_single_tag_is_not_enough():
    # A single match is a coincidence. Real case:
    # site-unused-items-auditor came out as "security" from one tag.
    p, score = detect(["static-analysis"], "site-unused-items-auditor")
    assert p.key == "unknown"
    assert score == 1


def test_no_tags_no_purpose():
    p, score = detect([], "whatever")
    assert p.key == "unknown"
    assert score == 0


def test_unrelated_tags_give_unknown():
    p, _ = detect(["something", "weird", "unrelated"], "mystery-box")
    assert p.key == "unknown"


def test_two_tags_are_enough():
    p, score = detect(["web-accessibility", "wcag-compliance"], "thing")
    assert p.key == "accessibility"
    assert score == 2


# --- internals ---


def test_every_purpose_has_an_icon_and_label():
    for p in purposes.all_purposes() + [purposes.UNKNOWN]:
        assert p.label and p.icon
        assert "<" in p.icon  # it's markup, not text


def test_purpose_keys_are_unique():
    keys = [p.key for p in purposes.all_purposes()]
    assert len(keys) == len(set(keys))


def test_design_is_last_because_it_is_the_widest():
    # "Design" would fit almost anything, so on a tie it should
    # yield to narrower purposes.
    assert purposes.all_purposes()[-1].key == "design"
    p, _ = detect(["typography", "web-accessibility", "wcag-compliance", "css"], "x")
    assert p.key == "accessibility"  # 2 vs 2, but accessibility is narrower


def test_detection_is_stable():
    tags = ["design-system", "typography", "playwright"]
    assert detect(tags, "x")[0].key == detect(tags, "x")[0].key
