"""Where a tool came from and whether a new version has been released.

The source is looked up ONLY where it's recorded explicitly:
  1. Gitea knows the repository is a mirror (original_url)
  2. The README has a line "Source: ... github.com/owner/repository"

The model is deliberately kept away from this. Ask it to find the source and it
will, because it always finds something. And then someone rewrites files based
on that guess. No source means no source, and that's what we write down.

Comparison goes by git blobs (blob sha). They're computed from content, so the
same file on GitHub and in Gitea yields the same blob — you can compare them
directly, without downloading anything.
"""

import logging
import re
from dataclasses import dataclass

import httpx

from vivatlas.archive import RepoContents

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; SkillAtlas/0.1)"

# "Source: [getdesign.md](...) · [VoltAgent/awesome-design-md](https://github.com/VoltAgent/awesome-design-md)"
_SOURCE_LINE = re.compile(
    r"source\s*:.*?github\.com/([\w.\-]+)/([\w.\-]+?)(?:\.git)?[)\s.,·]",
    re.I | re.S,
)


@dataclass
class UpstreamRef:
    kind: str  # github-file | gitea-mirror
    repo: str  # "VoltAgent/awesome-design-md"
    path: str  # "design-md/cohere/DESIGN.md", empty for a mirror
    url: str
    discovered_by: str


def detect_from_mirror(original_url: str) -> UpstreamRef | None:
    """Gitea itself knows where the repository was brought from. The most reliable case."""
    if not original_url:
        return None
    m = re.search(r"github\.com/([\w.\-]+)/([\w.\-]+?)(?:\.git)?/?$", original_url)
    if not m:
        return None
    return UpstreamRef(
        kind="gitea-mirror",
        repo=f"{m.group(1)}/{m.group(2)}",
        path="",
        url=original_url,
        discovered_by="Gitea: this is a mirror",
    )


def detect_from_readme(
    contents: RepoContents, repo_name: str, anchor_path: str | None
) -> UpstreamRef | None:
    """A "Source: ..." line at the end of the README.

    We search the full text of the file, not the truncated description: in design
    sets this line comes last and doesn't make it into the truncation.
    """
    readme = contents.get("README.md")
    if readme is None or not readme.text:
        return None

    m = _SOURCE_LINE.search(readme.text)
    if not m:
        return None

    upstream_repo = f"{m.group(1)}/{m.group(2)}"
    path = _guess_path(upstream_repo, repo_name, anchor_path)
    return UpstreamRef(
        kind="github-file",
        repo=upstream_repo,
        path=path,
        url=f"https://github.com/{upstream_repo}",
        discovered_by="Source line in README",
    )


def _guess_path(upstream_repo: str, repo_name: str, anchor_path: str | None) -> str:
    """Where the source keeps our file.

    Checked against all 74 design sets: the path is always design-md/<name>/DESIGN.md,
    where <name> is the name of our repository. The rule is deliberately narrow:
    guessing the path for an unknown source means comparing the wrong thing against
    the wrong thing later.
    """
    if upstream_repo.lower() == "voltagent/awesome-design-md" and anchor_path:
        return f"design-md/{repo_name}/{anchor_path}"
    return ""


class UpstreamChecker:
    """Blobs of the source's files. One request for the whole repository."""

    def __init__(self, token: str = "", timeout: float = 30.0) -> None:
        headers = {"User-Agent": _UA, "Accept": "application/vnd.github+json"}
        if token:
            # The token isn't for access — the repositories are public. It's only so
            # GitHub doesn't cap us at 60 requests per hour.
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True)
        self._cache: dict[str, dict[str, str]] = {}

    async def blob_shas(self, repo: str, branch: str = "") -> dict[str, str]:
        """All files of the repository with their blobs: path -> sha."""
        if repo in self._cache:
            return self._cache[repo]

        if not branch:
            info = await self._client.get(f"https://api.github.com/repos/{repo}")
            info.raise_for_status()
            branch = info.json()["default_branch"]

        response = await self._client.get(
            f"https://api.github.com/repos/{repo}/git/trees/{branch}",
            params={"recursive": "1"},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("truncated"):
            log.warning("%s: file list truncated by github, we won't see some paths", repo)

        shas = {t["path"]: t["sha"] for t in data.get("tree", []) if t["type"] == "blob"}
        self._cache[repo] = shas
        return shas

    async def blob(self, repo: str, sha: str) -> bytes:
        """A file's content by its blob.

        By blob, not by path in the branch: we've already compared the blob and know
        the new version is this one. While we're fetching the content, another commit
        may land on the branch, and by path we'd get something other than what we
        showed the user.
        """
        import base64

        response = await self._client.get(f"https://api.github.com/repos/{repo}/git/blobs/{sha}")
        response.raise_for_status()
        data = response.json()
        if data.get("encoding") != "base64":
            raise RuntimeError(f"{repo}@{sha[:8]}: unexpected packing {data.get('encoding')}")
        return base64.b64decode(data["content"])

    async def head_sha(self, repo: str) -> str:
        """The latest commit — for mirrors, where we compare the whole thing."""
        response = await self._client.get(f"https://api.github.com/repos/{repo}")
        response.raise_for_status()
        branch = response.json()["default_branch"]
        response = await self._client.get(f"https://api.github.com/repos/{repo}/commits/{branch}")
        response.raise_for_status()
        return response.json()["sha"]

    async def aclose(self) -> None:
        await self._client.aclose()


def decide_status(
    local_sha: str,
    upstream_sha: str,
    baseline_local: str,
    baseline_upstream: str,
) -> str:
    """What the discrepancy means.

    Without a mark taken at copy time, "the files differ" means nothing: either a
    new version came out, or the user edited it themselves. The mark tells them apart.
    """
    if not local_sha or not upstream_sha:
        return "unknown"
    if local_sha == upstream_sha:
        return "in-sync"
    if local_sha == baseline_local and upstream_sha != baseline_upstream:
        return "update-available"  # theirs is new, ours is untouched
    if upstream_sha == baseline_upstream and local_sha != baseline_local:
        return "locally-modified"  # we edited it, theirs is unchanged
    return "diverged"  # diverged on both sides


STATUS_NAMES = {
    "in-sync": "matches the source",
    "update-available": "a new version was released",
    "locally-modified": "you edited it — updating isn't allowed",
    "diverged": "diverged on both sides",
    "unknown": "nothing to compare against",
}
