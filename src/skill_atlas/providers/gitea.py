"""Gitea."""

import httpx

from skill_atlas.providers.base import RepoRef

# Инстанс отвечает 403 на запросы без узнаваемого User-Agent — проверено на
# git.example.com. Без этого заголовка не работает ни один запрос.
_USER_AGENT = "Mozilla/5.0 (compatible; SkillAtlas/0.1)"

_PAGE_SIZE = 50  # потолок Gitea, больше запрашивать бесполезно


class GiteaProvider:
    name = "gitea"

    def __init__(self, base_url: str, token: str = "", timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"token {token}"
        self._client = httpx.AsyncClient(
            base_url=f"{self.base_url}/api/v1",
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        )

    async def list_repositories(self) -> list[RepoRef]:
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

    async def aclose(self) -> None:
        await self._client.aclose()


def _to_repo_ref(item: dict) -> RepoRef:
    return RepoRef(
        external_id=str(item["id"]),
        owner=item["owner"]["login"],
        name=item["name"],
        default_branch=item.get("default_branch") or "main",
        is_private=bool(item.get("private", True)),  # неизвестно — считаем приватным
        is_archived=bool(item.get("archived", False)),
        is_empty=bool(item.get("empty", False)),
        html_url=item.get("html_url", ""),
        clone_url=item.get("clone_url", ""),
        size_kb=int(item.get("size") or 0),
        description=item.get("description") or "",
        updated_at=item.get("updated_at"),
    )
