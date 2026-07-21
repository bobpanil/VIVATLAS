"""Common interface to a repository host.

Gitea is implemented for now. GitHub is added as a separate class with the same
methods — the rest of the code knows nothing about the provider and shouldn't change.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class RepoRef(BaseModel):
    """A repository as the rest of the program sees it."""

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
    original_url: str = ""  # where it was imported from, if Gitea knows
    description: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@runtime_checkable
class GitProvider(Protocol):
    """The set of commands every host must support."""

    name: str

    async def list_repositories(self) -> list[RepoRef]:
        """All repositories visible with the current permissions."""
        ...

    async def get_head_sha(self, repo: RepoRef) -> str:
        """The latest commit of the default branch."""
        ...

    async def download_archive(self, repo: RepoRef, ref: str) -> bytes:
        """The whole repository as a single archive.

        We download an archive rather than files one by one: across hundreds of
        repositories, per-file reads run into request limits.
        """
        ...

    async def blob_shas(self, repo: RepoRef, ref: str) -> dict[str, str]:
        """Snapshots of all files: path -> sha. For comparing against the source."""
        ...

    async def aclose(self) -> None: ...
