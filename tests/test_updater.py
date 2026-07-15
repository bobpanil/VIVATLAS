from datetime import UTC, datetime

import pytest

from skill_atlas.models import Artifact, Repository, Source, UpstreamLink
from skill_atlas.updater import UpdateRefused, apply_update, plan_update


@pytest.fixture
def session(make_session):
    with make_session() as s:
        yield s


def make_link(session, *, kind="github-file", local="sha-old", upstream="sha-new", baseline=True):
    """Карточка с записанным источником. Отметка — на момент копирования."""
    source = Source(kind="gitea", base_url="https://git.example.com", display_name="Gitea")
    session.add(source)
    session.flush()
    repo = Repository(
        source_id=source.id,
        external_id="1",
        owner="skills-lib",
        name="cohere",
        default_branch="main",
        html_url="https://git.example.com/skills-lib/cohere",
        clone_url="https://git.example.com/skills-lib/cohere.git",
        size_kb=1,
    )
    session.add(repo)
    session.flush()
    art = Artifact(
        repository_id=repo.id,
        name="cohere",
        artifact_type="design-kit",
        anchor_path="DESIGN.md",
        confidence=1.0,
    )
    session.add(art)
    session.flush()
    link = UpstreamLink(
        artifact_id=art.id,
        kind=kind,
        upstream_repo="VoltAgent/awesome-design-md",
        upstream_path="design-md/cohere/DESIGN.md",
        upstream_url="https://github.com/VoltAgent/awesome-design-md",
        discovered_by="тест",
        baseline_local_sha="sha-old" if baseline else "",
        baseline_upstream_sha="sha-old" if baseline else "",
        baseline_at=datetime.now(UTC) if baseline else None,
        last_local_sha=local,
        last_upstream_sha=upstream,
    )
    session.add(link)
    session.flush()
    return link


class FakeGitea:
    """Поддельная Gitea. Настоящую в тестах не трогаем."""

    def __init__(self, *, local_sha="sha-old", after_write=None, fail=False):
        self.local_sha = local_sha
        self.after_write = after_write  # что окажется в файле после записи
        self.fail = fail
        self.writes: list[dict] = []

    async def get_head_sha(self, repo):
        return "head-1"

    async def blob_shas(self, repo, ref):
        if self.writes and self.after_write is not None:
            return {"DESIGN.md": self.after_write}
        return {"DESIGN.md": self.local_sha}

    async def update_file(self, owner, name, path, content, message, sha, branch="main"):
        if self.fail:
            raise RuntimeError("Gitea сказала нет")
        self.writes.append({"path": path, "sha": sha, "content": content, "branch": branch})
        return {}


_NEW_VERSION = "# Cohere, новая версия".encode()


class FakeGitHub:
    def __init__(self, *, shas=None, content=_NEW_VERSION):
        self.shas = shas if shas is not None else {"design-md/cohere/DESIGN.md": "sha-new"}
        self.content = content
        self.asked: list[str] = []

    async def blob_shas(self, repo, branch=""):
        return self.shas

    async def blob(self, repo, sha):
        self.asked.append(sha)
        return self.content


# --- когда обновлять можно ---


@pytest.mark.asyncio
async def test_plan_takes_the_new_version(session):
    link = make_link(session)
    plan = await plan_update(session, FakeGitea(), FakeGitHub(), link)
    assert plan.old_sha == "sha-old"
    assert plan.new_sha == "sha-new"
    assert plan.path == "DESIGN.md"
    assert plan.content == _NEW_VERSION


@pytest.mark.asyncio
async def test_content_is_taken_by_sha_not_by_path(session):
    # Пока мы ходим за содержимым, в ветку может прилететь ещё коммит. Берём
    # ровно тот слепок, который сравнили и показали человеку.
    link = make_link(session)
    gh = FakeGitHub()
    await plan_update(session, FakeGitea(), gh, link)
    assert gh.asked == ["sha-new"]


@pytest.mark.asyncio
async def test_apply_writes_and_moves_the_baseline(session):
    link = make_link(session)
    gitea = FakeGitea(after_write="sha-new")
    plan = await plan_update(session, gitea, FakeGitHub(), link)
    got = await apply_update(session, gitea, FakeGitHub(), plan)

    assert got == "sha-new"
    assert gitea.writes[0]["path"] == "DESIGN.md"
    # Слепок старого файла обязателен: Gitea откажет, если файл успели поправить.
    assert gitea.writes[0]["sha"] == "sha-old"
    assert link.status == "in-sync"
    assert link.baseline_local_sha == "sha-new"
    assert link.baseline_upstream_sha == "sha-new"


# --- когда обновлять нельзя ---


@pytest.mark.asyncio
async def test_your_own_edits_are_never_overwritten(session):
    # Мы правили, у источника без изменений. Перезапись затёрла бы правку.
    link = make_link(session, local="sha-моя-правка", upstream="sha-old")
    gh = FakeGitHub(shas={"design-md/cohere/DESIGN.md": "sha-old"})
    with pytest.raises(UpdateRefused, match="правили"):
        await plan_update(session, FakeGitea(local_sha="sha-моя-правка"), gh, link)


@pytest.mark.asyncio
async def test_diverged_needs_hands(session):
    link = make_link(session, local="sha-моя", upstream="sha-их")
    gh = FakeGitHub(shas={"design-md/cohere/DESIGN.md": "sha-их"})
    with pytest.raises(UpdateRefused, match="руки"):
        await plan_update(session, FakeGitea(local_sha="sha-моя"), gh, link)


@pytest.mark.asyncio
async def test_nothing_to_do_when_already_the_same(session):
    link = make_link(session, local="sha-old", upstream="sha-old")
    gh = FakeGitHub(shas={"design-md/cohere/DESIGN.md": "sha-old"})
    with pytest.raises(UpdateRefused, match="нечего"):
        await plan_update(session, FakeGitea(), gh, link)


@pytest.mark.asyncio
async def test_mirror_is_left_to_gitea(session):
    link = make_link(session, kind="gitea-mirror")
    with pytest.raises(UpdateRefused, match="зеркало"):
        await plan_update(session, FakeGitea(), FakeGitHub(), link)


@pytest.mark.asyncio
async def test_without_a_baseline_we_do_not_guess(session):
    # Разошлось до того, как мы начали следить: новая это версия или наша
    # правка — различить нечем. Молчим, а не гадаем.
    link = make_link(session, baseline=False, local="sha-a", upstream="sha-b")
    gh = FakeGitHub(shas={"design-md/cohere/DESIGN.md": "sha-b"})
    with pytest.raises(UpdateRefused):
        await plan_update(session, FakeGitea(local_sha="sha-a"), gh, link)


@pytest.mark.asyncio
async def test_empty_upstream_file_is_refused(session):
    link = make_link(session)
    with pytest.raises(UpdateRefused, match="пустой"):
        await plan_update(session, FakeGitea(), FakeGitHub(content=b""), link)


# --- проверка записи ---


@pytest.mark.asyncio
async def test_baseline_stays_put_when_the_write_lands_wrong(session):
    # Записали, а получилось другое — например, Gitea поменяла переносы строк.
    # Двинуть отметку значило бы соврать, что копия равна источнику.
    link = make_link(session)
    gitea = FakeGitea(after_write="sha-совсем-другое")
    plan = await plan_update(session, gitea, FakeGitHub(), link)
    with pytest.raises(RuntimeError, match="не то"):
        await apply_update(session, gitea, FakeGitHub(), plan)
    assert link.baseline_local_sha == "sha-old"
    assert link.status != "in-sync"


@pytest.mark.asyncio
async def test_planning_writes_nothing(session):
    link = make_link(session)
    gitea = FakeGitea()
    await plan_update(session, gitea, FakeGitHub(), link)
    assert gitea.writes == []
