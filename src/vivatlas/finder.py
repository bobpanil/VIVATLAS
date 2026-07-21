"""Find a repository from anything: a link, a page, a screenshot, text.

One door for everything. Whatever we're given, that's what we parse:

    GitHub link  -> take as is, no guessing needed
    website link -> read the page, look for GitHub links in it
    image        -> the model reads what's written on it
    video        -> the model listens to the audio
    plain text   -> the model pulls out the name

After that it's always the same: gather candidates, show them with stars and a
description, the user picks. We never pull anything automatically: a name heard
aloud or read off an image is recognised imprecisely, and a mistake is costly.

The model is explicitly forbidden from inventing a repository address. An empty
answer beats a plausible lie: a made-up address would drag in the wrong thing.
"""

import base64
import logging
import mimetypes
import re
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path

import httpx

from vivatlas.ai.base import TextModel

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120"
# Facebook and others serve a usable page only to the mobile view. Verified on a
# real reel: a normal request gets a 400, the mobile one gets a title, a
# description and a link to the video.
_UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1"
)

MAX_MEDIA_BYTES = 20_000_000

_GITHUB_REPO = re.compile(r"github\.com/([\w.\-]+)/([\w.\-]+?)(?:\.git)?(?:[/#?)\s\"']|$)", re.I)
_OG = re.compile(r'<meta[^>]+property="og:(\w+)"[^>]+content="([^"]*)"', re.I)
_OG_ALT = re.compile(r'<meta[^>]+content="([^"]*)"[^>]+property="og:(\w+)"', re.I)

# Other people's repositories that turn up in everyone's text — that's not what
# is being searched for.
_NOISE = {
    "actions",
    "features",
    "topics",
    "about",
    "pricing",
    "shields.io",
    "badges",
    "marketplace",
    "sponsors",
    "readme",
    "explore",
}


@dataclass
class Candidate:
    repo: str  # owner/name
    url: str
    stars: int = 0
    description: str = ""
    why: str = ""  # why it's suggested — this matters more to the user than to us
    exact: bool = False  # the address was named directly, not found by search


@dataclass
class FindResult:
    kind: str  # github | web | image | video | text
    source: str
    candidates: list[Candidate] = field(default_factory=list)
    heard: str = ""  # what we recognised: page text, speech, a caption
    language: str = ""  # language of the original — reels come in Hebrew and anything else
    gist: str = ""  # what it's about in English, if the original isn't English
    tool_name: str = ""
    notes: list[str] = field(default_factory=list)


def classify(source: str) -> str:
    s = source.strip()
    if _GITHUB_REPO.search(s) and s.lower().startswith(("http", "github.com")):
        return "github"
    if s.lower().startswith(("http://", "https://")):
        return "web"
    # This also catches whatever the user just typed into search. Checking the
    # path touches the disk, and on a string with a colon or a null byte Windows
    # complains instead of answering "no such file". Any trouble here means one
    # thing: it's not a file, it's words.
    try:
        if Path(s).is_file():
            mime = mimetypes.guess_type(s)[0] or ""
            if mime.startswith("image/"):
                return "image"
            if mime.startswith("video/") or mime.startswith("audio/"):
                return "video"
    except (OSError, ValueError):
        pass
    return "text"


def looks_like_link(text: str) -> bool:
    """Whether it looks like a link — so search doesn't hunt for it among names.

    We only judge by the unambiguous: a link is a link. We won't try to read the
    string "last30days" — unclear whether it's being searched locally or pulled in.
    """
    return classify(text) in ("github", "web")


def extract_repos(text: str) -> list[str]:
    """Every repository mentioned, without noise and without duplicates.

    Duplicates are matched case-insensitively: on the VoltAgent site the links
    are written both as VoltAgent/voltagent and as voltagent/voltagent — to
    GitHub that's one and the same repository, and there's no reason for the user
    to see it twice. We show the spelling that appeared first.
    """
    found: list[str] = []
    seen: set[str] = set()
    for owner, repo in _GITHUB_REPO.findall(text or ""):
        if owner.lower() in _NOISE or repo.lower() in _NOISE:
            continue
        full = f"{owner}/{repo}"
        if full.lower() in seen:
            continue
        seen.add(full.lower())
        found.append(full)
    return found


# A bare "owner/repository" without github.com. That's how the address looks on
# screenshots and in speech. We catch it cautiously and always check with GitHub
# that it exists at all: the rule on its own would also catch "design/system"
# from an ordinary phrase.
_BARE_REPO = re.compile(r"\b([A-Za-z][\w.\-]{1,38})/([A-Za-z][\w.\-]{1,38})\b")

_BARE_STOP = {
    "and",
    "or",
    "the",
    "for",
    "with",
    "input",
    "output",
    "km",
    "ms",
    "n",
    "yes",
    "no",
    "on",
    "off",
    "true",
    "false",
    "http",
    "https",
    "www",
}


def extract_bare_repos(text: str, limit: int = 3) -> list[str]:
    """Something that looks like a repository address, but without github.com. Only
    candidates — existence is checked separately."""
    out: list[str] = []
    seen: set[str] = set()
    for owner, repo in _BARE_REPO.findall(text or ""):
        if owner.lower() in _BARE_STOP or repo.lower() in _BARE_STOP:
            continue
        if owner.lower() in _NOISE or repo.lower() in _NOISE:
            continue
        if "." in owner and "/" not in owner:  # looks like a domain, not an owner
            continue
        full = f"{owner}/{repo}"
        if full.lower() in seen:
            continue
        seen.add(full.lower())
        out.append(full)
    return out[:limit]


def parse_og(page: str) -> dict[str, str]:
    """The og: tags from the page, already unescaped.

    Unescaping is mandatory, and it's not cosmetic. In the tag the video link is
    written as ...&amp;oe=6A5D9DE9&amp;... — in markup & is always written that
    way. Leave it as is and the link's signature breaks, and Facebook answers 403
    to a perfectly valid link. Verified on a live reel: without unescaping 403,
    with it — 1.47 MB mp4.
    """
    tags = {k.lower(): unescape(v) for k, v in _OG.findall(page)}
    for v, k in _OG_ALT.findall(page):
        tags.setdefault(k.lower(), unescape(v))
    return tags


SCHEMA = {
    "type": "object",
    "properties": {
        "heard": {"type": "string"},
        "language": {"type": "string"},
        "gist": {"type": "string"},
        "tool_name": {"type": "string"},
        "github_repo": {"type": "string"},
        "keywords": {"type": "string"},
        "stars_mentioned": {"type": "integer"},
    },
    "required": [
        "heard",
        "language",
        "gist",
        "tool_name",
        "github_repo",
        "keywords",
        "stars_mentioned",
    ],
}

_PROMPT = """Work out which developer tool is being talked about here.

{what}

Speech and captions may be in ANY language: English, Hebrew, Russian, Spanish,
Chinese — anything at all. Parse it as is, don't be surprised and don't refuse.
The text may run right to left — that's normal.

Return:
- heard: what's said or written here, verbatim and brief, IN THE ORIGINAL LANGUAGE
- language: the language of the original, in English, one word: English, Hebrew, Russian…
- gist: what it's about, IN ONE LINE IN ENGLISH. If the original is already English —
  an empty string.
- tool_name: the tool's name, if it was said or is visible. Write it the way it's
  spelled on Git — in Latin letters. Don't translate the name and don't write it
  in the letters of another alphabet. Didn't catch the name — an empty string.
- github_repo: an address of the form owner/repository — ONLY if it's named
  directly or visible. If it's not named — AN EMPTY STRING.
- keywords: 3-6 words IN ENGLISH for searching on GitHub, space-separated. Always
  in English, even if the original is in another language: that's how you search on GitHub.
- stars_mentioned: if a star count is named — that number, otherwise 0

The main rule: DON'T INVENT a repository address. An empty string beats a
plausible lie — a made-up address will drag in the wrong tool."""


class Finder:
    def __init__(self, github_token: str = "", timeout: float = 60.0) -> None:
        gh = {"User-Agent": _UA, "Accept": "application/vnd.github+json"}
        if github_token:
            gh["Authorization"] = f"Bearer {github_token}"
        self._gh = httpx.AsyncClient(base_url="https://api.github.com", headers=gh, timeout=timeout)
        self._web = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def find(self, source: str, model: TextModel | None = None) -> FindResult:
        kind = classify(source)
        result = FindResult(kind=kind, source=source)

        if kind == "github":
            repos = extract_repos(source)
            if repos:
                result.candidates = [
                    await self._describe(repos[0], "address given directly", exact=True)
                ]
            return result

        if kind == "web":
            await self._from_web(source, result, model)
        elif kind in ("image", "video"):
            await self._from_media(source, kind, result, model)
        else:
            await self._from_text(source, result, model)

        if not result.candidates and (result.tool_name or result.heard):
            await self._search(result)
        result.candidates.sort(key=lambda c: (not c.exact, -c.stars))
        return result

    # --- sources ---

    async def _from_web(self, url: str, result: FindResult, model: TextModel | None) -> None:
        html = ""
        for ua in (_UA_MOBILE, _UA):
            try:
                r = await self._web.get(url, headers={"User-Agent": ua})
                if r.status_code == 200 and len(r.text) > 500:
                    html = r.text
                    break
            except Exception as exc:
                result.notes.append(f"page didn't open ({ua.split('(')[0].strip()}): {exc}")
        if not html:
            result.notes.append("couldn't read the page")
            return

        og = parse_og(html)
        result.heard = (og.get("title", "") + " " + og.get("description", "")).strip()

        repos = extract_repos(html)
        if repos:
            result.notes.append(f"GitHub links found on the page: {len(repos)}")
            for repo in repos[:3]:
                result.candidates.append(
                    await self._describe(repo, "link is on the page", True)
                )
            return

        result.notes.append("no GitHub links on the page — we'll have to search by meaning")

        # Reel: no links on the page, the whole meaning is in the audio. A name
        # heard aloud is recognised imprecisely, so search and manual choice follow anyway.
        video = og.get("video") or og.get("video:url") or og.get("video:secure_url")
        if video and model is not None:
            data = await self._download(video, result)
            if data:
                result.notes.append(f"listening to the clip, {len(data) / 1024 / 1024:.1f} MB")
                await self._ask(
                    model,
                    "Listen to the audio of this clip. Read the on-screen text too, "
                    "if there is any.",
                    result,
                    media=("video/mp4", data),
                )
                return

        if result.heard and model is not None:
            await self._ask(model, f"Text from the page:\n\n{result.heard[:1500]}", result)

    async def _download(self, url: str, result: FindResult) -> bytes | None:
        """Download the clip. We skip oversized ones: the model won't accept them anyway."""
        try:
            async with self._web.stream("GET", url, headers={"User-Agent": _UA_MOBILE}) as response:
                if response.status_code != 200:
                    result.notes.append(f"clip wasn't served: HTTP {response.status_code}")
                    return None
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_MEDIA_BYTES:
                        result.notes.append(
                            f"clip is bigger than {MAX_MEDIA_BYTES // 1_000_000} MB"
                            " — can't handle it"
                        )
                        return None
                    chunks.append(chunk)
                return b"".join(chunks)
        except Exception as exc:
            result.notes.append(f"clip didn't download: {exc}")
            return None

    async def _from_media(
        self, path: str, kind: str, result: FindResult, model: TextModel | None
    ) -> None:
        if model is None:
            result.notes.append("without a model an image or video can't be parsed")
            return
        data = Path(path).read_bytes()
        if len(data) > MAX_MEDIA_BYTES:
            result.notes.append(f"file is oversized: {len(data) / 1024 / 1024:.0f} MB")
            return
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        what = (
            "Read what's written on this image."
            if kind == "image"
            else "Listen to the audio of this clip."
        )
        await self._ask(model, what, result, media=(mime, data))

    async def _from_text(self, text: str, result: FindResult, model: TextModel | None) -> None:
        repos = extract_repos(text)
        if repos:
            for repo in repos[:3]:
                result.candidates.append(await self._describe(repo, "address is in the text", True))
            return
        if model is None:
            result.notes.append("without a model nothing can be pulled from the text")
            return
        await self._ask(model, f"Text:\n\n{text[:2000]}", result)

    # --- model ---

    async def _ask(
        self,
        model: TextModel,
        what: str,
        result: FindResult,
        media: tuple[str, bytes] | None = None,
    ) -> None:
        prompt = _PROMPT.format(what=what)
        try:
            if media is not None:
                data = await model.generate_json_with_media(
                    prompt, SCHEMA, media[0], base64.b64encode(media[1]).decode()
                )
            else:
                data = await model.generate_json(prompt, SCHEMA)
        except Exception as exc:
            result.notes.append(f"the model couldn't cope: {exc}")
            return

        result.heard = (data.get("heard") or result.heard).strip()
        result.language = (data.get("language") or "").strip()
        result.gist = (data.get("gist") or "").strip()
        result.tool_name = (data.get("tool_name") or "").strip()
        stars = int(data.get("stars_mentioned") or 0)
        if stars:
            result.notes.append(f"stars named: about {stars:,}".replace(",", " "))

        # We look both at the address field and at the recognised text. The model
        # plays it safe: a GitHub card said "VoltAgent/awesome-design-md", it read
        # that into heard, but left the address field empty. The address was right
        # there in plain sight — a shame to lose it.
        sure: list[tuple[str, str]] = []
        for repo in extract_repos(data.get("github_repo") or "") or extract_bare_repos(
            data.get("github_repo") or "", limit=1
        ):
            sure.append((repo, "the address was named"))
        # A full link in a caption we take as is: it's already an address, nothing to guess.
        # That's how a repository is captioned in reels — "Link: https://github.com/...".
        for repo in extract_repos(result.heard):
            sure.append((repo, "the full link is visible"))

        # We check with GitHub everything the model named. It doesn't always obey
        # the "don't invent an address" ban: on a live reel it produced skills/last-30-day
        # — no such repository exists, and we'd have suggested pulling it in. You can
        # ask the model not to lie; you can't rely on it.
        known = {c.repo.lower() for c in result.candidates}
        for repo, why in sure[:3]:
            if repo.lower() in known:
                continue
            if not await self._exists(repo):
                result.notes.append(
                    f"the model named {repo}, but it doesn't exist — we don't trust it"
                )
                continue
            result.candidates.append(await self._describe(repo, why, True))
            known.add(repo.lower())

        # A bare "owner/repository" without github.com — that's how the address is
        # written on images and spoken aloud. We ask GitHub whether it exists:
        # the rule on its own would also catch "design/system" from an ordinary phrase.
        for repo in extract_bare_repos(result.heard):
            if repo.lower() in known:
                continue
            if not await self._exists(repo):
                continue
            result.candidates.append(
                await self._describe(repo, "address is visible in the caption", True)
            )
            known.add(repo.lower())

        if not result.candidates:
            result.notes.append("repository address not named — we'll search by name")

        result.notes.append(f"keywords for the search: {data.get('keywords', '')}")
        result._keywords = data.get("keywords", "")  # type: ignore[attr-defined]
        result._stars = stars  # type: ignore[attr-defined]

    # --- search ---

    async def _search(self, result: FindResult) -> None:
        """We search several ways: a name heard aloud is recognised imprecisely.

        Verified live: the model heard "last30dayskill", which finds nothing.
        It was only found by "last 30 days skill" — with spaces.
        """
        queries: list[str] = []
        name = result.tool_name
        if name:
            queries.append(name)
            spaced = re.sub(r"[-_]", " ", name)
            if spaced != name:
                queries.append(spaced)
            split = re.sub(r"(\d+)", r" \1 ", spaced).strip()
            if split not in queries:
                queries.append(split)
        kw = getattr(result, "_keywords", "")
        if kw:
            queries.append(kw)

        seen = {c.repo.lower() for c in result.candidates}
        for q in queries:
            for repo, stars, desc in await self._github_search(q):
                if repo.lower() in seen:
                    continue
                seen.add(repo.lower())
                why = f"found by the query «{q}»"
                said_stars = getattr(result, "_stars", 0)
                if said_stars and stars:
                    ratio = stars / said_stars
                    if 0.5 <= ratio <= 3:
                        why += f"; stars match ({stars:,})".replace(",", " ")
                result.candidates.append(
                    Candidate(
                        repo=repo,
                        url=f"https://github.com/{repo}",
                        stars=stars,
                        description=desc,
                        why=why,
                    )
                )
            if len(result.candidates) >= 5:
                break
        result.candidates.sort(key=lambda c: (not c.exact, -c.stars))
        result.candidates = result.candidates[:5]

    async def _github_search(self, query: str) -> list[tuple[str, int, str]]:
        try:
            r = await self._gh.get(
                "/search/repositories", params={"q": query, "sort": "stars", "per_page": 3}
            )
            if r.status_code != 200:
                return []
            return [
                (i["full_name"], i["stargazers_count"], (i.get("description") or "")[:90])
                for i in r.json().get("items", [])
            ]
        except Exception:
            return []

    async def _exists(self, repo: str) -> bool:
        try:
            r = await self._gh.get(f"/repos/{repo}")
            return r.status_code == 200
        except Exception:
            return False

    async def _describe(self, repo: str, why: str, exact: bool = False) -> Candidate:
        c = Candidate(repo=repo, url=f"https://github.com/{repo}", why=why, exact=exact)
        try:
            r = await self._gh.get(f"/repos/{repo}")
            if r.status_code == 200:
                d = r.json()
                c.stars = d.get("stargazers_count", 0)
                c.description = (d.get("description") or "")[:90]
            elif r.status_code == 404:
                c.why += " — but there's no such repository"
        except Exception:
            pass
        return c

    async def aclose(self) -> None:
        await self._gh.aclose()
        await self._web.aclose()
