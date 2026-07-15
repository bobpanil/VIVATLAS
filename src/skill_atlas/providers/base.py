"""Общий интерфейс к хостингу репозиториев.

Сейчас реализован Gitea. GitHub добавляется отдельным классом с теми же
методами — остальной код о провайдере не знает и меняться не должен.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class RepoRef(BaseModel):
    """Репозиторий в том виде, в каком его видит вся остальная программа."""

    external_id: str
    owner: str
    name: str
    default_branch: str
    is_private: bool
    is_archived: bool
    is_empty: bool
    html_url: str
    clone_url: str
    size_kb: int
    original_url: str = ""  # откуда привезли, если Gitea знает
    description: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@runtime_checkable
class GitProvider(Protocol):
    """Набор команд, который должен уметь любой хостинг."""

    name: str

    async def list_repositories(self) -> list[RepoRef]:
        """Все репозитории, видимые с текущими правами."""
        ...

    async def get_head_sha(self, repo: RepoRef) -> str:
        """Последний коммит ветки по умолчанию."""
        ...

    async def download_archive(self, repo: RepoRef, ref: str) -> bytes:
        """Репозиторий целиком одним архивом.

        Качаем архивом, а не файлами по одному: на сотнях репозиториев
        поштучное чтение упирается в лимиты запросов.
        """
        ...

    async def blob_shas(self, repo: RepoRef, ref: str) -> dict[str, str]:
        """Слепки всех файлов: путь -> sha. Для сравнения с источником."""
        ...

    async def aclose(self) -> None: ...
