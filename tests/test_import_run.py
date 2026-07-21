import pytest
from sqlalchemy import select

from vivatlas.import_run import execute, record_upstream
from vivatlas.importer import ImportFile, ImportPlan, ImportSource
from vivatlas.models import Artifact, Repository, UpstreamLink


@pytest.fixture
def session(make_session):
    with make_session() as s:
        yield s


def make_plan(files=None, kind="folder") -> ImportPlan:
    return ImportPlan(
        source=ImportSource(
            kind=kind,
            owner="mvanhorn",
            repo="last30days-skill",
            ref="main",
            path="skills/last30days",
        ),
        target_owner="skills-lib",
        target_name="last30days",
        files=files
        if files is not None
        else [
            ImportFile("SKILL.md", b"# Last30Days", "skills/last30days/SKILL.md", "sha-anchor"),
            ImportFile("scripts/run.py", b"print(1)", "skills/last30days/scripts/run.py", "sha-2"),
        ],
    )


class FakeGitea:
    """Fake Gitea. We don't touch the real one in tests."""

    def __init__(self, existing: set[str] | None = None, fail_at: int | None = None) -> None:
        self.existing = existing or set()
        self.fail_at = fail_at
        self.created: list[str] = []
        self.written: list[str] = []
        self.deleted: list[str] = []

    async def repo_exists(self, owner, name):
        return f"{owner}/{name}" in self.existing

    async def create_repo(self, owner, name, description=""):
        self.created.append(f"{owner}/{name}")
        return {
            "id": 777,
            "default_branch": "main",
            "description": description,
            "html_url": f"https://git.example.com/{owner}/{name}",
            "clone_url": f"https://git.example.com/{owner}/{name}.git",
        }

    async def put_file(self, owner, name, path, content, message, branch="main"):
        if self.fail_at is not None and len(self.written) >= self.fail_at:
            raise RuntimeError("network dropped")
        self.written.append(path)
        return {}

    async def delete_repo(self, owner, name):
        self.deleted.append(f"{owner}/{name}")


# --- creation ---


async def test_import_creates_repo_and_writes_files(session):
    gitea = FakeGitea()
    result = await execute(session, gitea, make_plan(), "https://git.example.com")
    session.commit()

    assert gitea.created == ["skills-lib/last30days"]
    assert gitea.written == ["SKILL.md", "scripts/run.py"]
    assert result.files_written == 2

    row = session.scalar(select(Repository))
    assert row.full_name == "skills-lib/last30days"
    assert row.original_url == "https://github.com/mvanhorn/last30days-skill"


async def test_existing_repo_is_never_overwritten(session):
    # The most dangerous thing: wiping out what already exists. Refuse, don't overwrite.
    gitea = FakeGitea(existing={"skills-lib/last30days"})

    with pytest.raises(RuntimeError, match="already exists"):
        await execute(session, gitea, make_plan(), "https://git.example.com")

    assert gitea.created == []
    assert gitea.written == []


# --- rollback ---


async def test_failure_midway_rolls_back(session):
    # Half a repository is worse than nothing: the card comes out wrong, and the
    # source marker will lie.
    files = [ImportFile(f"f{i}.md", b"x", f"up/f{i}.md", f"sha{i}") for i in range(5)]
    gitea = FakeGitea(fail_at=3)

    with pytest.raises(RuntimeError):
        await execute(session, gitea, make_plan(files), "https://git.example.com")

    assert gitea.created == ["skills-lib/last30days"]
    assert len(gitea.written) == 3
    assert gitea.deleted == ["skills-lib/last30days"], "created was not rolled back"


async def test_nothing_lands_in_db_when_import_fails(session):
    gitea = FakeGitea(fail_at=1)
    with pytest.raises(RuntimeError):
        await execute(session, gitea, make_plan(), "https://git.example.com")
    session.rollback()

    assert session.scalars(select(Repository)).all() == []


# --- source marker ---


async def test_baseline_is_honest_by_construction(session):
    # We just copied the files ourselves — so the copy and the original match by
    # construction. The marker can't be "diverged before we started watching".
    gitea = FakeGitea()
    await execute(session, gitea, make_plan(), "https://git.example.com")
    session.commit()

    row = session.scalar(select(Repository))
    art = Artifact(repository_id=row.id, name="last30days", artifact_type="skill")
    session.add(art)
    session.flush()

    link = record_upstream(session, art.id, make_plan())
    session.commit()

    assert link.status == "in-sync"
    assert link.baseline_local_sha == link.baseline_upstream_sha == "sha-anchor"
    assert link.baseline_at is not None
    assert link.discovered_by == "imported by this program"
    assert link.upstream_path == "skills/last30days/SKILL.md"


async def test_without_anchor_status_is_unknown_not_in_sync(session):
    # Nothing to compare — so we say so, instead of pretending "everything matches".
    files = [ImportFile("data.json", b"{}", "up/data.json", "sha-x")]
    gitea = FakeGitea()
    await execute(session, gitea, make_plan(files), "https://git.example.com")
    session.commit()

    row = session.scalar(select(Repository))
    art = Artifact(repository_id=row.id, name="x", artifact_type="unknown")
    session.add(art)
    session.flush()

    link = record_upstream(session, art.id, make_plan(files))
    session.commit()

    assert link.status == "unknown"
    assert "no anchor file" in link.check_error


async def test_imported_repo_is_findable_by_upstream(session):
    gitea = FakeGitea()
    await execute(session, gitea, make_plan(), "https://git.example.com")
    session.commit()

    row = session.scalar(select(Repository))
    art = Artifact(repository_id=row.id, name="last30days", artifact_type="skill")
    session.add(art)
    session.flush()
    record_upstream(session, art.id, make_plan())
    session.commit()

    link = session.scalar(select(UpstreamLink))
    assert link.upstream_repo == "mvanhorn/last30days-skill"


async def test_record_upstream_overwrites_existing_link(session):
    # Card building already creates a source from the original_url that we set
    # ourselves. Our information is more precise — we have snapshots. We need to
    # overwrite, not fail on "such a record already exists".
    from vivatlas.models import UpstreamLink

    gitea = FakeGitea()
    await execute(session, gitea, make_plan(), "https://git.example.com")
    session.commit()

    row = session.scalar(select(Repository))
    art = Artifact(repository_id=row.id, name="last30days", artifact_type="skill")
    session.add(art)
    session.flush()

    # someone already created a record, without snapshots
    session.add(
        UpstreamLink(
            artifact_id=art.id,
            kind="gitea-mirror",
            upstream_repo="mvanhorn/last30days-skill",
            discovered_by="Gitea: this is a mirror",
            status="unknown",
        )
    )
    session.commit()

    link = record_upstream(session, art.id, make_plan())
    session.commit()

    assert len(session.scalars(select(UpstreamLink)).all()) == 1, "a second record was created"
    assert link.discovered_by == "imported by this program"
    assert link.status == "in-sync"
    assert link.baseline_local_sha == "sha-anchor"
