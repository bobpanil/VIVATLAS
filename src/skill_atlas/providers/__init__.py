"""Реестр провайдеров."""

from skill_atlas.config import settings
from skill_atlas.providers.base import GitProvider, RepoRef
from skill_atlas.providers.gitea import GiteaProvider

__all__ = ["GitProvider", "RepoRef", "GiteaProvider", "build_provider"]


def build_provider(kind: str = "gitea") -> GitProvider:
    if kind == "gitea":
        return GiteaProvider(
            base_url=settings.gitea_url,
            token=settings.gitea_token,
            timeout=settings.http_timeout_seconds,
        )
    if kind == "github":
        from skill_atlas.providers.github import GitHubProvider

        return GitHubProvider()
    raise ValueError(f"Неизвестный провайдер: {kind}")
