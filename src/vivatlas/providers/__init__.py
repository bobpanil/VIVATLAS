"""Provider registry."""

from vivatlas.config import settings
from vivatlas.providers.base import GitProvider, RepoRef
from vivatlas.providers.gitea import GiteaProvider

__all__ = ["GitProvider", "RepoRef", "GiteaProvider", "build_provider"]


def build_provider(kind: str = "gitea") -> GitProvider:
    if kind == "gitea":
        return GiteaProvider(
            base_url=settings.gitea_url,
            token=settings.gitea_token,
            timeout=settings.http_timeout_seconds,
        )
    if kind == "github":
        from vivatlas.providers.github import GitHubProvider

        return GitHubProvider(
            user=settings.github_user,
            token=settings.github_token,
            timeout=settings.http_timeout_seconds,
        )
    raise ValueError(f"Unknown provider: {kind}")
