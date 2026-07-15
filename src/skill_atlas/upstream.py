"""Откуда взялся инструмент и не вышла ли новая версия.

Источник ищется ТОЛЬКО там, где он записан явно:
  1. Gitea знает, что репозиторий — зеркало (original_url)
  2. В README есть строчка "Source: ... github.com/владелец/репозиторий"

Модель к этому не подпускается намеренно. Попроси её найти источник — она
найдёт, потому что она всегда что-нибудь находит. А потом по этой догадке
кто-нибудь перезапишет файлы. Нет источника — значит нет, так и пишем.

Сравнение идёт по слепкам git (blob sha). Они считаются от содержимого, поэтому
одинаковый файл на GitHub и в Gitea даёт одинаковый слепок — сравнивать можно
напрямую, ничего не скачивая.
"""

import logging
import re
from dataclasses import dataclass

import httpx

from skill_atlas.archive import RepoContents

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
    path: str  # "design-md/cohere/DESIGN.md", пусто у зеркала
    url: str
    discovered_by: str


def detect_from_mirror(original_url: str) -> UpstreamRef | None:
    """Gitea сама знает, откуда привезли репозиторий. Самый надёжный случай."""
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
        discovered_by="Gitea: это зеркало",
    )


def detect_from_readme(
    contents: RepoContents, repo_name: str, anchor_path: str | None
) -> UpstreamRef | None:
    """Строчка "Source: ..." в конце README.

    Ищем по полному тексту файла, а не по обрезанному описанию: у дизайн-наборов
    эта строчка стоит последней и в обрезку не попадает.
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
        discovered_by="строка Source в README",
    )


def _guess_path(upstream_repo: str, repo_name: str, anchor_path: str | None) -> str:
    """Где у источника лежит наш файл.

    Проверено на всех 74 дизайн-наборах: путь всегда design-md/<имя>/DESIGN.md,
    где <имя> — имя нашего репозитория. Правило узкое намеренно: угадывать путь
    для незнакомого источника — значит потом сравнивать не то с не тем.
    """
    if upstream_repo.lower() == "voltagent/awesome-design-md" and anchor_path:
        return f"design-md/{repo_name}/{anchor_path}"
    return ""


class UpstreamChecker:
    """Слепки файлов у источника. Одним запросом на весь репозиторий."""

    def __init__(self, token: str = "", timeout: float = 30.0) -> None:
        headers = {"User-Agent": _UA, "Accept": "application/vnd.github+json"}
        if token:
            # Токен не для доступа — репозитории открытые. Только чтобы GitHub
            # не резал по 60 запросов в час.
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True)
        self._cache: dict[str, dict[str, str]] = {}

    async def blob_shas(self, repo: str, branch: str = "") -> dict[str, str]:
        """Все файлы репозитория со слепками: путь -> sha."""
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
            log.warning("%s: список файлов обрезан гитхабом, часть путей не увидим", repo)

        shas = {t["path"]: t["sha"] for t in data.get("tree", []) if t["type"] == "blob"}
        self._cache[repo] = shas
        return shas

    async def blob(self, repo: str, sha: str) -> bytes:
        """Содержимое файла по его слепку.

        Именно по слепку, а не по пути в ветке: слепок мы уже сравнили и знаем,
        что новая версия — это он. Пока мы ходим за содержимым, в ветку может
        прилететь ещё коммит, и по пути приехало бы не то, что мы показали
        человеку.
        """
        import base64

        response = await self._client.get(f"https://api.github.com/repos/{repo}/git/blobs/{sha}")
        response.raise_for_status()
        data = response.json()
        if data.get("encoding") != "base64":
            raise RuntimeError(f"{repo}@{sha[:8]}: неожиданная упаковка {data.get('encoding')}")
        return base64.b64decode(data["content"])

    async def head_sha(self, repo: str) -> str:
        """Последний коммит — для зеркал, где сравниваем целиком."""
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
    """Что означает расхождение.

    Без отметки на момент копирования "файлы разные" ничего не значит: то ли
    вышла новая версия, то ли пользователь сам поправил. Отметка это различает.
    """
    if not local_sha or not upstream_sha:
        return "unknown"
    if local_sha == upstream_sha:
        return "in-sync"
    if local_sha == baseline_local and upstream_sha != baseline_upstream:
        return "update-available"  # у них новое, у нас нетронуто
    if upstream_sha == baseline_upstream and local_sha != baseline_local:
        return "locally-modified"  # мы правили, у них без изменений
    return "diverged"  # разошлось с обеих сторон


STATUS_NAMES = {
    "in-sync": "совпадает с источником",
    "update-available": "вышла новая версия",
    "locally-modified": "вы правили — обновлять нельзя",
    "diverged": "разошлось с обеих сторон",
    "unknown": "сравнить не с чем",
}
