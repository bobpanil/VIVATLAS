"""Разделение владения и видимости: shared/owner_user_id вместо одной зоны."""
import sqlite3
import types
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from vivatlas import filters, migrate, scanner, web
from vivatlas.config import settings
from vivatlas.models import Artifact, Favorite, RemovedNotice, Repository, Source


@pytest.fixture
def session(make_session):
    with make_session() as s:
        yield s


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "ключ-для-тестов-двери-длинный")


def _art(session, name, *, owner_user_id=None, shared=True, hidden=False) -> Artifact:
    src = session.scalar(select(Source))
    if src is None:
        src = Source(kind="f", base_url="https://x", display_name="F")
        session.add(src)
        session.flush()
    repo = Repository(
        source_id=src.id, external_id=name, owner="o", name=name, default_branch="main"
    )
    session.add(repo)
    session.flush()
    art = Artifact(
        repository_id=repo.id,
        name=name,
        artifact_type="skill",
        owner_user_id=owner_user_id,
        shared=shared,
        hidden=hidden,
    )
    session.add(art)
    session.flush()
    return art


def _visible(session, user_id):
    return sorted(
        session.scalars(select(Artifact.name).where(Artifact.id.in_(filters.visible_ids(user_id))))
    )


# --- видимость ---


def test_shared_is_visible_to_everyone(session):
    _art(session, "pub", owner_user_id=1, shared=True)
    assert _visible(session, 1) == ["pub"]  # владельцу
    assert _visible(session, 2) == ["pub"]  # другому
    assert _visible(session, None) == ["pub"]  # анониму


def test_unshared_is_visible_only_to_owner(session):
    _art(session, "mine", owner_user_id=1, shared=False)
    assert _visible(session, 1) == ["mine"]
    assert _visible(session, 2) == []  # чужое личное — никогда
    assert _visible(session, None) == []


def test_hidden_is_never_visible(session):
    _art(session, "seed", owner_user_id=None, shared=True, hidden=True)
    assert _visible(session, 1) == []
    assert _visible(session, None) == []


def test_ownerless_unshared_is_invisible_to_all(session):
    # Ключевой угол: бесхозная неразделённая не должна утечь анониму из-за
    # того, что owner == None совпало бы с отсутствующим пользователем.
    _art(session, "orphan", owner_user_id=None, shared=False)
    assert _visible(session, None) == []
    assert _visible(session, 1) == []


def test_owner_sees_own_private_and_others_shared(session):
    _art(session, "mine", owner_user_id=1, shared=False)
    _art(session, "theirs", owner_user_id=2, shared=True)
    _art(session, "their-private", owner_user_id=2, shared=False)
    assert _visible(session, 1) == ["mine", "theirs"]


# --- фильтр по зоне ---


def test_zone_filter_private_vs_common(session):
    _art(session, "pub", owner_user_id=1, shared=True)
    _art(session, "mine", owner_user_id=1, shared=False)
    common = sorted(
        a.name
        for a in session.scalars(
            filters.apply(select(Artifact), filters.Filters(zone="common"), user_id=1)
        )
    )
    private = sorted(
        a.name
        for a in session.scalars(
            filters.apply(select(Artifact), filters.Filters(zone="private"), user_id=1)
        )
    )
    assert common == ["pub"]
    assert private == ["mine"]


def test_zone_counts(session):
    _art(session, "pub", owner_user_id=1, shared=True)
    _art(session, "mine", owner_user_id=1, shared=False)
    counts = filters.zone_counts(session, user_id=1)
    assert counts == {"common": 1, "private": 1}


# --- удаление и отметки об удалении ---


def test_owner_delete_notifies_favouriters_not_actor(session):
    art = _art(session, "shared-tool", owner_user_id=1, shared=True)
    session.add(Favorite(user_id=2, artifact_id=art.id))  # другой держит в избранном
    session.add(Favorite(user_id=1, artifact_id=art.id))  # владелец тоже
    session.flush()

    web._delete_artifact(session, art, actor_user_id=1)
    session.flush()

    assert session.scalar(select(Artifact).where(Artifact.name == "shared-tool")) is None
    notices = session.scalars(select(RemovedNotice)).all()
    # Уведомлён другой (был в избранном), но не сам удаливший владелец.
    assert {n.user_id for n in notices} == {2}
    assert notices[0].artifact_name == "shared-tool"
    # Избранное подчищено.
    assert session.scalars(select(Favorite)).all() == []


def test_admin_delete_notifies_owner(session):
    art = _art(session, "x", owner_user_id=5, shared=True)
    web._delete_artifact(session, art, actor_user_id=1)  # удаляет администратор, не владелец
    session.flush()
    notices = session.scalars(select(RemovedNotice)).all()
    assert {n.user_id for n in notices} == {5}  # владельцу — отметка


def test_delete_tombstones_repository(session):
    # «Навсегда»: репозиторий помечается удалённым и хоронится, чтобы скан не
    # собрал карточку заново.
    art = _art(session, "t", owner_user_id=1, shared=True)
    rid = art.repository_id
    web._delete_artifact(session, art, actor_user_id=1)
    session.flush()
    repo = session.get(Repository, rid)
    assert repo.user_removed is True
    assert repo.gone_at is not None


# --- безопасный дефолт ---


def test_default_shared_is_false(session):
    # Карточка без явного shared — приватная, а не публичная (безопасный дефолт).
    src = session.scalar(select(Source)) or Source(kind="f", base_url="https://x", display_name="F")
    if src.id is None:
        session.add(src)
        session.flush()
    repo = Repository(source_id=src.id, external_id="d", owner="o", name="d", default_branch="main")
    session.add(repo)
    session.flush()
    a = Artifact(repository_id=repo.id, name="d", artifact_type="skill")
    session.add(a)
    session.flush()
    assert a.shared is False


# --- скан не воскрешает удалённое ---


def _repo_ref(name="t"):
    return types.SimpleNamespace(
        external_id=name, owner="o", name=name, default_branch="main", description="",
        html_url="", clone_url="", size_kb=0, is_archived=False, is_empty=False,
        original_url="", created_at=None, updated_at=None,
    )


def test_scanner_does_not_resurrect_user_removed(session):
    art = _art(session, "t", owner_user_id=1, shared=True)
    repo = session.get(Repository, art.repository_id)
    repo.user_removed = True
    repo.gone_at = datetime.now(UTC)
    scanner._update_row(repo, _repo_ref(), datetime.now(UTC))
    assert repo.gone_at is not None  # остался похоронен


def test_scanner_resurrects_normal_gone_repo(session):
    # Контроль: обычный пропавший (не удалённый человеком) репозиторий вернётся.
    art = _art(session, "t2", owner_user_id=1, shared=True)
    repo = session.get(Repository, art.repository_id)
    repo.gone_at = datetime.now(UTC)  # пропал, но человек не удалял
    scanner._update_row(repo, _repo_ref("t2"), datetime.now(UTC))
    assert repo.gone_at is None  # вернулся


# --- миграция старой зоны ---


def test_migration_derives_owner_and_shared():
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE artifacts (id INTEGER PRIMARY KEY, private_to_user_id INTEGER, "
        "owner_user_id INTEGER, shared BOOLEAN)"
    )
    con.executemany(
        "INSERT INTO artifacts (id, private_to_user_id, owner_user_id, shared) VALUES (?,?,?,?)",
        [
            (1, 5, None, None),  # личная у 5 -> owner 5, shared 0
            (2, None, None, None),  # общая затравка -> owner NULL, shared 1
            (3, 7, 7, 0),  # уже переведена (shared не NULL) -> не трогаем
        ],
    )

    # Оборачиваем sqlite3-соединение так, чтобы derive_ownership (ждущий
    # SQLAlchemy-подобный conn) отработал прямо на нём.
    class _Conn:
        def execute(self, stmt, params=None):
            return con.execute(str(stmt.text if hasattr(stmt, "text") else stmt), params or {})

    derived = migrate.derive_ownership(_Conn())
    assert derived == 2  # тронули только две неперёведённые

    rows = {
        r[0]: (r[1], r[2], r[3])
        for r in con.execute("SELECT id, private_to_user_id, owner_user_id, shared FROM artifacts")
    }
    assert rows[1] == (5, 5, 0)
    assert rows[2] == (None, None, 1)
    assert rows[3] == (7, 7, 0)  # идемпотентность: уже переведённую не трогаем

    # Второй прогон — уже нечего переводить.
    assert migrate.derive_ownership(_Conn()) == 0
