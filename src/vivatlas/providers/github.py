"""GitHub — not wired up yet.

A placeholder for a future provider. To enable it: implement the methods below on top of
the GitHub REST API and register the class in providers/__init__.py. The rest of the code
won't need touching — it works through GitProvider.

Differences from Gitea to account for here:
  - pagination via the Link header, not via an empty page;
  - the archive is served as a redirect to codeload;
  - the rate limit is visible in the X-RateLimit-* headers.
"""

from vivatlas.providers.base import RepoRef


class GitHubProvider:
    name = "github"

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError(
            "The GitHub provider is not implemented yet. Only Gitea is currently supported."
        )

    async def list_repositories(self) -> list[RepoRef]:
        raise NotImplementedError

    async def get_head_sha(self, repo: RepoRef) -> str:
        raise NotImplementedError

    async def download_archive(self, repo: RepoRef, ref: str) -> bytes:
        raise NotImplementedError

    async def blob_shas(self, repo: RepoRef, ref: str) -> dict[str, str]:
        raise NotImplementedError

    async def aclose(self) -> None:
        raise NotImplementedError
