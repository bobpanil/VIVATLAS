import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from tests.test_archive import make_tar

from skill_atlas.indexer import index_repository
from skill_atlas.models import Artifact, Base, Repository, Source


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        source = Source(kind="fake", base_url="https://x", display_name="Fake")
        s.add(source)
        s.flush()
        s.add(
            Repository(
                source_id=source.id,
                external_id="1",
                owner="skills-lib",
                name="brandkit",
                default_branch="main",
            )
        )
        # Именно commit, а не flush: index_all при ошибке делает rollback, и
        # незафиксированная подготовка теста уехала бы вместе с ним.
        s.commit()
        yield s


class FakeProvider:
    name = "fake"

    def __init__(self, files: dict[str, bytes], sha: str = "abc123") -> None:
        self.blob = make_tar(files)
        self.sha = sha
        self.archive_calls = 0

    async def list_repositories(self):
        return []

    async def get_head_sha(self, repo):
        return self.sha

    async def download_archive(self, repo, ref):
        self.archive_calls += 1
        return self.blob

    async def aclose(self): ...


class FakeModel:
    model = "fake-model"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def generate_json(self, prompt, schema):
        self.calls += 1
        if self.fail:
            raise RuntimeError("квота кончилась")
        return {
            "summary_short": "Коротко",
            "summary_normal": "Нормально",
            "summary_technical": "Технически",
        }

    async def aclose(self): ...


async def test_creates_card_with_summaries(session):
    repo = session.scalar(select(Repository))
    provider = FakeProvider({"SKILL.md": b"# Brandkit"})
    model = FakeModel()

    outcome = await index_repository(session, provider, model, repo)

    assert outcome == "created"
    art = session.scalar(select(Artifact))
    assert art.artifact_type == "skill"
    assert art.summary_short == "Коротко"
    assert art.source_commit == "abc123"


async def test_failed_summary_is_recorded_not_faked(session):
    # Если модель не ответила, карточка остаётся, но описание пустое и с
    # пометкой почему. Притворяться, что описание есть, нельзя.
    repo = session.scalar(select(Repository))
    provider = FakeProvider({"SKILL.md": b"# Brandkit"})
    model = FakeModel(fail=True)

    outcome = await index_repository(session, provider, model, repo)

    assert outcome.endswith("+no-summary")
    art = session.scalar(select(Artifact))
    assert art.summary_short == ""
    assert "квота" in art.summary_error
    assert art.artifact_type == "skill"  # тип всё равно распознали


async def test_same_commit_skips_download(session):
    repo = session.scalar(select(Repository))
    provider = FakeProvider({"SKILL.md": b"# Brandkit"})
    model = FakeModel()

    await index_repository(session, provider, model, repo)
    assert provider.archive_calls == 1

    outcome = await index_repository(session, provider, model, repo)
    assert outcome == "unchanged"
    assert provider.archive_calls == 1  # второй раз не качали
    assert model.calls == 1  # и модель не дёргали


async def test_new_commit_triggers_rebuild(session):
    repo = session.scalar(select(Repository))
    provider = FakeProvider({"SKILL.md": b"# Brandkit"})
    model = FakeModel()
    await index_repository(session, provider, model, repo)

    provider.sha = "def456"
    outcome = await index_repository(session, provider, model, repo)

    assert outcome == "updated"
    assert provider.archive_calls == 2
    assert session.scalar(select(Artifact)).source_commit == "def456"


async def test_card_without_summary_is_retried_on_next_run(session):
    # Описание не вышло — на следующем прогоне пробуем ещё раз, даже если
    # коммит тот же. Иначе карточка навсегда останется без текста.
    repo = session.scalar(select(Repository))
    provider = FakeProvider({"SKILL.md": b"# Brandkit"})

    await index_repository(session, provider, FakeModel(fail=True), repo)
    good = FakeModel()
    outcome = await index_repository(session, provider, good, repo)

    assert outcome != "unchanged"
    assert good.calls == 1
    art = session.scalar(select(Artifact))
    assert art.summary_short == "Коротко"
    assert art.summary_error is None


async def test_work_survives_a_crash_midway(session):
    # Была ошибка: всё сохранялось одной транзакцией в конце. Прогон оборвался
    # на 56-м репозитории из 99 — и вся работа откатилась. Теперь каждая
    # карточка сохраняется сразу, и обрыв уносит с собой только текущую.
    from skill_atlas.indexer import index_all

    source = session.scalar(select(Source))
    for i in range(2, 5):
        session.add(
            Repository(
                source_id=source.id,
                external_id=str(i),
                owner="skills-lib",
                name=f"repo-{i}",
                default_branch="main",
            )
        )
    session.commit()

    class DyingProvider(FakeProvider):
        async def download_archive(self, repo, ref):
            self.archive_calls += 1
            if self.archive_calls > 2:
                raise RuntimeError("сеть отвалилась")
            return self.blob

    provider = DyingProvider({"SKILL.md": b"# x"})
    result = await index_all(session, provider, FakeModel())

    assert result.created == 2
    assert result.failed == 2
    # Главное: две успешные карточки в базе, а не ноль.
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Artifact)) == 2


async def test_no_ai_mode_creates_card_without_summary(session):
    repo = session.scalar(select(Repository))
    provider = FakeProvider({"DESIGN.md": b"# Airbnb", "preview.svg": b"<svg/>"})

    outcome = await index_repository(session, provider, None, repo)

    assert outcome == "created"
    art = session.scalar(select(Artifact))
    assert art.artifact_type == "design-kit"
    assert art.preview_path == "preview.svg"
    assert art.summary_short == ""
    assert art.summary_error is None  # не ошибка, просто не просили


async def test_counters_do_not_double_count_a_failed_row(session):
    # Была ошибка: строка считалась и "обработанной", и "ошибочной" сразу,
    # из-за чего в отчёте выходило больше строк, чем репозиториев.
    from skill_atlas.indexer import index_all

    class AlwaysFailing(FakeProvider):
        async def download_archive(self, repo, ref):
            raise RuntimeError("сеть отвалилась")

    result = await index_all(session, AlwaysFailing({"SKILL.md": b"# x"}), FakeModel())

    assert result.failed == 1
    assert result.processed == 0
    assert result.processed + result.failed == 1  # ровно один репозиторий
