"""The picture on a card: what gets picked, and what gets thrown out.

Nearly all of the value here is in the throwing out. A README's first images are
almost always shields, sponsor logos and subscribe buttons, so a naive "first
image wins" gives a catalogue of build-status pills — which is worse than the
grey box it replaced, because it looks deliberate.
"""

import io

import pytest

from vivatlas import previews

# --- what is not a picture -------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://img.shields.io/badge/PRs-welcome-brightgreen.svg",
        "https://img.shields.io/twitter/follow/someone?style=social",
        "https://badgen.net/badge/license/MIT",
        "https://star-history.dera.page/svg?repos=a/b&type=Date",
        "https://contrib.rocks/image?repo=a/b",
        "images/free-module-button.svg",
        "assets/subscribe-badge.png",
        "https://github.com/o/r/actions/workflows/ci.yml/badge.svg",
    ],
)
def test_furniture_is_not_a_face(url):
    assert previews.is_badge(url)


@pytest.mark.parametrize(
    "url",
    ["https://i.postimg.cc/kG03s7tk/prompt-banner.png", "assets/logo.png", "docs/hero.jpg"],
)
def test_a_real_picture_survives(url):
    assert not previews.is_badge(url)


def test_alt_text_naming_the_project_is_not_a_badge():
    """"MIT License" is a badge; "Prompt Master" is the thing itself. Without this
    the word "license" in an alt would throw away a perfectly good banner."""
    assert previews.is_badge("x/y.png", alt="MIT License")
    assert not previews.is_badge("x/y.png", alt="Prompt Master")


def test_the_author_s_own_size_is_believed():
    """A sponsors table carries real logos on real hosts with innocent filenames —
    nothing else here catches them. But it renders them 28px tall, and nobody sizes
    a hero image at 28px."""
    md = (
        '<img src="images/contextual.png" alt="Contextual AI" style="height: 28px;">\n'
        '<img src="images/banner.png" alt="The project">'
    )
    assert previews.image_candidates(md) == ["images/banner.png"]


# --- what does get picked --------------------------------------------------


def test_the_readme_is_read_in_order():
    """The banner is at the top; that is the whole reason this walks the document
    rather than choosing a "best" image."""
    md = (
        "![build](https://img.shields.io/badge/build-passing-green)\n"
        "![](https://cdn.example.com/banner.png)\n"
        "![later](https://cdn.example.com/screenshot.png)\n"
    )
    assert previews.image_candidates(md) == [
        "https://cdn.example.com/banner.png",
        "https://cdn.example.com/screenshot.png",
    ]


def test_relative_links_are_resolved_against_the_repository():
    base = "https://raw.githubusercontent.com/o/r/main"
    assert previews.absolute("assets/logo.png", base) == f"{base}/assets/logo.png"
    assert previews.absolute("./assets/logo.png", base) == f"{base}/assets/logo.png"
    assert previews.absolute("https://cdn.example.com/x.png", base) == "https://cdn.example.com/x.png"
    assert previews.absolute("//cdn.example.com/x.png", base) == "https://cdn.example.com/x.png"
    assert previews.absolute("data:image/png;base64,AAAA", base) is None


def test_raw_urls_differ_per_host():
    """The old preview link used Gitea's shape for every source, so every GitHub
    card with a picture pointed at a 404 and showed a broken image."""
    assert previews.raw_base("https://github.com/o/r", "main") == (
        "https://raw.githubusercontent.com/o/r/main"
    )
    assert previews.raw_base("https://git.example.com/o/r", "dev") == (
        "https://git.example.com/o/r/raw/branch/dev"
    )


def test_the_host_card_is_the_last_resort_not_the_first():
    """A project's own banner beats the social card the host draws for it — but a
    card wearing the host's is still that project, and not a grey box."""
    md = "![](https://cdn.example.com/banner.png)"
    base = "https://raw.githubusercontent.com/o/r/main"
    og = "https://opengraph.githubassets.com/1/o/r"

    assert previews.choose(md, [], base, og)[0] == "https://cdn.example.com/banner.png"
    assert previews.choose("", [], base, og) == [og]


def test_a_named_asset_is_used_when_the_readme_has_none():
    base = "https://raw.githubusercontent.com/o/r/main"
    got = previews.choose("", ["src/main.py", "assets/logo.png"], base)
    assert got[0] == f"{base}/assets/logo.png"


# --- turning it into the card's picture ------------------------------------


def _png(w: int, h: int, colour=(10, 20, 30)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, format="PNG")
    return buf.getvalue()


def test_a_wide_banner_keeps_its_ends():
    """Cropping to fill was the first attempt and it sliced the ends off the host's
    2:1 card — "NVIDIA/SkillSpector" arrived as "killSpector". Half a title is worse
    than a smaller whole one, so the picture is fitted and padded instead."""
    from PIL import Image

    out = previews.to_card_webp(_png(1280, 640))
    assert out
    im = Image.open(io.BytesIO(out))
    assert im.size == (previews.CARD_W, previews.CARD_H)


def test_the_result_is_always_the_card_s_shape():
    """The card box is 16:10 with object-fit: cover. Anything else stored here
    would be cropped a second time by the browser, undoing the fitting above."""
    from PIL import Image

    for w, h in [(1280, 640), (600, 900), (700, 438)]:
        out = previews.to_card_webp(_png(w, h))
        assert out, (w, h)
        assert Image.open(io.BytesIO(out)).size == (previews.CARD_W, previews.CARD_H)


def test_a_shield_sized_image_is_refused():
    """The net under every name-based rule: whatever the URL claimed, a 100×20
    image is a status pill."""
    assert previews.to_card_webp(_png(110, 20)) is None


def test_rubbish_is_refused_rather_than_raised():
    """A preview is a nicety. Nothing here may take a scan down with it."""
    assert previews.to_card_webp(b"not an image at all") is None
    assert previews.to_card_webp(b"") is None


def test_the_padding_takes_the_picture_s_own_colour():
    """Bars in the picture's own colour read as part of it; grey bars read as a
    frame bolted round it."""
    from PIL import Image

    out = previews.to_card_webp(_png(1200, 300, colour=(12, 24, 36)))
    im = Image.open(io.BytesIO(out)).convert("RGB")
    r, g, b = im.getpixel((previews.CARD_W // 2, 4))  # in the top bar
    assert abs(r - 12) < 24 and abs(g - 24) < 24 and abs(b - 36) < 24
