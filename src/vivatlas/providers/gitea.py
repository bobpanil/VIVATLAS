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

    # --- write. Requires a token and is only called after confirmation. ---

    async def repo_exists(self, owner: str, name: str) -> bool:
        response = await self._client.get(f"/repos/{owner}/{name}")
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    async def create_repo(self, owner: str, name: str, description: str = "") -> dict:
        """Create a repository in an organization.

        Creation is the only write that cannot break anything:
        before it there was no repository. So we check in advance that the name
        is free, and refuse if it is taken, instead of overwriting.
        """
        if await self.repo_exists(owner, name):
            raise RuntimeError(f"{owner}/{name} already exists — refusing to overwrite")

        response = await self._client.post(
            f"/orgs/{owner}/repos",
            json={
                "name": name,
                "description": description[:255],
                "private": False,
                "auto_init": False,
                "default_branch": "main",
            },
        )
        response.raise_for_status()
        return response.json()

    async def put_file(
        self, owner: str, name: str, path: str, content: bytes, message: str, branch: str = "main"
    ) -> dict:
        import base64

        response = await self._client.post(
            f"/repos/{owner}/{name}/contents/{path}",
            json={
                "content": base64.b64encode(content).decode(),
                "message": message,
                "branch": branch,
            },
        )
        response.raise_for_status()
        return response.json()

    async def update_file(
        self,
        owner: str,
        name: str,
        path: str,
        content: bytes,
        message: str,
        sha: str,
        branch: str = "main",
    ) -> dict:
        """Replace the contents of a file that already exists.

        The hash of the old file is mandatory. This is not a formality: Gitea checks it
        against what is actually there, and refuses if the file was edited
        after our check. Otherwise we would silently overwrite someone else's edit.
        """
        import base64

        if not sha:
            raise RuntimeError("nothing to replace: the old file's hash is unknown")
        response = await self._client.put(
            f"/repos/{owner}/{name}/contents/{path}",
            json={
                "content": base64.b64encode(content).decode(),
                "message": message,
                "branch": branch,
                "sha": sha,
            },
        )
        response.raise_for_status()
        return response.json()

    async def delete_repo(self, owner: str, name: str) -> None:
        """Only for rolling back a failed import. Not called anywhere else."""
        response = await self._client.delete(f"/repos/{owner}/{name}")
        response.raise_for_status()

    async def org_exists(self, org: str) -> bool:
        response = await self._client.get(f"/orgs/{org}")
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    async def create_org(self, org: str) -> dict:
        """An organization for a GitHub owner.

        Idempotent: if it already exists — we return it rather than crashing. A migration
        may resume after a failure, with half the organizations already created.
        """
        if await self.org_exists(org):
            response = await self._client.get(f"/orgs/{org}")
            response.raise_for_status()
            return response.json()
        response = await self._client.post("/orgs", json={"username": org})
        response.raise_for_status()
        return response.json()

    async def rename_repo(self, owner: str, name: str, new_name: str) -> None:
        """Rename within the same owner."""
        if name == new_name:
            return
        response = await self._client.patch(f"/repos/{owner}/{name}", json={"name": new_name})
        response.raise_for_status()

    async def transfer_repo(self, owner: str, name: str, new_owner: str) -> None:
        """Transfer a repository to another owner, keeping the name.

        The token is an admin one — the transfer goes through immediately, without confirmation
        from the recipient. 202 means "accepted".
        """
        if owner == new_owner:
            return
        response = await self._client.post(
            f"/repos/{owner}/{name}/transfer", json={"new_owner": new_owner}
        )
        if response.status_code not in (200, 202):
            response.raise_for_status()


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
