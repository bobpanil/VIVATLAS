"""Папки-категории: общие (админские) и личные (пер-user) — права, видимость,
отбор, каскад удаления и перенос старого category_id в связку."""

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.pool import StaticPool

from vivatlas import categories as catperm
from vivatlas import filters as flt
from vivatlas.migrate import backfill_artifact_categories, create_fts_table
from vivatlas.models import (
    Artifact,
    ArtifactCategory,
    Base,
    Category,
    Repository,
    Source,
    User,
)


@pytest.fixture
def session():
    """Сессия с включёнными внешними ключами — как в бою (нужно для каскада)."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi, _rec):
        cur = dbapi.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        create_fts_table(conn)
    from sqlalchemy.orm import sessionmaker

    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _user(s, email, owner=False):
    u = User(email=email, display_name=email, password_hash="h", is_owner=owner)
    s.add(u)
    s.flush()
    return u


def _ensure_user(s, uid):
    """Внешние ключи включены — владелец должен существовать. Заводим по id."""
    if uid is None or s.get(User, uid) is not None:
        return
    s.add(User(id=uid, email=f"u{uid}@x", display_name=f"u{uid}", password_hash="h"))
    s.flush()


def _src(s):
    src = s.scalar(select(Source))
    if src is None:
        src = Source(kind="gitea", base_url="http://x", display_name="S")
        s.add(src)
        s.flush()
    return src


def _art(s, name, owner_user_id=None, shared=True):
    _ensure_user(s, owner_user_id)
    src = _src(s)
    repo = Repository(
        source_id=src.id, external_id=name, owner="o", name=name, default_branch="main"
    )
    s.add(repo)
    s.flush()
    a = Artifact(
        repository_id=repo.id,
        name=name,
        artifact_type="tool",
        owner_user_id=owner_user_id,
        shared=shared,
    )
    s.add(a)
    s.flush()
    return a


def _cat(s, name, owner_user_id=None, position=0):
    _ensure_user(s, owner_user_id)
    c = Category(name=name, owner_user_id=owner_user_id, position=position, names_json="")
    s.add(c)
    s.flush()
    return c


def _file(s, art, cat):
    s.add(ArtifactCategory(artifact_id=art.id, category_id=cat.id))
    s.flush()


# --- видимость папок -------------------------------------------------------


def test_can_view_shared_and_private(session):
    shared = _cat(session, "Общая", owner_user_id=None)
    mine = _cat(session, "Моя", owner_user_id=1)
    assert catperm.can_view(shared, None) is True  # аноним видит общую
    assert catperm.can_view(shared, 2) is True
    assert catperm.can_view(mine, 1) is True  # владелец
    assert catperm.can_view(mine, 2) is False  # чужой
    assert catperm.can_view(mine, None) is False  # аноним


def test_visible_category_ids_scopes(session):
    shared = _cat(session, "Общая", owner_user_id=None)
    a_cat = _cat(session, "Аня", owner_user_id=1)
    b_cat = _cat(session, "Боря", owner_user_id=2)
    ids1 = set(session.scalars(catperm.visible_category_ids(1)))
    assert ids1 == {shared.id, a_cat.id}  # свои личные + общие, чужие — нет
    ids_anon = set(session.scalars(catperm.visible_category_ids(None)))
    assert ids_anon == {shared.id}
    assert b_cat.id not in ids1


# --- права вести папку -----------------------------------------------------


def test_can_manage(session):
    shared = _cat(session, "Общая", owner_user_id=None)
    mine = _cat(session, "Моя", owner_user_id=1)
    # общую ведёт только администратор
    assert catperm.can_manage(shared, user_id=1, is_admin=True) is True
    assert catperm.can_manage(shared, user_id=1, is_admin=False) is False
    # личную — только владелец, администратор чужую личную не ведёт
    assert catperm.can_manage(mine, user_id=1, is_admin=False) is True
    assert catperm.can_manage(mine, user_id=2, is_admin=True) is False


# --- права раскладывать (матрица) ------------------------------------------


def test_can_file_shared_folder(session):
    shared_cat = _cat(session, "Общая", owner_user_id=None)
    shared_art = _art(session, "a", owner_user_id=1, shared=True)
    # в общую папку раскладывает ТОЛЬКО администратор — даже владелец карточки нет
    assert catperm.can_file(shared_art, shared_cat, user_id=9, is_admin=True) is True
    assert catperm.can_file(shared_art, shared_cat, user_id=1, is_admin=False) is False
    assert catperm.can_file(shared_art, shared_cat, user_id=2, is_admin=False) is False


def test_can_file_private_art_never_into_shared(session):
    shared_cat = _cat(session, "Общая", owner_user_id=None)
    priv_art = _art(session, "p", owner_user_id=1, shared=False)
    # личную (не общую) карточку в ОБЩУЮ папку — нельзя даже владельцу/админу
    assert catperm.can_file(priv_art, shared_cat, user_id=1, is_admin=False) is False
    assert catperm.can_file(priv_art, shared_cat, user_id=1, is_admin=True) is False


def test_can_file_own_private_folder(session):
    mine = _cat(session, "Моя", owner_user_id=1)
    other = _cat(session, "Чужая", owner_user_id=2)
    shared_art = _art(session, "a", owner_user_id=9, shared=True)
    # в свою личную папку — любую видимую карточку (видимость проверяет маршрут)
    assert catperm.can_file(shared_art, mine, user_id=1, is_admin=False) is True
    # в чужую личную папку — никогда, даже администратору
    assert catperm.can_file(shared_art, other, user_id=1, is_admin=True) is False


# --- боковой список папок: область и счётчики ------------------------------


def test_category_options_scoping_and_counts(session):
    shared = _cat(session, "Общая", owner_user_id=None, position=0)
    mine = _cat(session, "Моя", owner_user_id=1, position=0)
    other = _cat(session, "Чужая", owner_user_id=2, position=0)
    art = _art(session, "a", owner_user_id=9, shared=True)
    _file(session, art, shared)
    _file(session, art, mine)
    _file(session, art, other)  # членство в ЧУЖОЙ личной папке

    opts = flt.category_options(session, user_id=1)
    values = {o.value for o in opts}
    assert values == {str(shared.id), str(mine.id)}  # чужой личной не видно
    by_id = {o.value: o for o in opts}
    assert by_id[str(shared.id)].count == 1
    assert by_id[str(mine.id)].count == 1
    # общие идут первыми (owned=False), потом личные
    assert opts[0].value == str(shared.id)
    assert by_id[str(mine.id)].owned is True
    assert by_id[str(shared.id)].owned is False

    # другой человек: видит общую (с тем же членством) + свою «Чужую»
    opts2 = flt.category_options(session, user_id=2)
    assert {o.value for o in opts2} == {str(shared.id), str(other.id)}


def test_category_options_private_count_isolated(session):
    """Общая карточка в личной папке одного не попадает в счётчик другого."""
    shared = _cat(session, "Общая", owner_user_id=None)
    mine = _cat(session, "Моя", owner_user_id=1)
    art = _art(session, "a", owner_user_id=9, shared=True)
    _file(session, art, mine)
    # человек 2 видит общую папку, но членства в личной папке 1 не считает
    opts2 = {o.value: o for o in flt.category_options(session, user_id=2)}
    assert str(mine.id) not in opts2  # чужой личной вообще нет
    assert str(shared.id) in opts2
    assert opts2[str(shared.id)].count == 0


# --- отбор по папке (apply) ------------------------------------------------


def test_apply_cat_filter_membership(session):
    shared = _cat(session, "Общая", owner_user_id=None)
    a1 = _art(session, "a1", owner_user_id=9, shared=True)
    a2 = _art(session, "a2", owner_user_id=9, shared=True)
    _file(session, a1, shared)
    f = flt.Filters(cat=str(shared.id))
    ids = set(session.scalars(flt.apply(select(Artifact.id), f, user_id=1)))
    assert ids == {a1.id}  # только член папки
    assert a2.id not in ids


def test_apply_cat_filter_foreign_private_leaks_nothing(session):
    """Фильтр по ЧУЖОЙ личной папке ничего не возвращает — нельзя прощупать."""
    other = _cat(session, "Чужая", owner_user_id=2)
    shared_art = _art(session, "a", owner_user_id=9, shared=True)
    _file(session, shared_art, other)  # общая карточка в личной папке чужого
    f = flt.Filters(cat=str(other.id))
    ids = set(session.scalars(flt.apply(select(Artifact.id), f, user_id=1)))
    assert ids == set()  # хотя карточка общая и видима, членство не утекает


# --- удаление папки: каскад членства ---------------------------------------


def test_delete_category_cascades_membership(session):
    cat = _cat(session, "Общая", owner_user_id=None)
    art = _art(session, "a", owner_user_id=1, shared=True)
    _file(session, art, cat)
    session.commit()

    session.delete(cat)
    session.commit()

    assert session.scalar(select(Artifact).where(Artifact.id == art.id)) is not None
    left = session.scalars(select(ArtifactCategory)).all()
    assert left == []  # членство ушло каскадом, карточка осталась


# --- перенос старого category_id в связку ----------------------------------


def test_backfill_artifact_categories(session):
    cat = _cat(session, "Общая", owner_user_id=None)
    art = _art(session, "a", owner_user_id=1, shared=True)
    art.category_id = cat.id  # старое одиночное поле
    session.flush()

    conn = session.connection()
    n = backfill_artifact_categories(conn)
    assert n == 1
    links = conn.execute(
        text("SELECT artifact_id, category_id FROM artifact_categories")
    ).fetchall()
    assert links == [(art.id, cat.id)]

    # идемпотентно: повтор не задваивает
    assert backfill_artifact_categories(conn) == 0
    links2 = conn.execute(text("SELECT count(*) FROM artifact_categories")).scalar_one()
    assert links2 == 1
