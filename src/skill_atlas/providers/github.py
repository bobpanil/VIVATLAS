"""GitHub — пока не подключён.

Место под будущего провайдера. Чтобы включить: реализовать методы ниже поверх
GitHub REST API и зарегистрировать класс в providers/__init__.py. Остальной код
трогать не придётся — он работает через GitProvider.

Отличия от Gitea, которые здесь придётся учесть:
  - постраничность через заголовок Link, а не через пустую страницу;
  - архив отдаётся редиректом на codeload;
  - лимит запросов виден в заголовках X-RateLimit-*.
"""

from skill_atlas.providers.base import RepoRef


class GitHubProvider:
    name = "github"

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError(
            "Провайдер GitHub ещё не реализован. Сейчас поддерживается только Gitea."
        )

    async def list_repositories(self) -> list[RepoRef]:
        raise NotImplementedError

    async def get_head_sha(self, repo: RepoRef) -> str:
        raise NotImplementedError

    async def download_archive(self, repo: RepoRef, ref: str) -> bytes:
        raise NotImplementedError

    async def aclose(self) -> None:
        raise NotImplementedError
