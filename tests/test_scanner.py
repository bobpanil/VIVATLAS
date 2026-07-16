from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from vivatlas.models import Base, Repository
from vivatlas.providers.base import RepoRef
from vivatlas.scanner import get_or_create_source, is_scannable, scan_source


def make_repo(**kw) -> RepoRef:
    defaults = dict(
        external_id="1",
        owner="skills-lib",
        name="brandkit",
        default_branch="main",
        is_private=False,
        is_archived=False,
        is_empty=False,
        html_url="https://git.example.com/skills-lib/brandkit",
        clone_url="https://git.example.com/skills-lib/brandkit.git",
        size_kb=24,
        description="",
        updated_at=datetime(2026, 6, 26, tzinfo=UTC),
    )
    return RepoRef(**{**defaults, **kw})


class FakeProvider:
    name = "fake"

    def __init__(self, repos: list[RepoRef]) -> None:
        self._repos = repos

    async def list_repositories(self) -> list[RepoRef]:
        return self._repos

    async def get_head_sha(self, repo): ...
    async def download_archive(self, repo, ref): ...
    async def aclose(self) -> None: ...


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s


# --- правило про приватные ---


def test_private_repo_is_never_scannable():
    assert is_scannable(make_repo(is_private=True)) is False


def test_public_repo_is_scannable():
    assert is_scannable(make_repo(is_private=False)) is True


def test_empty_repo_is_skipped():
    assert is_scannable(make_repo(is_empty=True)) is False


def test_archived_repo_is_still_scannable():
    # Заархивированный — это старый, а не закрытый. Он-то нам и нужен в списке
    # протухшего.
    assert is_scannable(make_repo(is_archived=True)) is True


async def test_private_repos_never_reach_the_database(session):
    provider = FakeProvider(
        [
            make_repo(external_id="1", name="public-one", is_private=False),
            make_repo(external_id="2", name="secret", is_private=True),
            make_repo(external_id="3", name="public-two", is_private=False),
        ]
    )
    source = get_or_create_source(session, "fake", "https://git.example.com", "Fake")

    result = await scan_source(session, provider, source)

    assert result.seen == 3
    assert result.skipped_private == 1
    assert result.added == 2

    names = {r.name for r in session.scalars(select(Repository))}
    assert names == {"public-one", "public-two"}
    assert "secret" not in names


async def test_repo_missing_private_flag_is_treated_as_private():
    from vivatlas.providers.gitea import _to_repo_ref

    # Если хостинг не сказал, приватный ли репозиторий, считаем что да.
    # Ошибиться в эту сторону безопасно, в обратную — нет.
    repo = _to_repo_ref({"id": 7, "owner": {"login": "x"}, "name": "y"})
    assert repo.is_private is True
    assert is_scannable(repo) is False


# --- повторное сканирование ---


async def test_second_scan_updates_instead_of_duplicating(session):
    provider = FakeProvider([make_repo(external_id="1", size_kb=24)])
    source = get_or_create_source(session, "fake", "https://git.example.com", "Fake")
    await scan_source(session, provider, source)

    provider._repos = [make_repo(external_id="1", size_kb=99)]
    result = await scan_source(session, provider, source)

    assert result.added == 0
    assert result.updated == 1
    rows = session.scalars(select(Repository)).all()
    assert len(rows) == 1
    assert rows[0].size_kb == 99


async def test_disappeared_repo_is_marked_not_deleted(session):
    provider = FakeProvider(
        [make_repo(external_id="1", name="stays"), make_repo(external_id="2", name="goes")]
    )
    source = get_or_create_source(session, "fake", "https://git.example.com", "Fake")
    await scan_source(session, provider, source)

    provider._repos = [make_repo(external_id="1", name="stays")]
    result = await scan_source(session, provider, source)

    assert result.gone == 1
    gone = session.scalar(select(Repository).where(Repository.name == "goes"))
    assert gone is not None  # запись осталась
    assert gone.gone_at is not None


async def test_repo_that_becomes_private_disappears_from_listing(session):
    # Репозиторий закрыли. Он пропадает из выдачи, и мы обязаны это заметить.
    provider = FakeProvider([make_repo(external_id="1", name="was-public")])
    source = get_or_create_source(session, "fake", "https://git.example.com", "Fake")
    await scan_source(session, provider, source)

    provider._repos = [make_repo(external_id="1", name="was-public", is_private=True)]
    result = await scan_source(session, provider, source)

    assert result.skipped_private == 1
    row = session.scalar(select(Repository).where(Repository.external_id == "1"))
    assert row.gone_at is not None
