"""GitHub.

Reads the PUBLIC repositories of one account (a user or an organization) over
the GitHub REST API. Private repos are never scanned (scanner.is_scannable), so
a token is optional here — it only lifts the anonymous rate limit (60/hour) to
5000/hour. Same GitProvider interface as Gitea; the rest of the code is unaware
of which host it talks to.

Notes vs. Gitea:
  - there is no "everything visible" search — we list one account's repos, so we
    need the account name (`user`), not just a token;
  - the archive is served as a redirect to codeload.github.com; httpx follows it
    (and drops the Authorization header cross-host, which is fine for public repos);
  - the git-tree API is identical (Gitea mirrors it), so blob_shas is the same shape.
"""

import logging
from urllib.parse import urlsplit

import httpx

from vivatlas.providers.base import RepoRef

log = logging.getLogger(__name__)

_API_ROOT = "https://api.github.com"
_PAGE_SIZE = 100  # GitHub's ceiling
_USER_AGENT = "VivAtlas/0.1 (+https://github.com/bobpanil/vivatlas)"


def _account_from(user: str) -> str:
    """Accept either a bare account name ("bobpanil") or a profile URL
    ("https://github.com/bobpanil") — a Source stores the latter in base_url."""
    user = (user or "").strip().rstrip("/")
    if "://" in user:
        path = urlsplit(user).path.strip("/")
        return path.split("/", 1)[0] if path else ""
    return user


class GitHubProvider:
    name = "github"

    def __init__(self, user: str, token: str = "", timeout: float = 30.0) -> None:
        self.account = _account_from(user)
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=_API_ROOT,
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        )

    async def list_repositories(self) -> list[RepoRef]:
        # One account's repositories. /users/{account}/repos returns public repos
        # for both users AND organizations, so we don't need to know which it is.
        if not self.account:
            return []
        repos: list[RepoRef] = []
        page = 1
        while True:
            response = await self._client.get(
                f"/users/{self.account}/repos",
                params={"per_page": _PAGE_SIZE, "page": page, "sort": "updated", "type": "owner"},
            )
            response.raise_for_status()
            batch = response.json() or []
            if not batch:
                break
            repos.extend(_to_repo_ref(item) for item in batch)
            if len(batch) < _PAGE_SIZE:
                break
            page += 1
        return repos

    async def get_head_sha(self, repo: RepoRef) -> str:
        response = await self._client.get(
            f"/repos/{repo.owner}/{repo.name}/branches/{repo.default_branch}"
        )
        response.raise_for_status()
        return response.json()["commit"]["sha"]

    async def download_archive(self, repo: RepoRef, ref: str) -> bytes:
        # 302 -> codeload.github.com; httpx follows it (Authorization is dropped
        # cross-host, which is fine — the repo is public).
        response = await self._client.get(f"/repos/{repo.owner}/{repo.name}/tarball/{ref}")
        response.raise_for_status()
        return response.content

    async def blob_shas(self, repo: RepoRef, ref: str) -> dict[str, str]:
        """Hashes of every file: path -> sha, in one request. A git blob sha is
        computed from content, so the same file on Gitea and GitHub matches."""
        response = await self._client.get(
            f"/repos/{repo.owner}/{repo.name}/git/trees/{ref}",
            params={"recursive": "1"},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("truncated"):
            log.warning("%s: file list truncated, some paths will be missed", repo.full_name)
        return {t["path"]: t["sha"] for t in data.get("tree", []) if t["type"] == "blob"}

    async def aclose(self) -> None:
        await self._client.aclose()


def _to_repo_ref(item: dict) -> RepoRef:
    size_kb = int(item.get("size") or 0)
    return RepoRef(
        external_id=str(item["id"]),
        owner=item["owner"]["login"],
        name=item["name"],
        default_branch=item.get("default_branch") or "main",
        is_private=bool(item.get("private", True)),  # unknown — treat as private
        is_archived=bool(item.get("archived", False)),
        # GitHub has no "empty" flag; a 0 KB repo is effectively empty.
        is_empty=size_kb == 0,
        html_url=item.get("html_url", ""),
        clone_url=item.get("clone_url", ""),
        size_kb=size_kb,
        description=item.get("description") or "",
        created_at=item.get("created_at"),
        # pushed_at reflects actual code changes; updated_at also moves on metadata edits.
        updated_at=item.get("pushed_at") or item.get("updated_at"),
    )
