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


# --- letting a vision model choose ------------------------------------------


class _StubModel:
    """A vision model that answers however the test needs it to."""

    def __init__(self, pick=None, boom=False):
        self.pick, self.boom, self.calls = pick, boom, 0
        self.last_media_len = 0

    async def generate_json_with_media(self, prompt, schema, mime_type, data_base64):
        self.calls += 1
        self.last_media_len = len(data_base64)
        if self.boom:
            raise RuntimeError("rate limited")
        return {"pick": self.pick, "why": "it names the project"}


@pytest.mark.asyncio
async def test_the_model_s_choice_is_used():
    """The rules can only judge an image by its name and its size, and by those a
    sponsor's logo and a product screenshot look the same. Looking is the only way
    to tell them apart, so when the model answers, its answer wins."""
    imgs = [_png(800, 500, (1, 1, 1)), _png(800, 500, (2, 2, 2)), _png(800, 500, (3, 3, 3))]
    model = _StubModel(pick=3)
    assert await previews.pick_with_model(model, imgs, "Thing") == 2
    assert model.calls == 1


@pytest.mark.asyncio
async def test_a_model_that_fails_falls_back_to_document_order():
    """A rate-limited vision model must cost the card its best picture, not its
    picture — and never the scan."""
    imgs = [_png(800, 500), _png(800, 500)]
    assert await previews.pick_with_model(_StubModel(boom=True), imgs, "Thing") == 0


@pytest.mark.asyncio
async def test_an_answer_outside_the_range_is_ignored():
    imgs = [_png(800, 500), _png(800, 500)]
    assert await previews.pick_with_model(_StubModel(pick=9), imgs, "Thing") == 0
    assert await previews.pick_with_model(_StubModel(pick=0), imgs, "Thing") == 0


@pytest.mark.asyncio
async def test_nothing_is_asked_when_there_is_no_choice():
    """One candidate is not a decision. No model, no call, no token spent."""
    model = _StubModel(pick=1)
    assert await previews.pick_with_model(model, [_png(800, 500)], "Thing") == 0
    assert await previews.pick_with_model(None, [_png(800, 500)] * 3, "Thing") == 0
    assert model.calls == 0


@pytest.mark.asyncio
async def test_the_candidates_go_as_one_picture():
    """One image, not one call each: the interface takes a single piece of media,
    and "which of these is best" is a question that only makes sense side by side."""
    from PIL import Image

    imgs = [_png(800, 500), _png(800, 500), _png(800, 500)]
    sheet = previews.contact_sheet(imgs)
    im = Image.open(io.BytesIO(sheet))
    # three tiles, two to a row -> two rows
    assert im.size == (previews.CARD_W * 2, previews.CARD_H * 2)

    model = _StubModel(pick=2)
    await previews.pick_with_model(model, imgs, "Thing")
    assert model.calls == 1  # not three


# --- the filler must keep moving --------------------------------------------


def test_a_page_that_is_not_a_repository_gets_no_raw_base():
    """For a scanned repo or a GitHub link, raw files live somewhere. For ltx.io
    they do not, and hunting for README.md there is five 404s per card per pass."""
    assert previews.raw_base("https://github.com/o/r", "main")
    assert previews.raw_base("https://git.example.com/o/r", "main")
    # the caller decides; but a bare page URL must at least round-trip harmlessly
    assert previews.host_card("https://ltx.io/some/page") == ""


@pytest.mark.asyncio
async def test_the_filler_rotates_rather_than_starving(make_session, monkeypatch):
    """The bug this guards: ordering by "most recently updated" put the same
    twenty link captures — with nothing to find — at the front of every pass,
    so the rest of the catalogue never got a turn. Every attempt is stamped, and
    never-checked cards go first."""
    from datetime import UTC, datetime

    from vivatlas import web
    from vivatlas.models import Artifact, Repository

    session = make_session()
    for i in range(6):
        repo = Repository(
            source_id=1, external_id=f"e{i}", owner="o", name=f"r{i}", default_branch="main",
            html_url="", original_url="https://example.invalid/nothing",
        )
        session.add(repo)
        session.flush()
        session.add(Artifact(repository_id=repo.id, name=f"r{i}", artifact_type="page",
                             updated_at=datetime.now(UTC)))
    session.commit()

    # every attempt finds nothing — the worst case for starvation
    async def nothing(*a, **k):
        return False
    monkeypatch.setattr(previews, "refresh_artifact", nothing)
    monkeypatch.setattr(web, "session_scope", lambda: _Scope(session))

    touched = []
    for _ in range(3):
        before = {a.id for a in session.query(Artifact) if a.preview_checked_at}
        filled, examined = await web.fill_missing_previews(2)
        assert (filled, examined) == (0, 2)
        after = {a.id for a in session.query(Artifact) if a.preview_checked_at}
        touched.append(after - before)

    # three passes of two, six cards: each pass reached two NEW ones
    assert [len(t) for t in touched] == [2, 2, 2]
    assert len(set().union(*touched)) == 6


class _Scope:
    """A session_scope() stand-in that hands back the test's own session."""

    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, *exc):
        return False


# --- an avatar is not a picture; a drawing is the last resort ---------------


@pytest.mark.parametrize(
    "url",
    [
        "https://git.example.com/avatars/6bedb8c7ba5e906d6f85c270fbc63549",
        "https://git.example.com/user/avatar/someone/-1",
        "https://www.gravatar.com/avatar/abc?s=200",
    ],
)
def test_an_avatar_is_never_a_project_picture(url):
    """Gitea's og:image for a repository is the owner's avatar — for an org with
    none set, an identicon. A row of identical green quilts is what accepting it
    produced on a real catalogue."""
    assert previews.is_avatar(url)
    assert previews.is_badge(url)


class _Drawer:
    def __init__(self, png=None, boom=False):
        self.png, self.boom, self.prompts = png, boom, []

    async def generate_image(self, prompt, model):
        self.prompts.append((prompt, model))
        if self.boom:
            raise RuntimeError("HTTP 429 quota exceeded")
        return self.png


class _Art:
    name, artifact_type, summary_short = "output-skill", "claude-skill", "Forces complete outputs."


@pytest.mark.asyncio
async def test_a_card_with_nothing_gets_a_drawing(monkeypatch):
    from vivatlas.config import settings

    monkeypatch.setattr(settings, "image_model", "test-image-model")
    drawer = _Drawer(png=_png(1280, 800))
    webp, src = await previews.generated_picture(drawer, _Art())
    assert webp and src == "generated:test-image-model"
    prompt, model = drawer.prompts[0]
    assert model == "test-image-model"
    assert "output-skill" in prompt and "Forces complete outputs." in prompt
    assert "No text" in prompt  # image models spell badly; a card wears no caption


@pytest.mark.asyncio
async def test_no_quota_means_a_plain_card_not_a_failure(monkeypatch):
    from vivatlas.config import settings

    monkeypatch.setattr(settings, "image_model", "test-image-model")
    assert await previews.generated_picture(_Drawer(boom=True), _Art()) == (None, None)


@pytest.mark.asyncio
async def test_generation_is_off_when_no_drawing_model_is_set(monkeypatch):
    from vivatlas.config import settings

    monkeypatch.setattr(settings, "image_model", "")
    drawer = _Drawer(png=_png(1280, 800))
    assert await previews.generated_picture(drawer, _Art()) == (None, None)
    assert drawer.prompts == []


def test_the_prompt_names_what_the_thing_is():
    p = previews.draw_prompt("redesign-skill", "claude-skill", "Upgrades websites.")
    assert "redesign-skill" in p and "(a claude skill)" in p and "Upgrades websites." in p
    # a captured page has no useful type to announce
    assert "(a page)" not in previews.draw_prompt("x", "page", "y")


@pytest.mark.asyncio
async def test_one_quota_refusal_pauses_drawing_for_the_pass(monkeypatch):
    """A key with no image quota answers every request 429, and each one costs a
    backoff. One refusal is information enough: the second card must not ask."""
    from vivatlas.config import settings

    monkeypatch.setattr(settings, "image_model", "test-image-model")
    monkeypatch.setattr(previews, "_draw_paused_until", 0.0)
    drawer = _Drawer(boom=True)
    await previews.generated_picture(drawer, _Art())
    await previews.generated_picture(drawer, _Art())
    assert len(drawer.prompts) == 1  # asked once, then left alone
    monkeypatch.setattr(previews, "_draw_paused_until", 0.0)  # don't leak into other tests


# --- the designed cover ------------------------------------------------------


def test_a_cover_is_the_card_s_shape_and_deterministic():
    """Same card, same cover — on every render and every machine. That is what
    lets a rescan tell "unchanged" from "changed" by bytes alone."""
    from PIL import Image

    a = previews.cover_image("output-skill", "claude skill", "skills-lib")
    b = previews.cover_image("output-skill", "claude skill", "skills-lib")
    assert a.size == (previews.CARD_W, previews.CARD_H)
    assert a.tobytes() == b.tobytes()
    art = type(
        "A", (), {"name": "output-skill", "artifact_type": "claude-skill", "repository": None}
    )()
    webp = previews.designed_cover(art)
    assert Image.open(io.BytesIO(webp)).size == (previews.CARD_W, previews.CARD_H)


def test_different_cards_get_different_grounds():
    """A catalogue of one colour is the identicon problem in a new coat."""
    names = ["output-skill", "redesign-skill", "minimalist-skill", "site-compatibility-auditor",
             "imagegen-frontend-web", "soft-skill", "taste-skill-v1", "dify"]
    grounds = {previews.cover_image(n).getpixel((4, 4)) for n in names}
    assert len(grounds) >= 3


def test_names_wrap_at_hyphens_never_inside_words():
    """site-compatibility-auditor became "compatibi- / lity" when breaks were
    allowed anywhere. Only spaces and hyphens are break points now."""
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    for name in ("site-compatibility-auditor", "imagegen-frontend-web", "minimalist-skill"):
        _, lines, _ = previews._fit_name(draw, name, previews.CARD_W - 96)
        assert "".join(lines) == name, lines  # every break sat on a hyphen
        assert 1 <= len(lines) <= 3


def test_a_very_long_title_still_fits_three_lines():
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    title = "Don't publish the first video until you set this up – the video explains everything"
    font, lines, _ = previews._fit_name(draw, title, previews.CARD_W - 96)
    assert len(lines) <= 3
    assert all(draw.textlength(ln, font=font) <= previews.CARD_W - 96 for ln in lines)


def test_the_bundled_face_is_used():
    """Plex is bundled so the cover looks the same in the container as here — the
    image has no system fonts, and Pillow's built-in face is not a look."""
    assert (previews._FONT_DIR / "IBMPlexSans-Bold.ttf").exists()
    assert (previews._FONT_DIR / "IBMPlexSans-Regular.ttf").exists()


@pytest.mark.asyncio
async def test_a_card_with_nothing_at_all_gets_a_cover(make_session, monkeypatch):
    """The end of the chain: no banner, no logo, no host card, no drawing model —
    the card still gets a picture, and it is marked as ours so a real one wins later."""
    from vivatlas.models import Artifact, Preview, Repository

    session = make_session()
    repo = Repository(source_id=1, external_id="x", owner="skills-lib", name="output-skill",
                      default_branch="main", html_url="", original_url="https://example.invalid/x")
    session.add(repo)
    session.flush()
    art = Artifact(repository_id=repo.id, name="output-skill", artifact_type="claude-skill")
    session.add(art)
    session.flush()

    async def no_page(url):
        return ""
    monkeypatch.setattr(previews, "page_image", no_page)

    assert await previews.refresh_artifact(session, art, model=None)
    assert art.preview_src == "generated:cover"
    assert session.get(Preview, art.id) is not None


def test_the_preview_address_changes_when_the_picture_does():
    """Served with a day's cache, so a replaced picture needs a new address, or
    every browser that saw the old one keeps it until tomorrow."""
    from vivatlas.web import preview_url

    class A:
        id = 7
        preview_src = "https://git.example.com/avatars/abc"

    before = preview_url(A())
    A.preview_src = "generated:cover"
    after = preview_url(A())
    assert before and after and before != after
    assert before.startswith("/preview/7?v=") and after.startswith("/preview/7?v=")
    A.preview_src = None
    assert preview_url(A()) is None


# --- Pollinations: free, keyless, opt-in ---------------------------------------


def test_the_image_model_setting_names_the_drawer():
    from vivatlas.ai import pollinations as pl

    assert pl.is_pollinations("pollinations:flux")
    assert pl.is_pollinations("Pollinations")
    assert not pl.is_pollinations("gemini-3.1-flash-lite-image")
    assert pl.model_name("pollinations:turbo") == "turbo"
    assert pl.model_name("pollinations") == "flux"


@pytest.mark.asyncio
async def test_pollinations_needs_no_model_object(monkeypatch):
    """It is a URL. A catalogue with no AI configured at all can still draw."""
    import respx
    from httpx import Response

    from vivatlas.config import settings

    monkeypatch.setattr(settings, "image_model", "pollinations:flux")
    monkeypatch.setattr(previews, "_draw_paused_until", 0.0)
    with respx.mock(base_url="https://image.pollinations.ai") as mock:
        route = mock.get(path__startswith="/prompt/").mock(
            return_value=Response(
                200, content=_png(1280, 800), headers={"content-type": "image/png"}
            )
        )
        webp, src = await previews.generated_picture(None, _Art())
    assert webp and src == "generated:pollinations:flux"
    assert route.called
    params = route.calls[0].request.url.params
    assert params["model"] == "flux" and params["nologo"] == "true"
    assert params["width"] == "1280" and params["height"] == "800"


@pytest.mark.asyncio
async def test_a_stalled_pollinations_queue_pauses_briefly_and_the_cover_steps_in(monkeypatch):
    import respx
    from httpx import Response

    from vivatlas.config import settings

    monkeypatch.setattr(settings, "image_model", "pollinations:flux")
    monkeypatch.setattr(previews, "_draw_paused_until", 0.0)
    with respx.mock(base_url="https://image.pollinations.ai") as mock:
        route = mock.get(path__startswith="/prompt/").mock(
            return_value=Response(502, text="bad gateway")
        )
        first = await previews.generated_picture(None, _Art())
        second = await previews.generated_picture(None, _Art())
    assert first == (None, None) and second == (None, None)
    assert route.call_count == 1  # paused after the first refusal
    monkeypatch.setattr(previews, "_draw_paused_until", 0.0)


@pytest.mark.asyncio
async def test_google_is_still_the_drawer_for_a_google_model(monkeypatch):
    from vivatlas.config import settings

    monkeypatch.setattr(settings, "image_model", "gemini-3.1-flash-lite-image")
    monkeypatch.setattr(previews, "_draw_paused_until", 0.0)
    drawer = _Drawer(png=_png(1280, 800))
    webp, src = await previews.generated_picture(drawer, _Art())
    assert webp and src == "generated:gemini-3.1-flash-lite-image"
    assert drawer.prompts[0][1] == "gemini-3.1-flash-lite-image"



@pytest.mark.asyncio
async def test_a_short_pass_means_the_work_is_done(make_session, monkeypatch):
    """The loop drains while passes come back full and idles when one comes back
    short. What it reads is the examined count, so that has to be right."""
    from vivatlas import web
    from vivatlas.models import Artifact, Repository

    session = make_session()
    for i in range(3):
        repo = Repository(source_id=1, external_id=f"s{i}", owner="o", name=f"r{i}",
                          default_branch="main", html_url="", original_url="https://x.invalid/")
        session.add(repo)
        session.flush()
        session.add(Artifact(repository_id=repo.id, name=f"r{i}", artifact_type="page"))
    session.commit()

    async def cover(session_, art, **k):  # what really happens: a cover always fills
        art.preview_src = "generated:cover"
        return True
    monkeypatch.setattr(previews, "refresh_artifact", cover)
    monkeypatch.setattr(web, "session_scope", lambda: _Scope(session))

    assert await web.fill_missing_previews(2) == (2, 2)   # full: more behind it
    assert await web.fill_missing_previews(2) == (1, 1)   # short: that was the last
    assert await web.fill_missing_previews(2) == (0, 0)   # nothing left: idle


@pytest.mark.asyncio
async def test_idle_passes_give_a_paused_drawer_another_go_at_covers(make_session, monkeypatch):
    """A cover is what a card wears when drawing was down — not a verdict. Once a
    drawer is configured, idle passes revisit cover-wearing cards; busy ones don't."""
    from vivatlas import web
    from vivatlas.config import settings
    from vivatlas.models import Artifact, Repository

    session = make_session()
    repo = Repository(source_id=1, external_id="c", owner="o", name="r", default_branch="main",
                      html_url="", original_url="https://x.invalid/")
    session.add(repo)
    session.flush()
    session.add(Artifact(repository_id=repo.id, name="r", artifact_type="page",
                         preview_src="generated:cover"))
    session.commit()
    seen = []

    async def note(session_, art, **k):
        seen.append(art.name)
        return False
    monkeypatch.setattr(previews, "refresh_artifact", note)
    monkeypatch.setattr(web, "session_scope", lambda: _Scope(session))
    monkeypatch.setattr(settings, "image_model", "pollinations:flux")

    # a busy pass leaves covers alone; an idle one tries them again
    assert await web.fill_missing_previews(5) == (0, 0)
    assert await web.fill_missing_previews(5, retry_covers=True) == (0, 1)
    assert seen == ["r"]

    monkeypatch.setattr(settings, "image_model", "")
    # and with no drawer configured there is nothing to retry them with
    assert await web.fill_missing_previews(5, retry_covers=True) == (0, 0)


# --- the loop's pacing --------------------------------------------------------


def test_the_first_idle_pass_follows_at_once_not_in_fifteen_minutes():
    """A full catalogue must start drawing over its covers immediately after the
    blank pass comes back empty — the first attempt waited a quarter-hour."""
    from vivatlas import api

    drain, every = api._PREVIEW_DRAIN_SECONDS, api._PREVIEW_EVERY_SECONDS
    assert api._preview_next(False, 20, 20, False) == (False, drain)   # blanks: more behind
    assert api._preview_next(False, 0, 20, False) == (True, drain)     # blanks done: covers NOW
    assert api._preview_next(True, 20, 20, False) == (True, drain)     # covers: more behind
    assert api._preview_next(True, 3, 20, False) == (False, every)     # lap done: rest, then blanks


def test_a_paused_drawer_does_not_churn_covers():
    """Revisiting a cover re-runs the whole hunt. With the drawer paused it would
    fetch every README every few seconds to get the same cover back."""
    from vivatlas import api

    assert api._preview_next(True, 20, 20, True) == (False, api._PREVIEW_EVERY_SECONDS)
    # …but a paused drawer must not stop BLANK cards from being done
    assert api._preview_next(False, 20, 20, True) == (False, api._PREVIEW_DRAIN_SECONDS)


# --- taking a drawer's work back off --------------------------------------------


def test_only_drawings_are_drawings():
    assert previews.is_drawing("generated:pollinations:flux")
    assert previews.is_drawing("generated:gemini-3.1-flash-lite-image")
    assert not previews.is_drawing("generated:cover")
    assert not previews.is_drawing("https://cdn.example.com/banner.png")
    assert not previews.is_drawing(None)


def test_dropping_drawings_leaves_covers_and_real_pictures_alone(make_session):
    """Turning a drawer off leaves its pictures on the cards, because the loop
    never replaces a picture a card already has. This forgets them — and nothing
    else — so those cards go to the front of the next pass and get a cover."""
    from vivatlas.models import Artifact, Preview, Repository

    session = make_session()
    cards = {}
    for name, src in [("drawn", "generated:pollinations:flux"), ("covered", "generated:cover"),
                      ("real", "https://cdn.example.com/banner.png"), ("blank", None)]:
        repo = Repository(source_id=1, external_id=name, owner="o", name=name,
                          default_branch="main", html_url="", original_url="https://x.invalid/")
        session.add(repo)
        session.flush()  # one card per repository — that is a real constraint
        a = Artifact(repository_id=repo.id, name=name, artifact_type="page", preview_src=src)
        session.add(a)
        session.flush()
        if src:
            session.add(Preview(artifact_id=a.id, webp=b"x"))
        cards[name] = a
    session.commit()

    dropped = previews.drop_drawings(session)
    session.commit()

    assert [a.name for a in dropped] == ["drawn"]
    assert cards["drawn"].preview_src is None
    assert cards["drawn"].preview_checked_at is None
    assert session.get(Preview, cards["drawn"].id) is None
    assert cards["covered"].preview_src == "generated:cover"
    assert session.get(Preview, cards["covered"].id) is not None
    assert cards["real"].preview_src == "https://cdn.example.com/banner.png"
