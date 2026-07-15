"""Притащить инструмент по ссылке.

Смысл затеи: если притащили мы, то источник известен по построению. Не надо
его искать в README и угадывать — мы сами его записали. Отметка baseline
получается идеальной: в момент импорта копия и оригинал совпадают заведомо.

Порядок работы всегда один: разобрать ссылку -> показать план -> дождаться
подтверждения -> выполнить. Без подтверждения ничего не создаётся.
"""

import logging
import re
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; SkillAtlas/0.1)"

# Ограничиваем по весу, а не по числу файлов: настоящий скилл может состоять
# из сотни мелких файлов (mvanhorn/last30days-skill — 121 файл), и отвергать
# его за количество неправильно. Потолок высокий, потому что он защищает
# только от явной ошибки — ссылки на чужой монорепозиторий (49 МБ).
MAX_FILES = 500
MAX_TOTAL_BYTES = 25_000_000


_SKIP_DIRS = (".git/", "node_modules/", ".github/workflows/")


class ImportError_(RuntimeError):
    pass


@dataclass
class ImportSource:
    """Что именно просят притащить."""

    kind: str  # repo | folder | file
    owner: str
    repo: str
    ref: str = ""  # ветка, пусто = по умолчанию
    path: str = ""  # папка или файл внутри репозитория

    @property
    def full_repo(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def suggested_name(self) -> str:
        """Как назвать у себя."""
        if self.kind == "repo":
            return self.repo
        if self.kind == "folder":
            return self.path.rstrip("/").rsplit("/", 1)[-1]
        return self.path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


@dataclass
class ImportFile:
    path: str  # путь у нас
    content: bytes
    upstream_path: str  # путь у источника
    sha: str  # слепок git — сразу годится в отметку


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


# Файлы, по которым видно "здесь лежит отдельный инструмент".
_ANCHOR_NAMES = ("skill.md", "design.md", "mcp.json", "plugin.json", "agents.md")


def find_tool_folders(blobs: list[dict]) -> list[str]:
    """Папки внутри репозитория, похожие на отдельные инструменты.

    Нужно, когда ссылку дали на весь репозиторий, а инструмент внутри. Так
    устроен mvanhorn/last30days-skill: 400 файлов, а скилл в skills/last30days/.
    Вместо бесполезного отказа показываем, что человек, вероятно, имел в виду.
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


# --- разбор ссылки ---

_GITHUB = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[\w.\-]+)/(?P<repo>[\w.\-]+?)(?:\.git)?"
    r"(?:/(?P<kind>tree|blob)/(?P<ref>[^/]+)(?:/(?P<path>.*))?)?/?$"
)
_RAW = re.compile(
    r"^https?://raw\.githubusercontent\.com/(?P<owner>[\w.\-]+)/(?P<repo>[\w.\-]+)"
    r"/(?P<ref>[^/]+)/(?P<path>.+)$"
)


def parse_url(url: str) -> ImportSource:
    """Разобрать ссылку на GitHub. Другие хостинги пока не умеем — и говорим
    об этом прямо, а не притворяемся."""
    url = url.strip()

    m = _RAW.match(url)
    if m:
        return ImportSource(
            kind="file", owner=m["owner"], repo=m["repo"], ref=m["ref"], path=m["path"]
        )

    m = _GITHUB.match(url)
    if not m:
        raise ImportError_(
            f"Не разобрал ссылку: {url}\nПока умею только github.com и raw.githubusercontent.com."
        )

    kind_marker = m["kind"]
    path = (m["path"] or "").strip("/")

    if not kind_marker:
        return ImportSource(kind="repo", owner=m["owner"], repo=m["repo"])
    if kind_marker == "blob":
        return ImportSource(kind="file", owner=m["owner"], repo=m["repo"], ref=m["ref"], path=path)
    # tree без пути — это просто ветка, то есть весь репозиторий
    if not path:
        return ImportSource(kind="repo", owner=m["owner"], repo=m["repo"], ref=m["ref"])
    return ImportSource(kind="folder", owner=m["owner"], repo=m["repo"], ref=m["ref"], path=path)


# --- скачивание с GitHub ---


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
            raise ImportError_(f"Нет такого репозитория: {source.full_repo}")
        r.raise_for_status()
        return r.json()

    async def fetch(self, source: ImportSource) -> tuple[list[ImportFile], list[str]]:
        """Файлы источника. Возвращает (файлы, предупреждения)."""
        info = await self.repo_info(source)
        ref = source.ref or info["default_branch"]
        warnings: list[str] = []

        if info.get("archived"):
            warnings.append("репозиторий заархивирован — обновлений больше не будет")
        if info.get("private"):
            raise ImportError_("репозиторий приватный — не тащим")

        r = await self._client.get(
            f"/repos/{source.full_repo}/git/trees/{ref}", params={"recursive": "1"}
        )
        r.raise_for_status()
        tree = r.json()
        if tree.get("truncated"):
            warnings.append("список файлов обрезан гитхабом — возможно, взяли не всё")

        blobs = [t for t in tree.get("tree", []) if t["type"] == "blob"]

        if source.kind == "repo":
            wanted = blobs
            strip = ""
        elif source.kind == "folder":
            prefix = source.path.rstrip("/") + "/"
            wanted = [t for t in blobs if t["path"].startswith(prefix)]
            strip = prefix
            if not wanted:
                raise ImportError_(f"В {source.full_repo} нет папки {source.path}")
        else:
            wanted = [t for t in blobs if t["path"] == source.path]
            strip = source.path.rsplit("/", 1)[0] + "/" if "/" in source.path else ""
            if not wanted:
                raise ImportError_(f"В {source.full_repo} нет файла {source.path}")

        wanted = [t for t in wanted if not any(s in t["path"] for s in _SKIP_DIRS)]

        weight = sum(t.get("size", 0) for t in wanted)
        if weight > MAX_TOTAL_BYTES or len(wanted) > MAX_FILES:
            # Не просто отказать, а подсказать. Настоящий случай:
            # mvanhorn/last30days-skill — 400 файлов, но сам скилл лежит в
            # skills/last30days/. Репозиторий не равен инструменту.
            hints = find_tool_folders(blobs)
            message = (
                f"Файлов {len(wanted)}, весом {weight / 1024 / 1024:.1f} МБ. "
                f"Похоже, это целый проект, а не один инструмент."
            )
            if hints:
                message += "\n\nВот что внутри похоже на отдельные инструменты:"
                for h in hints[:6]:
                    message += f"\n  https://github.com/{source.full_repo}/tree/{ref}/{h}"
            else:
                message += "\n\nЕсли нужен весь репозиторий целиком — тащите зеркалом."
            raise ImportError_(message)

        # Содержимое качаем одним архивом, а не файлами по одному: у
        # last30days-skill в папке 121 файл — это был бы 121 запрос, и лимиты
        # гитхаба кончились бы на первом же импорте.
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
                    sha=entry["sha"],  # слепок из дерева — годится в отметку
                )
            )

        if excluded:
            # Это не ошибка, а замысел автора. В .gitattributes помечают
            # export-ignore то, что не нужно для работы: тесты, документацию,
            # картинки. У mvanhorn/last30days-skill так исключены 14 МБ демо.
            # Важно: Claude Code при установке скачивает ровно этот же архив,
            # значит архив и есть канонический вид инструмента.
            weight_mb = sum(e.get("size", 0) for e in excluded) / 1024 / 1024
            folders = sorted({e["path"].split("/")[-2] for e in excluded if "/" in e["path"]})
            warnings.append(
                f"автор исключил из архива {len(excluded)} файлов "
                f"({weight_mb:.1f} МБ, папки: {', '.join(folders[:4])}) — "
                f"пометил их в .gitattributes как ненужные для работы. "
                f"Claude Code при установке получает ровно этот же архив."
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
                # архив завёрнут в одну папку вида owner-repo-sha/
                parts = member.name.split("/", 1)
                if len(parts) != 2:
                    continue
                f = tar.extractfile(member)
                if f is not None:
                    out[parts[1]] = f.read()
        return out

    async def aclose(self) -> None:
        await self._client.aclose()


# --- план ---


async def plan_import(
    fetcher: GitHubFetcher,
    url: str,
    target_owner: str,
    target_name: str = "",
    method: str = "copy",
) -> ImportPlan:
    source = parse_url(url)

    if method == "mirror" and source.kind != "repo":
        raise ImportError_(
            "Зеркало умеет только целый репозиторий. Папку или файл можно только скопировать."
        )

    files, warnings = await fetcher.fetch(source)
    plan = ImportPlan(
        source=source,
        target_owner=target_owner,
        target_name=target_name or source.suggested_name,
        files=files,
        method=method,
        warnings=warnings,
    )

    if not any(f.path.lower() in ("skill.md", "design.md", "readme.md") for f in files):
        plan.warnings.append("нет ни SKILL.md, ни README.md — тип определится плохо")

    return plan
