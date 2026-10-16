"""Gitea."""

import logging
from urllib.parse import urlsplit

import httpx

from vivatlas.providers.base import RepoRef

log = logging.getLogger(__name__)

# The instance answers 403 to requests without a recognizable User-Agent — verified on
# a live Gitea. Without this header not a single request works.
_USER_AGENT = "Mozilla/5.0 (compatible; SkillAtlas/0.1)"

_PAGE_SIZE = 50  # Gitea's ceiling, asking for more is pointless


class GiteaProvider:
    name = "gitea"

    def __init__(self, base_url: str, token: str = "", timeout: float = 30.0) -> None:
        # The Gitea root is only scheme and host. A path in the URL (e.g. /boris) is a
        # profile link, not part of the API: it cannot be glued onto /api/v1,
        # otherwise .../boris/api/v1/… gives a 404. We drop the path — the root
        # instance URL is enough.
        parts = urlsplit(base_url.rstrip("/"))
        self.api_root = f"{parts.scheme}://{parts.netloc}"
        self.base_url = self.api_root
        headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"token {token}"
        self._client = httpx.AsyncClient(
            base_url=f"{self.api_root}/api/v1",
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        )

    async def list_repositories(self) -> list[RepoRef]:
        # /repos/search with a token returns everything the token can see: public
        # repositories across the whole instance plus private ones it has access to — for
        # all users and organizations at once. A name in the URL is not required,
        # the root link is enough. What of this ends up in the shared zone and what does not
        # is decided by scan_source via include_private; here we only enumerate.
        repos: list[RepoRef] = []
        page = 1
        while True:
            response = await self._client.get(
                "/repos/search", params={"limit": _PAGE_SIZE, "page": page}
            )
            response.raise_for_status()
            batch = response.json().get("data") or []
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
        return response.json()["commit"]["id"]

    async def download_archive(self, repo: RepoRef, ref: str) -> bytes:
        response = await self._client.get(f"/repos/{repo.owner}/{repo.name}/archive/{ref}.tar.gz")
        response.raise_for_status()
        return response.content

    async def blob_shas(self, repo: RepoRef, ref: str) -> dict[str, str]:
        """Hashes of every file: path -> sha. In a single request.

        A git hash is computed from the content, so the same file here and on
        GitHub gives the same sha — you can compare directly, without downloading anything.
        """
        response = await self._client.get(
            f"/repos/{repo.owner}/{repo.name}/git/trees/{ref}",
            params={"recursive": "true", "per_page": 1000},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("truncated"):
            log.warning("%s: file list truncated, some paths will be missed", repo.full_name)
        return {t["path"]: t["sha"] for t in data.get("tree", []) if t["type"] == "blob"}

    async def aclose(self) -> None:
        await self._client.aclose()

    # Read only. VIVATLAS never creates, changes or deletes a repository: copying a
    # repository into Git is a different job for a different tool.


def _to_repo_ref(item: dict) -> RepoRef:
    return RepoRef(
        external_id=str(item["id"]),
        owner=item["owner"]["login"],
        name=item["name"],
        default_branch=item.get("default_branch") or "main",
        is_private=bool(item.get("private", True)),  # unknown — treat as private
        is_archived=bool(item.get("archived", False)),
        is_empty=bool(item.get("empty", False)),
        html_url=item.get("html_url", ""),
        clone_url=item.get("clone_url", ""),
        size_kb=int(item.get("size") or 0),
        original_url=item.get("original_url") or "",
        description=item.get("description") or "",
        created_at=item.get("created_at"),
        updated_at=item.get("updated_at"),
    )
