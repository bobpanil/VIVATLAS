"""The picture on a card.

A catalogue of grey rectangles tells you nothing at a glance, and most projects
already have a face — a banner at the top of the README, a logo in the repo, or
failing both the social card the host generates. This module finds it, fetches
it, and normalises it to one small webp the card can show.

Three rules shape everything here:

* **Take what exists, don't make something up.** A project's own banner says more
  than any rendering of its landing page, and costs no browser.
* **A badge is not a picture.** READMEs open with rows of shields — build passing,
  MIT, 12k stars. They are the first images in the file and the last thing worth
  showing, so they are filtered by name and then, decisively, by size.
* **Best effort, never fatal.** A missing or broken preview is a card that looks
  like it does today. Nothing in here may take a scan down with it.
"""

import base64
import io
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx

log = logging.getLogger(__name__)

# The card's own shape: .thumb is 16/10 and crops from the top, so a banner keeps
# its title and a tall logo keeps its head.
CARD_W, CARD_H = 640, 400

MAX_BYTES = 8 * 1024 * 1024  # a card picture that weighs more is a mistake, not art

# Under this it is furniture, not a face. Shields are ~100×20, so this one test
# catches the badges that slip past every name-based rule below — including the
# ones GitHub proxies through camo, where the URL says nothing at all.
MIN_W, MIN_H = 200, 100

# Hosts that exist to serve status pills. Not exhaustive and doesn't need to be:
# it is a cheap first pass, and MIN_W is the net underneath.
_BADGE_HOSTS = (
    "shields.io", "badgen.net", "badge.fury.io", "forthebadge.com",
    "travis-ci.org", "travis-ci.com", "circleci.com", "codecov.io",
    "coveralls.io", "snyk.io", "deepsource.io", "sonarcloud.io",
    "star-history.com", "star-history.dera.page", "api.star-history.com",
    "visitor-badge.laobi.icu", "komarev.com", "hits.dwyl.com",
    "opencollective.com", "herokucdn.com", "gitpod.io", "codespaces.new",
)

# …and paths that give the game away wherever they are hosted. Buttons, sponsor
# strips and contributor collages are the same problem wearing different clothes:
# they are decoration a project pastes in, not a picture of the project.
_BADGE_PATH_HINTS = (
    "badge", "/actions/workflows/", "/workflows/", "shield",
    "button", "subscribe", "sponsor", "donate", "patreon", "buymeacoffee",
    "ko-fi", "kofi", "paypal", "contrib.rocks", "profile-views", "followers",
    "discord.gg", "twitter.com/intent", "linkedin.com/",
)

# Alt text that announces a badge even when the URL is opaque.
_BADGE_ALT = re.compile(
    r"badge|build\s*status|licen[cs]e|coverage|version|downloads|stars?\b|ci\b", re.I
)

# ![alt](url "title")  — the title, if any, is dropped with the url split below.
_MD_IMAGE = re.compile(r"!\[(?P<alt>[^\]]*)\]\(\s*(?P<url>[^)\s]+)")
# <img src="url" alt="alt">, either attribute order.
_HTML_IMAGE = re.compile(r"<img\b[^>]*?>", re.I)
_ATTR = re.compile(r"""(\w+)\s*=\s*["']([^"']*)["']""")

# Names a project gives its own face, most deliberate first. Only consulted when
# the README offered nothing.
_ASSET_NAMES = (
    "banner", "logo", "hero", "cover", "preview", "screenshot", "demo", "social",
)
_ASSET_DIRS = ("", "assets/", "docs/", "img/", "images/", "media/", ".github/", "docs/assets/")
_ASSET_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")


def is_badge(url: str, alt: str = "") -> bool:
    """A status pill rather than a picture of the thing."""
    low = url.lower()
    # removeprefix, not lstrip: lstrip takes a SET of characters, so "wow.dev"
    # would come back as "dev" and match hosts it has nothing to do with.
    host = (urlparse(low).hostname or "").removeprefix("www.")
    if any(host.endswith(h) or h in host for h in _BADGE_HOSTS):
        return True
    if any(hint in low for hint in _BADGE_PATH_HINTS):
        return True
    # Alt text, last: a short label made of badge words ("MIT License", "build
    # status") announces a pill even when the URL is opaque. Length is what makes
    # this safe — a caption or a description that happens to contain "version" is
    # not a badge, and a badge is never a sentence.
    return bool(alt and len(alt.split()) <= 4 and _BADGE_ALT.search(alt))


def image_candidates(markdown: str) -> list[str]:
    """Every image the README points at, in the order it points at them, badges
    dropped. The first survivor is nearly always the banner — that is the whole
    reason this reads in document order rather than picking a "best" one."""
    out: list[str] = []
    for m in _MD_IMAGE.finditer(markdown or ""):
        url = m.group("url").split()[0].strip("<>")
        if not is_badge(url, m.group("alt")):
            out.append(url)
    for tag in _HTML_IMAGE.finditer(markdown or ""):
        attrs = dict(_ATTR.findall(tag.group(0)))
        url = attrs.get("src", "")
        if url and not is_badge(url, attrs.get("alt", "")) and not _declared_small(attrs):
            out.append(url)
    return out


_PX = re.compile(r"(width|height)\s*[:=]\s*[\"']?\s*(\d+)", re.I)


def _declared_small(attrs: dict) -> bool:
    """The author said how big it is meant to be — believe them.

    A sponsors table carries real logos on real hosts with innocent filenames, so
    nothing above catches them; but it renders them `height: 28px`, and nobody
    sizes a hero image at 28px. This is the same threshold the post-download check
    uses, applied earlier and for free, from the page's own markup.
    """
    blob = " ".join(f"{k}={v}" for k, v in attrs.items() if k in ("width", "height", "style"))
    for dim, value in _PX.findall(blob):
        limit = MIN_W if dim.lower() == "width" else MIN_H
        if int(value) < limit:
            return True
    return False


def asset_candidates(paths: list[str]) -> list[str]:
    """Files in the repository that a project uses as its own face. Only reached
    when the README had nothing — a README banner is the more deliberate choice."""
    lower = {p.lower(): p for p in paths}
    out: list[str] = []
    for name in _ASSET_NAMES:
        for d in _ASSET_DIRS:
            for ext in _ASSET_EXTS:
                hit = lower.get(f"{d}{name}{ext}")
                if hit:
                    out.append(hit)
    return out


def absolute(url: str, raw_base: str) -> str | None:
    """A README says `![](assets/logo.png)`; the card needs a real address. Only
    http(s) survives — a data: URI or an anchor is not a picture we can store."""
    if not url:
        return None
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    if url.lower().startswith(("http://", "https://")):
        return url
    if url.startswith(("data:", "#", "mailto:")):
        return None
    if not raw_base:
        return None
    return urljoin(raw_base.rstrip("/") + "/", url.lstrip("./"))


def choose(readme: str, paths: list[str], raw_base: str, og_image: str = "") -> list[str]:
    """Every address worth trying, best first:

    the README's own banner, then a file the project named `logo`/`banner`, then
    the social card the host generated. The last is never the project's own work,
    but it carries the name and the owner's avatar, so a card wearing one still
    reads as that project rather than as a grey box.

    A LIST, not a pick. The first candidate is only the most likely one — it can
    turn out to be an SVG we cannot render or a link that has rotted, and the
    second is usually still better than nothing.
    """
    urls: list[str] = []
    for raw in image_candidates(readme):
        got = absolute(raw, raw_base)
        if got and got not in urls:
            urls.append(got)
    for path in asset_candidates(paths):
        got = absolute(path, raw_base)
        if got and got not in urls:
            urls.append(got)
    if og_image.strip() and og_image.strip() not in urls:
        urls.append(og_image.strip())
    return urls


def _edge_colour(im) -> tuple:
    """What to pad with: the picture's own border colour, so the bars read as part
    of the image rather than as a frame round it. A dark banner gets dark bars, a
    logo on white gets white ones. The average of the outermost pixels is enough —
    what matters is that it does not clash, not that it is exact."""
    from PIL import Image

    edges = im.convert("RGB").resize((3, 3), Image.Resampling.BILINEAR)
    px = [edges.getpixel((x, y)) for x in range(3) for y in range(3) if (x, y) != (1, 1)]
    return tuple(sum(c[i] for c in px) // len(px) for i in range(3))


def raw_base(html_url: str, branch: str) -> str:
    """Where a repository's files are actually served raw.

    Each host spells this differently, and getting it wrong is silent: the old
    preview link used Gitea's `/raw/branch/<b>/` for every source, so GitHub cards
    pointed at a 404. GitHub has a separate raw domain; Gitea serves from the repo
    itself. Anything unrecognised gets Gitea's shape, which is what self-hosted
    installs here run.
    """
    url = (html_url or "").rstrip("/")
    if not url:
        return ""
    branch = branch or "main"
    if "github.com/" in url:
        path = url.split("github.com/", 1)[1]
        return f"https://raw.githubusercontent.com/{path}/{branch}"
    return f"{url}/raw/branch/{branch}"


async def page_image(url: str) -> str:
    """The og:image a page names for itself — what a link shared into a chat
    would show. For a captured link that is not a repository, it is the only
    picture there is, and it is usually a good one: sites choose it on purpose."""
    if not url.lower().startswith(("http://", "https://")):
        return ""
    try:
        from vivatlas.finder import fetch_page_meta

        og = await fetch_page_meta(url, timeout=15.0)
        return (og.get("image") or "").strip()
    except Exception as exc:  # noqa: BLE001 — a page that won't open is no failure of ours
        log.debug("preview: no og:image for %s (%s)", url, exc)
        return ""


def host_card(html_url: str) -> str:
    """The social card the host draws for a repository — name, description, owner's
    avatar, stars. Never the project's own work, so it is the last resort; but it
    is always there, and a card wearing one still reads as that project."""
    url = (html_url or "").rstrip("/")
    if "github.com/" in url:
        return f"https://opengraph.githubassets.com/1/{url.split('github.com/', 1)[1]}"
    return ""


_README_NAMES = ("README.md", "readme.md", "README.MD", "Readme.md", "README.rst")


async def fetch_readme(base: str, timeout: float = 15.0) -> str:
    """The repository's README, straight from the host. "" if there isn't one.

    Needed because a card's doc_text is not always the README: a repository picked
    up from a listing has only its name, description and topics — a couple of
    hundred characters with no markup in them at all. Looking for a banner in that
    finds nothing, and every such card would settle for the host's generated
    social card while its actual banner sat one request away.
    """
    if not base:
        return ""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            for name in _README_NAMES:
                r = await client.get(f"{base}/{name}")
                if r.status_code == 200 and r.text:
                    return r.text
    except Exception as exc:  # noqa: BLE001 — no README is not a failure
        log.debug("preview: no readme at %s (%s)", base, exc)
    return ""


async def fetch(url: str, timeout: float = 20.0) -> bytes | None:
    """Download a candidate. None for anything that isn't a fetchable image —
    a preview is a nicety and never a reason for a scan to fail."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            r = await client.get(url, headers={"User-Agent": "VIVATLAS/1.0 (+preview)"})
            if r.status_code != 200:
                return None
            ctype = r.headers.get("content-type", "").lower()
            if ctype and not ctype.startswith("image/"):
                return None
            data = r.content
            return data if 0 < len(data) <= MAX_BYTES else None
    except Exception as exc:  # noqa: BLE001 — a dead link isn't our failure
        log.debug("preview: %s didn't fetch: %s", url, exc)
        return None


def to_card_webp(data: bytes, content_type: str = "") -> bytes | None:
    """Any image → one 640×400 webp, cropped from the top like the card shows it.

    None rather than an exception for "not usable": too small to be anything but
    a badge, or not decodable at all. The decompression-bomb guards are the same
    ones avatars uses, and for the same reason — a 200 KB file can still be
    hundreds of megabytes once open.
    """
    from PIL import Image, ImageOps

    from vivatlas import avatars

    if not data or len(data) > MAX_BYTES:
        return None
    if avatars._is_svg(data) or "svg" in (content_type or "").lower():
        try:
            # At the card's own width, not an avatar's 256: these are wide generated
            # cards, and rendering them small then scaling up is how text turns to mush.
            data = avatars._svg_to_png(data, width=CARD_W * 2)
        except Exception as exc:  # noqa: BLE001 — a preview is never worth a failure
            log.warning("preview: could not render an SVG (%s)", exc)
            return None

    Image.MAX_IMAGE_PIXELS = avatars.MAX_PIXELS
    try:
        im = Image.open(io.BytesIO(data))
        if im.size[0] * im.size[1] > avatars.MAX_PIXELS or max(im.size) > avatars.MAX_SIDE:
            return None
        if im.size[0] < MIN_W or im.size[1] < MIN_H:
            return None  # a shield, whatever its URL claimed
        im.load()
    except Exception as exc:  # noqa: BLE001
        log.debug("preview: undecodable image (%s)", exc)
        return None

    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
    else:
        im = im.convert("RGB")

    # Fit the WHOLE picture in and pad, rather than cropping to fill.
    #
    # Cropping was the obvious choice and it was wrong: the host's own social card
    # is 2:1, wider than the 16:10 the card shows, so filling the frame sliced the
    # ends off — "NVIDIA/SkillSpector" arrived as "killSpector". A banner exists to
    # be read, and half a title is worse than a smaller whole one. Padding to
    # exactly 16:10 also means the CSS (object-fit: cover on a 16:10 box) has
    # nothing left to crop.
    fitted = im.copy()
    fitted.thumbnail((CARD_W, CARD_H), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (CARD_W, CARD_H), _edge_colour(im))
    mask = fitted.split()[-1] if fitted.mode == "RGBA" else None
    canvas.paste(
        fitted.convert("RGB"),
        ((CARD_W - fitted.width) // 2, (CARD_H - fitted.height) // 2),
        mask,
    )
    im = canvas
    out = io.BytesIO()
    im.save(out, format="WEBP", quality=80, method=6)
    return out.getvalue()


# How many candidates are worth a round trip before we accept the card has no
# face. Enough to get past a rotted first link or an SVG we can't render; small
# enough that a repo listing forty images doesn't hold a scan hostage.
MAX_TRIES = 4

# What the model is asked. It sees the candidates side by side, so it is choosing
# between real alternatives rather than judging one image against an idea of what
# a good one would look like.
_PICK_PROMPT = """These are candidate pictures for a catalogue card about "{name}".
{about}
They are laid out in a grid, two per row, numbered left to right and then top to
bottom: 1 is top-left, 2 is top-right, 3 is the left of the next row, and so on.
There are {count} of them.

Pick the one that best shows what this project IS — its own banner, logo, product
screenshot or title card. Prefer a picture that names or depicts the project over
one that is generic.

Reject, by preferring something else: status badges and shields, sponsor and
partner logos, "buy me a coffee" and subscribe buttons, contributor avatar
collages, star-history charts, and unrelated stock imagery. If every candidate is
one of those, pick the least bad one and say so.

Answer with the number of your pick."""

_PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "pick": {"type": "integer"},
        "why": {"type": "string"},
    },
    "required": ["pick"],
}


def contact_sheet(images: list[bytes]) -> bytes:
    """The candidates as one picture, two to a row.

    One image rather than several calls: the model interface here takes a single
    piece of media, and asking about each candidate separately would be N requests
    to answer one question — and would ask "is this good?" rather than "which of
    these is best", which is the question that actually has an answer.

    Numbered by position rather than by drawing labels: the layout is stated in the
    prompt, so nothing depends on a font being installed or legible after scaling.
    """
    from PIL import Image

    cols = 2
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (CARD_W * cols, CARD_H * rows), (255, 255, 255))
    for i, raw in enumerate(images):
        tile = Image.open(io.BytesIO(raw)).convert("RGB")
        sheet.paste(tile, (CARD_W * (i % cols), CARD_H * (i // cols)))
    out = io.BytesIO()
    sheet.save(out, format="WEBP", quality=80, method=4)
    return out.getvalue()


async def pick_with_model(model, images: list[bytes], name: str, about: str = "") -> int:
    """Which candidate actually represents the project. Returns its index, or 0 —
    the order-based first choice — whenever the model can't be reached, answers
    with nonsense, or there is nothing to choose between.

    Never fatal, and never blocking a picture: a card getting the second-best
    image is a far smaller failure than a scan stopping because a vision model
    was rate-limited.
    """
    if model is None or len(images) < 2:
        return 0
    try:
        prompt = _PICK_PROMPT.format(
            name=name or "this project",
            about=f"It is described as: {about.strip()[:300]}" if about.strip() else "",
            count=len(images),
        )
        sheet = contact_sheet(images)
        data = await model.generate_json_with_media(
            prompt, _PICK_SCHEMA, "image/webp", base64.b64encode(sheet).decode()
        )
        pick = int(data.get("pick") or 1) - 1  # the model counts from one
        if 0 <= pick < len(images):
            log.info("preview: model chose %d of %d (%s)",
                     pick + 1, len(images), (data.get("why") or "")[:120])
            return pick
        log.warning("preview: model answered %r, outside 1..%d", data.get("pick"), len(images))
    except Exception as exc:  # noqa: BLE001 — the heuristic order is a fine answer
        # WARNING, not debug, and deliberately. Falling back returns index 0, which
        # is exactly what a genuine "the first one is best" looks like — so a model
        # that is never actually reached (a stale key, no vision support) would
        # look from the outside like a model that always agrees. This line is the
        # only thing that tells those two apart.
        log.warning("preview: model couldn't choose, falling back to order (%s)", exc)
    return 0


async def build(
    readme: str,
    paths: list[str],
    raw_base: str,
    og_image: str = "",
    model=None,
    name: str = "",
    about: str = "",
) -> tuple:
    """Pick, fetch and convert. Returns (webp_bytes, source_url), or (None, None)
    when nothing usable turned up.

    Gathers what it can rather than betting on the first: the leading image in a
    README is often unusable — an SVG with no renderer here, a dead host — and
    giving up there would leave a grey card beside a repo that plainly has a
    picture further down.

    With a vision model it then asks which of them is actually the project. The
    rules above can only judge an image by its name, its declared size and its
    dimensions, and by those a sponsor's logo and a product screenshot are
    indistinguishable. Looking at them is the only way to tell, so when there is
    something that can look, it does. Without one, document order decides — which
    is the old behaviour, and still right most of the time.
    """
    viable: list[tuple[bytes, str]] = []
    for url in choose(readme, paths, raw_base, og_image)[:MAX_TRIES]:
        data = await fetch(url)
        if not data:
            continue
        webp = to_card_webp(data)
        if webp:
            viable.append((webp, url))

    if not viable:
        return None, None
    if len(viable) == 1:
        return viable[0]

    index = await pick_with_model(model, [w for w, _ in viable], name, about)
    return viable[index]


async def refresh_artifact(
    session, artifact, force: bool = False, readme: str | None = None, model=None
) -> bool:
    """Give one card a picture, if it can have one. True when a new one was stored.

    `readme` is the file's real text when the caller has it — the scanner does,
    having just unpacked the archive. Without it we fall back to the card's
    doc_text, which is the same content truncated at 24k: enough for the banner at
    the top, which is the one that matters, and it means a backfill over a whole
    catalogue asks no host for a README it has already read once.
    """
    from vivatlas.models import Preview

    if artifact.preview_src and not force:
        return False

    repo = artifact.repository
    # A scanned repository has html_url; a link captured from a phone or the
    # extension has html_url="" and keeps the page it came from in original_url.
    # Those were getting nothing at all — no raw base, no host card — while the
    # page they point at usually carries a perfectly good og:image.
    html_url = (repo.html_url or repo.original_url or "") if repo else ""
    # A raw base only where there is a repository to read raw files from: one we
    # scanned, or a captured link that is itself a GitHub repo. For an ordinary
    # web page it would be nonsense — five 404s hunting for a README on ltx.io.
    is_repo = bool(repo and repo.html_url) or "github.com/" in html_url
    base = raw_base(html_url, repo.default_branch if repo else "main") if is_repo else ""
    og = host_card(html_url)
    if html_url and not og:
        og = await page_image(html_url)

    paths: list[str] = []
    if artifact.file_paths:
        import json

        try:
            paths = json.loads(artifact.file_paths)
        except Exception:  # noqa: BLE001 — a malformed listing is not worth a failure
            paths = []

    text = readme if readme is not None else (artifact.doc_text or "")
    # Nothing image-shaped in what we were given? Then it is not a README — see
    # fetch_readme. Worth one request to find the picture the project actually put
    # at the top of its own page.
    if base and not image_candidates(text):
        text = await fetch_readme(base) or text

    webp, src = await build(
        text,
        paths,
        base,
        og_image=og,
        model=model,
        name=artifact.name or "",
        about=artifact.summary_short or "",
    )
    if not webp:
        return False

    row = session.get(Preview, artifact.id)
    if row is None:
        session.add(Preview(artifact_id=artifact.id, webp=webp))
    else:
        row.webp = webp
    artifact.preview_src = src[:1024]
    return True
