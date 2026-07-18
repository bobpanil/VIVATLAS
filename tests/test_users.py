"""Управление людьми (Phase B): приглашения и удаление человека.

Логику маршрутов (register/join/admin) проверяем вживую в браузере; здесь —
критичное разрушительное ядро: перенос общих карточек администратору, удаление
личных с уведомлением избранного, каскад личных папок/источников."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vivatlas import auth
from vivatlas.admin_web import _purge_user
from vivatlas.migrate import create_fts_table
from vivatlas.models import (
    Artifact,
    ArtifactCategory,
    Base,
    Category,
    Favorite,
    Invite,
    RemovedNotice,
    Repository,
    Source,
    User,
)


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    from vivatlas.config import settings

    monkeypatch.setattr(settings, "secret_key", "тестовый-ключ-достаточной-длины-для-подписи")


@pytest.fixture
def session():
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
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _user(s, email, owner=False):
    u = User(email=email, display_name=email, password_hash="h", is_owner=owner)
    s.add(u)
    s.flush()
    return u


def _art(s, owner_id, shared):
    src = s.scalar(select(Source))
    if src is None:
        src = Source(kind="gitea", base_url="http://x", display_name="S", owner_user_id=owner_id)
        s.add(src)
        s.flush()
    repo = Repository(
        source_id=src.id, external_id=f"r{owner_id}-{shared}", owner="o",
        name=f"repo{owner_id}{shared}", default_branch="main",
    )
    s.add(repo)
    s.flush()
    a = Artifact(
        repository_id=repo.id, name=f"art-{owner_id}-{shared}", artifact_type="tool",
        owner_user_id=owner_id, shared=shared,
    )
    s.add(a)
    s.flush()
    return a


# --- приглашения -----------------------------------------------------------


def test_invite_make_read_consume(session):
    admin = _user(session, "a@x", owner=True)
    raw = auth.make_invite(session, "guest@x", admin.id)
    session.flush()
    inv = auth.read_invite(session, raw)
    assert inv is not None
    assert inv.email == "guest@x"
    assert auth.read_invite(session, "wrong-code") is None

    user = _user(session, "guest@x")
    assert auth.consume_invite(session, inv, user) is True
    session.flush()
    # одноразовость: принятое приглашение больше не читается
    assert auth.read_invite(session, raw) is None


def test_consume_invite_atomic_second_fails(session):
    """Гонка: то же приглашение нельзя принять дважды. Второй раз rowcount==0."""
    admin = _user(session, "a@x", owner=True)
    raw = auth.make_invite(session, "", admin.id)
    session.flush()
    inv = auth.read_invite(session, raw)
    u1 = _user(session, "one@x")
    assert auth.consume_invite(session, inv, u1) is True
    u2 = _user(session, "two@x")
    # used_at уже стоит — условный UPDATE не сработает
    assert auth.consume_invite(session, inv, u2) is False


def test_invite_expired(session):
    admin = _user(session, "a@x", owner=True)
    raw = auth.make_invite(session, "", admin.id)
    session.flush()
    inv = session.scalar(select(Invite))
    inv.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    session.flush()
    assert auth.read_invite(session, raw) is None


# --- удаление человека -----------------------------------------------------


def test_purge_user_transfers_shared_deletes_private(session):
    admin = _user(session, "admin@x", owner=True)
    victim = _user(session, "victim@x")
    fan = _user(session, "fan@x")

    shared = _art(session, victim.id, shared=True)  # общая — уйдёт админу
    private = _art(session, victim.id, shared=False)  # личная — удалится

    # фанат держит ЛИЧНУЮ карточку жертвы в избранном → должен получить уведомление
    session.add(Favorite(user_id=fan.id, artifact_id=private.id))
    # личная папка жертвы с обеими карточками
    cat = Category(name="Моё", owner_user_id=victim.id, names_json="")
    session.add(cat)
    session.flush()
    session.add(ArtifactCategory(artifact_id=shared.id, category_id=cat.id))
    session.add(ArtifactCategory(artifact_id=private.id, category_id=cat.id))
    session.flush()

    shared_id, private_id, cat_id = shared.id, private.id, cat.id
    _purge_user(session, victim, admin.id)
    session.commit()  # commit + expire, чтобы увидеть каскады БД (не кэш сессии)

    # общая карточка осталась в каталоге, теперь за админом
    kept = session.get(Artifact, shared_id)
    assert kept is not None and kept.owner_user_id == admin.id and kept.shared is True
    # личная карточка удалена целиком
    assert session.get(Artifact, private_id) is None
    # личная папка жертвы ушла каскадом
    assert session.get(Category, cat_id) is None
    # фанату — «что-то пропало» про личную карточку; про общую (осталась) — нет
    notices = session.scalars(select(RemovedNotice).where(RemovedNotice.user_id == fan.id)).all()
    assert len(notices) == 1
    # сам человек удалён
    assert session.get(User, victim.id) is None


def test_purge_user_clears_legacy_private_to_user_id(session):
    """Переданная админу общая карточка не должна каскадом уйти по ветхому
    private_to_user_id (FK CASCADE), который у миграционных строк ещё стоит."""
    admin = _user(session, "admin@x", owner=True)
    victim = _user(session, "victim@x")
    art = _art(session, victim.id, shared=True)
    art.private_to_user_id = victim.id  # как у мигрированной со старой зоны строки
    session.flush()
    art_id = art.id

    _purge_user(session, victim, admin.id)
    session.commit()

    kept = session.get(Artifact, art_id)
    assert kept is not None  # не снесена каскадом
    assert kept.owner_user_id == admin.id
    assert kept.private_to_user_id is None  # связь с ушедшим разорвана
    assert session.get(User, victim.id) is None


def test_purge_user_reassigns_sources_clears_token(session):
    admin = _user(session, "admin@x", owner=True)
    victim = _user(session, "victim@x")
    src = Source(
        kind="gitea", base_url="http://x", display_name="S",
        owner_user_id=victim.id, token_enc="зашифрованный-токен",
    )
    session.add(src)
    session.flush()
    # у источника есть репозиторий — прямое удаление источника упало бы на FK
    session.add(
        Repository(source_id=src.id, external_id="r1", owner="o", name="r1", default_branch="main")
    )
    session.flush()
    src_id = src.id

    _purge_user(session, victim, admin.id)
    session.commit()  # commit + expire, чтобы увидеть каскады БД (не кэш сессии)

    moved = session.get(Source, src_id)
    assert moved is not None  # источник не удалён (репозиторий бы заблокировал)
    assert moved.owner_user_id == admin.id  # передан админу
    assert moved.token_enc == ""  # чужой токен очищен
    assert session.get(User, victim.id) is None


# --- переключатель регистрации ---------------------------------------------


def test_registration_toggle(session):
    from vivatlas import runtime_settings

    assert runtime_settings.registration_open(session) is True  # по умолчанию открыта
    runtime_settings.set_bool(session, runtime_settings.REGISTRATION_OPEN, False)
    session.flush()
    assert runtime_settings.registration_open(session) is False
