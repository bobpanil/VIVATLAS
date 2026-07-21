"""Pull in a tool by URL.

The point: if we pulled it in, the source is known by construction. No need to
hunt for it in the README and guess — we recorded it ourselves. The baseline
mark comes out perfect: at import time the copy and the original match by design.

The workflow is always the same: parse the URL -> show the plan -> wait for
confirmation -> execute. Nothing is created without confirmation.
"""

import logging
import re
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; SkillAtlas/0.1)"

# We cap by weight, not by file count: a real skill may be made up of a hundred
# small files (mvanhorn/last30days-skill — 121 files), and rejecting it over the
# count is wrong. The ceiling is high because it only guards against an obvious
# mistake — a link to someone else's monorepo (49 MB).
MAX_FILES = 500
MAX_TOTAL_BYTES = 25_000_000


_SKIP_DIRS = (".git/", "node_modules/", ".github/workflows/")

# Gitea allows letters, digits, hyphen, dot and underscore in repository and
# owner names. Everything else we replace with a hyphen rather than dropping it:
# otherwise "foo/bar" and "foo bar" would merge into a single name.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(raw: str) -> str:
    name = _UNSAFE.sub("-", raw.strip()).strip("-.")
    return name or "unnamed"


class ImportError_(RuntimeError):
    pass


@dataclass
class ImportSource:
    """What exactly is being asked to pull in."""

    kind: str  # repo | folder | file
    owner: str
    repo: str
    ref: str = ""  # branch, empty = default
    path: str = ""  # folder or file inside the repository

    @property
    def full_repo(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def leaf(self) -> str:
        """The last part of the path — the tool's folder or file name."""
        if self.kind == "repo":
            return ""
        tail = self.path.rstrip("/").rsplit("/", 1)[-1]
        if self.kind == "file":
            tail = tail.rsplit(".", 1)[0]
        return tail

    @property
    def mirror_owner(self) -> str:
        """Our owner = the owner on GitHub. Always.

        This way the GitHub address reads off the Gitea address, and the card's
        source is visible right in the path: git.../mvanhorn/last30days-skill
        ← github.com/mvanhorn/...
        """
        return _safe_name(self.owner)

    @property
    def mirror_name(self) -> str:
        """Our name follows the GitHub address.

        Whole repository → its name as-is.
        Monorepo subfolder → "repository-folder": 74 sets from a single
        awesome-design-md would land at one address, but this way each gets its own —
        awesome-design-md-airbnb, awesome-design-md-apple. It also removes the
        collision: someone else's airbnb arrives under its own owner and never
        meets this one.
        """
        if self.kind == "repo":
            return _safe_name(self.repo)
        return _safe_name(f"{self.repo}-{self.leaf}")

    @property
    def suggested_name(self) -> str:
        return self.mirror_name


@dataclass
class ImportFile:
    path: str  # our path
    content: bytes
    upstream_path: str  # path at the source
    sha: str  # git snapshot — usable straight away as the baseline mark


@dataclass
class ImportPlan:
    source: ImportSource
    target_owner: str
    target_name: str
    files: list[ImportFile] = field(default_factory=list)
    method: str = "copy"  # copy | mirror
    warnings: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(len(f.content) for f in self.files)

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.source.full_repo}.git"


# Files that signal "a standalone tool lives here".
_ANCHOR_NAMES = ("skill.md", "design.md", "mcp.json", "plugin.json", "agents.md")


def find_tool_folders(blobs: list[dict]) -> list[str]:
    """Folders inside the repository that look like standalone tools.

    Needed when the link points at the whole repository but the tool is inside.
    That's how mvanhorn/last30days-skill is built: 400 files, but the skill is
    in skills/last30days/.
    Instead of a useless rejection we show what the user probably meant.
    """
    folders: list[str] = []
    for entry in blobs:
        path = entry["path"]
        name = path.rsplit("/", 1)[-1].lower()
        if name not in _ANCHOR_NAMES:
            continue
        folder = path.rsplit("/", 1)[0] if "/" in path else ""
        if folder and folder not in folders:
            folders.append(folder)
    folders.sort(key=lambda f: (f.count("/"), f))
    return folders


# --- URL parsing ---

_GITHUB = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[\w.\-]+)/(?P<repo>[\w.\-]+?)(?:\.git)?"
    r"(?:/(?P<kind>tree|blob)/(?P<ref>[^/]+)(?:/(?P<path>.*))?)?/?$"
)
_RAW = re.compile(
    r"^https?://raw\.githubusercontent\.com/(?P<owner>[\w.\-]+)/(?P<repo>[\w.\-]+)"
    r"/(?P<ref>[^/]+)/(?P<path>.+)$"
)


def parse_url(url: str) -> ImportSource:
    """Parse a GitHub URL. We don't handle other hosts yet — and we say so
    plainly instead of pretending."""
    url = url.strip()

    m = _RAW.match(url)
    if m:
        return ImportSource(
            kind="file", owner=m["owner"], repo=m["repo"], ref=m["ref"], path=m["path"]
        )

    m = _GITHUB.match(url)
    if not m:
        raise ImportError_(
            f"Couldn't parse the URL: {url}\n"
            "For now I only handle github.com and raw.githubusercontent.com."
        )

    kind_marker = m["kind"]
    path = (m["path"] or "").strip("/")

    if not kind_marker:
        return ImportSource(kind="repo", owner=m["owner"], repo=m["repo"])
    if kind_marker == "blob":
        return ImportSource(kind="file", owner=m["owner"], repo=m["repo"], ref=m["ref"], path=path)
    # tree without a path is just a branch, i.e. the whole repository
    if not path:
        return ImportSource(kind="repo", owner=m["owner"], repo=m["repo"], ref=m["ref"])
    return ImportSource(kind="folder", owner=m["owner"], repo=m["repo"], ref=m["ref"], path=path)


# --- downloading from GitHub ---


class GitHubFetcher:
    def __init__(self, token: str = "", timeout: float = 60.0) -> None:
        headers = {"User-Agent": _UA, "Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url="https://api.github.com", headers=headers, timeout=timeout
        )

    async def repo_info(self, source: ImportSource) -> dict:
        r = await self._client.get(f"/repos/{source.full_repo}")
        if r.status_code == 404:
            raise ImportError_(f"No such repository: {source.full_repo}")
        r.raise_for_status()
        return r.json()

    async def fetch(self, source: ImportSource) -> tuple[list[ImportFile], list[str]]:
        """The source's files. Returns (files, warnings)."""
        info = await self.repo_info(source)
        ref = source.ref or info["default_branch"]
        warnings: list[str] = []

        if info.get("archived"):
            warnings.append("the repository is archived — there will be no more updates")
        if info.get("private"):
            raise ImportError_("the repository is private — we won't pull it in")

        r = await self._client.get(
            f"/repos/{source.full_repo}/git/trees/{ref}", params={"recursive": "1"}
        )
        r.raise_for_status()
        tree = r.json()
        if tree.get("truncated"):
            warnings.append(
                "the file list was truncated by GitHub — we may not have taken everything"
            )

        blobs = [t for t in tree.get("tree", []) if t["type"] == "blob"]

        if source.kind == "repo":
            wanted = blobs
            strip = ""
        elif source.kind == "folder":
            prefix = source.path.rstrip("/") + "/"
            wanted = [t for t in blobs if t["path"].startswith(prefix)]
            strip = prefix
            if not wanted:
                raise ImportError_(f"{source.full_repo} has no folder {source.path}")
        else:
            wanted = [t for t in blobs if t["path"] == source.path]
            strip = source.path.rsplit("/", 1)[0] + "/" if "/" in source.path else ""
            if not wanted:
                raise ImportError_(f"{source.full_repo} has no file {source.path}")

        wanted = [t for t in wanted if not any(s in t["path"] for s in _SKIP_DIRS)]

        weight = sum(t.get("size", 0) for t in wanted)
        if weight > MAX_TOTAL_BYTES or len(wanted) > MAX_FILES:
            # Don't just reject — give a hint. A real case:
            # mvanhorn/last30days-skill — 400 files, but the skill itself lives in
            # skills/last30days/. A repository isn't the same as a tool.
            hints = find_tool_folders(blobs)
            message = (
                f"{len(wanted)} files, {weight / 1024 / 1024:.1f} MB total. "
                f"Looks like a whole project, not a single tool."
            )
            if hints:
                message += "\n\nHere's what inside looks like standalone tools:"
                for h in hints[:6]:
                    message += f"\n  https://github.com/{source.full_repo}/tree/{ref}/{h}"
            else:
                message += "\n\nIf you need the whole repository as-is — pull it in as a mirror."
            raise ImportError_(message)

        # We download the contents as a single archive, not file by file: the
        # last30days-skill folder has 121 files — that would be 121 requests, and
        # GitHub's rate limits would run out on the very first import.
        contents = await self._download_tarball(source, ref)

        files: list[ImportFile] = []
        excluded: list[dict] = []
        for entry in wanted:
            content = contents.get(entry["path"])
            if content is None:
                excluded.append(entry)
                continue
            local = entry["path"][len(strip) :] if strip else entry["path"]
            files.append(
                ImportFile(
                    path=local,
                    content=content,
                    upstream_path=entry["path"],
                    sha=entry["sha"],  # snapshot from the tree — usable as the baseline mark
                )
            )

        if excluded:
            # This isn't an error but the author's intent. In .gitattributes they mark
            # export-ignore on what isn't needed to run: tests, documentation,
            # images. That's how mvanhorn/last30days-skill excludes 14 MB of demos.
            # Important: on install an agent client downloads exactly this same
            # archive, so the archive is the canonical form of the tool.
            weight_mb = sum(e.get("size", 0) for e in excluded) / 1024 / 1024
            folders = sorted({e["path"].split("/")[-2] for e in excluded if "/" in e["path"]})
            warnings.append(
                f"the author excluded {len(excluded)} files from the archive "
                f"({weight_mb:.1f} MB, folders: {', '.join(folders[:4])}) — "
                f"marked them in .gitattributes as not needed to run. "
                f"On install the agent client receives exactly this same archive."
            )

        return files, warnings

    async def _download_tarball(self, source: ImportSource, ref: str) -> dict[str, bytes]:
        import io
        import tarfile

        r = await self._client.get(
            f"/repos/{source.full_repo}/tarball/{ref}", follow_redirects=True
        )
        r.raise_for_status()

        out: dict[str, bytes] = {}
        with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                # the archive is wrapped in a single folder like owner-repo-sha/
                parts = member.name.split("/", 1)
                if len(parts) != 2:
                    continue
                f = tar.extractfile(member)
                if f is not None:
                    out[parts[1]] = f.read()
        return out

    async def aclose(self) -> None:
        await self._client.aclose()


# --- plan ---


async def plan_import(
    fetcher: GitHubFetcher,
    url: str,
    target_owner: str = "",
    target_name: str = "",
    method: str = "copy",
) -> ImportPlan:
    source = parse_url(url)

    if method == "mirror" and source.kind != "repo":
        raise ImportError_(
            "A mirror only works on a whole repository. A folder or file can only be copied."
        )

    # If no target was given, we place it by rule: the path as on GitHub. The user
    # can override it, but by default the Gitea address mirrors the source address.
    files, warnings = await fetcher.fetch(source)
    plan = ImportPlan(
        source=source,
        target_owner=target_owner or source.mirror_owner,
        target_name=target_name or source.mirror_name,
        files=files,
        method=method,
        warnings=warnings,
    )

    if not any(f.path.lower() in ("skill.md", "design.md", "readme.md") for f in files):
        plan.warnings.append("no SKILL.md and no README.md — the type will be hard to determine")

    return plan
