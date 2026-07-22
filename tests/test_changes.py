from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from tests.test_archive import make_tar
from vivatlas import changes
from vivatlas.indexer import index_repository
from vivatlas.models import Artifact, Change, Repository, Source, User
from vivatlas.providers.base import RepoRef
from vivatlas.scanner import get_or_create_source, scan_source


@pytest.fixture
def session(make_session):
    with make_session() as s:
        yield s


def repo_ref(**kw) -> RepoRef:
    base = dict(
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
        updated_at=datetime(2026, 6, 26, tzinfo=UTC),
    )
    return RepoRef(**{**base, **kw})


class FakeProvider:
    name = "fake"

    def __init__(self, repos=None, files=None, sha="abc123") -> None:
        self.repos = repos or []
        self.blob = make_tar(files or {"SKILL.md": b"# Brandkit"})
        self.sha = sha

    async def list_repositories(self):
        return self.repos

    async def get_head_sha(self, repo):
        return self.sha

    async def download_archive(self, repo, ref):
        return self.blob

    async def blob_shas(self, repo, ref):
        return {}

    async def aclose(self): ...


def kinds(session) -> list[str]:
    return [c.kind for c in session.scalars(select(Change).order_by(Change.id))]


# --- scanning ---


async def test_new_repo_is_recorded_as_added(session):
    provider = FakeProvider([repo_ref()])
    source = get_or_create_source(session, "fake", "https://x", "Fake")
    await scan_source(session, provider, source)
    session.commit()

    assert kinds(session) == ["added"]
    c = session.scalar(select(Change))
    assert c.title == "skills-lib/brandkit"


async def test_second_scan_records_nothing_new(session):
    provider = FakeProvider([repo_ref()])
    source = get_or_create_source(session, "fake", "https://x", "Fake")
    await scan_source(session, provider, source)
    session.commit()
    await scan_source(session, provider, source)
    session.commit()

    assert kinds(session) == ["added"], "a repeat scan invented an event"


async def test_disappeared_repo_is_recorded_as_removed(session):
    provider = FakeProvider([repo_ref()])
    source = get_or_create_source(session, "fake", "https://x", "Fake")
    await scan_source(session, provider, source)
    session.commit()

    provider.repos = []
    await scan_source(session, provider, source)
    session.commit()

    assert kinds(session) == ["added", "removed"]


async def test_rename_is_recorded_with_old_name(session):
    provider = FakeProvider([repo_ref(name="old-name")])
    source = get_or_create_source(session, "fake", "https://x", "Fake")
    await scan_source(session, provider, source)
    session.commit()

    provider.repos = [repo_ref(name="new-name")]
    await scan_source(session, provider, source)
    session.commit()

    renamed = session.scalar(select(Change).where(Change.kind == "renamed"))
    assert renamed is not None
    assert "old-name" in renamed.details
    assert renamed.title == "skills-lib/new-name"


# --- building cards ---


@pytest.fixture
def repo_row(session):
    source = Source(kind="fake", base_url="https://x", display_name="Fake")
    session.add(source)
    session.flush()
    row = Repository(
        source_id=source.id,
        external_id="1",
        owner="skills-lib",
        name="brandkit",
        default_branch="main",
    )
    session.add(row)
    session.commit()
    return row


async def test_new_card_is_recorded(session, repo_row):
    await index_repository(session, FakeProvider(), None, repo_row)
    session.commit()
    assert kinds(session) == ["added"]


async def test_rebuild_without_content_change_records_nothing(session, repo_row):
    # Most important: --force must not spawn fake "changed" events.
    provider = FakeProvider()
    await index_repository(session, provider, None, repo_row)
    session.commit()

    for _ in range(3):
        await index_repository(session, provider, None, repo_row, force=True)
        session.commit()

    assert kinds(session) == ["added"], "a rebuild invented changes"


async def test_real_content_change_is_recorded(session, repo_row):
    provider = FakeProvider(files={"SKILL.md": b"# Old"})
    await index_repository(session, provider, None, repo_row)
    session.commit()

    provider.blob = make_tar({"SKILL.md": b"# New and different"})
    provider.sha = "def456"
    await index_repository(session, provider, None, repo_row)
    session.commit()

    assert kinds(session) == ["added", "updated"]


async def test_type_change_is_mentioned(session, repo_row):
    provider = FakeProvider(files={"README.md": b"# Thing"})
    await index_repository(session, provider, None, repo_row)
    session.commit()

    provider.blob = make_tar({"SKILL.md": b"# Now a skill"})
    provider.sha = "def456"
    await index_repository(session, provider, None, repo_row)
    session.commit()

    upd = session.scalar(select(Change).where(Change.kind == "updated"))
    assert "type changed" in upd.details


# --- stale ---


def _artifact_aged(session, days: int, name: str = "old", archived: bool = False):
    source = session.scalar(select(Source)) or Source(
        kind="f", base_url="https://x", display_name="F"
    )
    session.add(source)
    session.flush()
    row = Repository(
        source_id=source.id,
        external_id=name,
        owner="lib",
        name=name,
        default_branch="main",
        is_archived=archived,
        remote_updated_at=datetime.now(UTC) - timedelta(days=days),
    )
    session.add(row)
    session.flush()
    art = Artifact(repository_id=row.id, name=name, artifact_type="skill")
    session.add(art)
    session.commit()
    return art


def test_stale_finds_only_old_ones(session):
    _artifact_aged(session, days=400, name="ancient")
    _artifact_aged(session, days=30, name="fresh")

    items = changes.stale(session)
    assert [i.artifact.name for i in items] == ["ancient"]
    assert items[0].days >= 400


def test_stale_sorted_oldest_first(session):
    _artifact_aged(session, days=400, name="old")
    _artifact_aged(session, days=900, name="older")

    items = changes.stale(session)
    assert [i.artifact.name for i in items] == ["older", "old"]


def test_archived_is_mentioned_in_reason(session):
    _artifact_aged(session, days=400, name="archived-one", archived=True)
    items = changes.stale(session)
    assert "archived" in items[0].reason


def test_gone_repos_are_not_stale_they_are_gone(session):
    art = _artifact_aged(session, days=400, name="deleted")
    art.repository.gone_at = datetime.now(UTC)
    session.commit()

    assert changes.stale(session) == []


def test_threshold_is_adjustable(session):
    _artifact_aged(session, days=100, name="hundred")
    assert changes.stale(session, days=365) == []
    assert len(changes.stale(session, days=50)) == 1


# --- visibility scoping (a person must not see another user's private cards) ---


def _owned_card(session, name: str, owner_id: int, shared: bool):
    source = Source(kind="f", base_url=f"https://x/{name}", display_name="F")
    session.add(source)
    session.flush()
    row = Repository(
        source_id=source.id,
        external_id=name,
        owner="lib",
        name=name,
        default_branch="main",
        remote_updated_at=datetime.now(UTC) - timedelta(days=400),
    )
    session.add(row)
    session.flush()
    art = Artifact(
        repository_id=row.id,
        name=name,
        artifact_type="skill",
        owner_user_id=owner_id,
        shared=shared,
        hidden=False,
    )
    session.add(art)
    session.flush()
    changes.record(session, "added", repository_id=row.id, artifact_id=art.id, title=name)
    session.commit()
    return art


def test_changes_feed_hides_other_users_private_cards(session):
    a = User(email="a@x.com", display_name="A", password_hash="h")
    b = User(email="b@x.com", display_name="B", password_hash="h")
    session.add_all([a, b])
    session.flush()
    _owned_card(session, "a-private", a.id, shared=False)
    _owned_card(session, "shared-one", a.id, shared=True)

    # Owner A sees both; user B sees only the shared one; unscoped (the local CLI) sees all.
    assert {c.title for c in changes.recent(session, user_id=a.id)} == {"a-private", "shared-one"}
    assert {c.title for c in changes.recent(session, user_id=b.id)} == {"shared-one"}
    assert {c.title for c in changes.recent(session)} == {"a-private", "shared-one"}

    # Stale scoping matches: B never sees A's private card as a stale candidate.
    assert {i.artifact.name for i in changes.stale(session, user_id=b.id)} == {"shared-one"}
    assert {i.artifact.name for i in changes.stale(session, user_id=a.id)} == {
        "a-private",
        "shared-one",
    }
