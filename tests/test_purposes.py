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


# --- always an answer ---


def test_single_tag_decides():
    # One match is enough now. It can be a coincidence — site-unused-items-auditor
    # comes out "security" off one tag — and the card's own picker is the correction.
    p, score = detect(["static-analysis"], "site-unused-items-auditor")
    assert p.key != "unknown"
    assert score == 1


def test_no_tags_falls_back_to_the_kind_of_card():
    # Nothing to read from tags or name, so the type answers instead.
    assert detect([], "whatever", "design-kit")[0].key == "design"
    assert detect([], "whatever", "page")[0].key == "research"
    assert detect([], "whatever", "project")[0].key == "code"
    # Skills, agents, commands, plugins, MCP servers all do work.
    assert detect([], "whatever", "claude-agent")[0].key == "automation"


def test_unrelated_tags_still_get_a_purpose():
    p, _ = detect(["something", "weird", "unrelated"], "mystery-box", "project")
    assert p.key == "code"


def test_nothing_is_ever_undetermined():
    """The whole point: no combination of inputs answers "undetermined"."""
    types = ["design-kit", "claude-skill", "skill", "claude-agent", "mcp-server",
             "plugin", "project", "page", "unknown", ""]
    tagsets = [[], ["static-analysis"], ["something", "weird"], ["wcag"]]
    for atype in types:
        for tags in tagsets:
            for name in ("", "mystery-box"):
                assert detect(tags, name, atype)[0].key != "unknown"


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
