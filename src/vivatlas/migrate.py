"""Приведение базы к текущей схеме.

Полноценная система миграций (Alembic) для одного пользователя и одного файла
базы — лишний вес. Здесь хватает трёх шагов: создать недостающие таблицы,
дописать недостающие столбцы, пересобрать таблицу полнотекстового поиска.
Все шаги можно повторять сколько угодно раз.
"""

import logging

from sqlalchemy import text

from vivatlas.db import engine
from vivatlas.models import Base

log = logging.getLogger(__name__)

# Столбцы, добавленные после первого выпуска: таблица → столбец → тип.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "artifacts": {
        "file_paths": "TEXT DEFAULT ''",
        "category_id": "INTEGER",
        "private_to_user_id": "INTEGER",
        "hidden": "BOOLEAN DEFAULT 0",
        "is_new": "BOOLEAN DEFAULT 0",
        "owner_user_id": "INTEGER",
        # Без DEFAULT намеренно: у существующих строк тут ляжет NULL, и это метка
        # «ещё не мигрировано». После вывода из private_to_user_id NULL исчезнет,
        # и повторный прогон уже ничего не тронет.
        "shared": "BOOLEAN",
    },
    "repositories": {
        "original_url": "VARCHAR(512) DEFAULT ''",
        "remote_created_at": "DATETIME",
        "user_removed": "BOOLEAN DEFAULT 0",
    },
    "sources": {
        "owner_user_id": "INTEGER",
        "token_enc": "VARCHAR(512) DEFAULT ''",
        "last_auto_scan_at": "DATETIME",
    },
    "categories": {
        "icon": "VARCHAR(32) DEFAULT ''",
        "names_json": "TEXT DEFAULT ''",
        # Пусто = общая (админская) папка; задан = личная папка человека. У
        # существующих папок ляжет NULL — они и есть прежние общие. Отдельного
        # backfill не нужно.
        "owner_user_id": "INTEGER",
    },
    "users": {
        # Аватар по умолчанию из набора. У существующих строк ляжет '' —
        # backfill ниже проставит каждому случайный.
        "avatar_preset": "VARCHAR(32) DEFAULT ''",
    },
}

# Индексы, которые модель объявляет на добавленных позже столбцах. create_all не
# трогает уже существующую таблицу, а ALTER ADD COLUMN идёт без индекса, поэтому
# на обновлённой боевой базе их пришлось бы досоздавать вручную. Имена — как их
# даёт SQLAlchemy (ix_<таблица>_<столбец>), чтобы на свежей базе не задвоить.
_ADDED_INDEXES = [
    ("ix_artifacts_owner_user_id", "artifacts", "owner_user_id"),
    ("ix_artifacts_shared", "artifacts", "shared"),
    ("ix_repositories_user_removed", "repositories", "user_removed"),
    ("ix_categories_owner_user_id", "categories", "owner_user_id"),
]

# Полнотекстовый поиск. unicode61 разбирает и русский, и английский;
# remove_diacritics убирает разницу между "ё" и "е".
#
# Таблица хранит свою копию текста (обычный режим, без content=''). Режим
# без содержимого экономит место, но не умеет удалять строки — а нам надо
# обновлять карточки. На нашем объёме копия текста весит единицы мегабайт.
_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS artifacts_fts USING fts5(
    name,
    summary_short,
    summary_normal,
    summary_technical,
    doc_text,
    tokenize="unicode61 remove_diacritics 2"
);
"""


def create_fts_table(conn) -> None:
    """Создать таблицу поиска по словам.

    Отдельно от ensure_schema, чтобы тесты поднимали ту же схему, что и боевая
    база: create_all виртуальные таблицы не создаёт, и без этого поиск по
    словам падает на пустом месте.
    """
    conn.execute(text(_FTS_SQL))


def _fts_is_contentless(conn) -> bool:
    row = conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name='artifacts_fts'")
    ).fetchone()
    return bool(row) and "content=''" in (row[0] or "")


def _existing_columns(conn, table: str) -> set[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {r[1] for r in rows}


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name=:n"),
        {"n": table},
    ).fetchone()
    return row is not None


def derive_ownership(conn) -> int:
    """Вывести owner_user_id/shared из старой отметки private_to_user_id ОДИН раз.

    Задан пользователь — он владелец, карточка личная (shared=0); пусто — общая
    затравка без владельца (shared=1). Метка «ещё не мигрировано» — shared IS
    NULL; после прогона NULL исчезает, и повтор трогает 0 строк (идемпотентно).
    Возвращает, сколько карточек перевели. 0 — колонок ещё нет или всё уже
    переведено. Порядок важен: зовётся ПОСЛЕ правки private в ensure_schema.
    """
    if not _table_exists(conn, "artifacts"):
        return 0
    acols = _existing_columns(conn, "artifacts")
    if not {"shared", "owner_user_id", "private_to_user_id"} <= acols:
        return 0
    return conn.execute(
        text(
            """
            UPDATE artifacts
            SET owner_user_id = private_to_user_id,
                shared = CASE WHEN private_to_user_id IS NULL THEN 1 ELSE 0 END
            WHERE shared IS NULL
            """
        )
    ).rowcount


# Новая таблица категорий: имя больше НЕ глобально-уникальное (иначе двое не
# завели бы по «Дизайну» и сам факт чужой папки утекал бы отказом). Уникальность
# теперь в пределах владельца + частичный индекс для общих имён. Схема повторяет
# модель Category, чтобы ALTER ADD COLUMN потом ничего не досоздавал.
_CATEGORIES_NEW_DDL = """
CREATE TABLE categories_new (
    id INTEGER NOT NULL,
    name VARCHAR(128) NOT NULL,
    names_json TEXT DEFAULT '',
    icon VARCHAR(32) NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL,
    owner_user_id INTEGER,
    PRIMARY KEY (id),
    CONSTRAINT uq_category_owner_name UNIQUE (owner_user_id, name),
    FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE CASCADE
)
"""


def _needs_category_rebuild(cur) -> bool:
    """Осталась ли на categories старая ГЛОБАЛЬНАЯ уникальность имени? Признак —
    уникальный авто-индекс (origin='u') ровно по одному столбцу [name]. После
    пересборки уникальность станет составной (owner_user_id, name), её авто-индекс
    покрывает два столбца, и эта проверка вернёт False — то есть идемпотентно."""
    for _seq, name, unique, origin, *_ in cur.execute("PRAGMA index_list(categories)").fetchall():
        if unique == 1 and origin == "u":
            cols = [ci[2] for ci in cur.execute(f"PRAGMA index_info('{name}')").fetchall()]
            if cols == ["name"]:
                return True
    return False


def rebuild_categories_scope() -> bool:
    """Разово пересобрать categories, сняв глобальную уникальность имени и добавив
    owner_user_id. SQLite не умеет снять inline-UNIQUE через ALTER — только полной
    пересборкой таблицы. Делаем ВНЕ основной транзакции и с foreign_keys=OFF:
    иначе DROP старой таблицы каскадом (ArtifactCategory.category_id ON DELETE
    CASCADE) стёр бы уже перенесённое членство, а по artifacts.category_id
    (SET NULL) — само значение, из которого мы переносим. Возвращает True, если
    пересобрали. Идемпотентно: со второго раза _needs_category_rebuild — False."""
    if engine.dialect.name != "sqlite":
        return False
    raw = engine.raw_connection()
    try:
        dbapi = raw.driver_connection
        cur = dbapi.cursor()
        exists = cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='categories'"
        ).fetchone()
        if not exists or not _needs_category_rebuild(cur):
            return False
        cols = {row[1] for row in cur.execute("PRAGMA table_info(categories)").fetchall()}
        # На самой старой базе часть столбцов могла ещё не появиться (они в
        # _ADDED_COLUMNS, а пересборка идёт ДО ALTER ADD COLUMN). Берём столбец,
        # только если он есть, иначе подставляем значение по умолчанию — COALESCE
        # спасает от NULL, но не от «нет такого столбца».
        owner_sel = "owner_user_id" if "owner_user_id" in cols else "NULL"
        names_sel = "COALESCE(names_json, '')" if "names_json" in cols else "''"
        icon_sel = "COALESCE(icon, '')" if "icon" in cols else "''"
        old_iso = dbapi.isolation_level
        dbapi.isolation_level = None  # ручное управление BEGIN/COMMIT
        try:
            cur.execute("PRAGMA foreign_keys=OFF")
            cur.execute("BEGIN")
            cur.execute(_CATEGORIES_NEW_DDL)
            cur.execute(
                f"""
                INSERT INTO categories_new
                    (id, name, names_json, icon, position, created_at, owner_user_id)
                SELECT id, name, {names_sel}, {icon_sel},
                       COALESCE(position, 0), created_at, {owner_sel}
                FROM categories
                """
            )
            cur.execute("DROP TABLE categories")
            cur.execute("ALTER TABLE categories_new RENAME TO categories")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS ix_categories_owner_user_id "
                "ON categories(owner_user_id)"
            )
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_shared_category_name "
                "ON categories(name) WHERE owner_user_id IS NULL"
            )
            # Проверяем ТОЛЬКО задетые пересборкой таблицы: общий
            # foreign_key_check уронил бы миграцию из-за любой посторонней
            # висящей ссылки в базе, к папкам отношения не имеющей.
            bad = (
                cur.execute("PRAGMA foreign_key_check(categories)").fetchall()
                + cur.execute("PRAGMA foreign_key_check(artifact_categories)").fetchall()
            )
            if bad:
                raise RuntimeError(f"foreign_key_check после пересборки categories: {bad}")
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
        finally:
            cur.execute("PRAGMA foreign_keys=ON")
            dbapi.isolation_level = old_iso
        cur.close()
        return True
    finally:
        raw.close()


def backfill_artifact_categories(conn) -> int:
    """Перенести старое одиночное artifacts.category_id в таблицу-связку
    ArtifactCategory ОДИН раз. NOT EXISTS делает шаг идемпотентным: повтор
    вставит 0 строк. Прежние категории были общими, так что перенесённое членство
    сразу в общих папках — доп. правки владельца не нужны."""
    if not (_table_exists(conn, "artifact_categories") and _table_exists(conn, "artifacts")):
        return 0
    if "category_id" not in _existing_columns(conn, "artifacts"):
        return 0
    return conn.execute(
        text(
            """
            INSERT INTO artifact_categories (artifact_id, category_id, created_at)
            SELECT a.id, a.category_id, CURRENT_TIMESTAMP
            FROM artifacts a
            WHERE a.category_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM artifact_categories ac
                WHERE ac.artifact_id = a.id AND ac.category_id = a.category_id
              )
            """
        )
    ).rowcount


def backfill_avatar_presets(conn) -> int:
    """Проставить аватар по умолчанию из набора тем, у кого его ещё нет.

    Случайность на стороне Python (в SQLite нет удобного выбора из списка
    ключей). Идемпотентно: трогаем только пустые/NULL — повтор даёт 0. Возвращает
    число обновлённых. 0 — столбца/набора нет либо у всех уже задан."""
    if not _table_exists(conn, "users") or "avatar_preset" not in _existing_columns(conn, "users"):
        return 0
    from vivatlas import usericons

    if not usericons.PRESETS:
        return 0
    rows = conn.execute(
        text("SELECT id FROM users WHERE avatar_preset IS NULL OR avatar_preset = ''")
    ).fetchall()
    for (uid,) in rows:
        conn.execute(
            text("UPDATE users SET avatar_preset = :p WHERE id = :id"),
            {"p": usericons.random_preset(), "id": uid},
        )
    return len(rows)


def ensure_schema() -> list[str]:
    """Довести базу до текущей схемы. Возвращает список того, что сделали."""
    done: list[str] = []

    Base.metadata.create_all(engine)

    # Пересборку categories делаем СРАЗУ после create_all и ДО основной
    # транзакции: ей нужен foreign_keys=OFF, а его нельзя переключать внутри
    # открытой транзакции.
    if rebuild_categories_scope():
        done.append("папки-категории пересобраны под личные/общие")

    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if not _table_exists(conn, table):
                continue
            present = _existing_columns(conn, table)
            for column, ddl in columns.items():
                if column not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                    done.append(f"добавлен столбец {table}.{column}")

        # Досоздаём индексы на добавленных столбцах (на этих полях держится вся
        # видимость — без индекса каждый показ каталога сканирует таблицу).
        for ix, table, column in _ADDED_INDEXES:
            if _table_exists(conn, table) and column in _existing_columns(conn, table):
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {ix} ON {table}({column})"))

        # Разовая правка данных: у уже отсканированных карточек личных
        # источников частная зона держалась на владельце источника. Модель
        # видимости перешла на явную отметку private_to_user_id — проставим её
        # тем, где её ещё нет, иначе они стали бы публичными. Идемпотентно:
        # трогаем только NULL.
        if _table_exists(conn, "artifacts") and _table_exists(conn, "sources"):
            fixed = conn.execute(
                text(
                    """
                    UPDATE artifacts SET private_to_user_id = (
                        SELECT s.owner_user_id FROM repositories r
                        JOIN sources s ON s.id = r.source_id
                        WHERE r.id = artifacts.repository_id
                    )
                    WHERE private_to_user_id IS NULL
                      AND repository_id IN (
                        SELECT r.id FROM repositories r
                        JOIN sources s ON s.id = r.source_id
                        WHERE s.owner_user_id IS NOT NULL
                      )
                    """
                )
            ).rowcount
            if fixed:
                done.append(f"частная зона проставлена {fixed} карточкам личных источников")

        # Владение и «общий» выводим из старой отметки private_to_user_id.
        derived = derive_ownership(conn)
        if derived:
            done.append(f"владение/общий выведены из старой зоны для {derived} карточек")

        # Частичный уникальный индекс для ОБЩИХ имён (owner пуст): составная
        # уникальность (owner, name) его не держит, т.к. SQLite считает NULL
        # разными. На пересобранных и свежих базах он уже есть — здесь на всякий.
        if _table_exists(conn, "categories") and "owner_user_id" in _existing_columns(
            conn, "categories"
        ):
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_shared_category_name "
                    "ON categories(name) WHERE owner_user_id IS NULL"
                )
            )

        # Старое одиночное category_id переносим в таблицу-связку ArtifactCategory.
        filed = backfill_artifact_categories(conn)
        if filed:
            done.append(f"членство в папках перенесено для {filed} карточек")

        # У кого ещё нет аватара по умолчанию — назначаем случайный из набора.
        seeded = backfill_avatar_presets(conn)
        if seeded:
            done.append(f"аватар по умолчанию назначен {seeded} пользователям")

        # Первая версия таблицы была без содержимого — такая не умеет удалять
        # строки, а нам надо обновлять карточки. Пересоздаём.
        if _fts_is_contentless(conn):
            conn.execute(text("DROP TABLE artifacts_fts"))
            done.append("пересоздана таблица поиска по словам")

        if not _table_exists(conn, "artifacts_fts"):
            conn.execute(text(_FTS_SQL))
            if "пересоздана таблица поиска по словам" not in done:
                done.append("создана таблица поиска по словам")

    return done


def rebuild_fts() -> int:
    """Перезаполнить таблицу поиска по словам из карточек."""
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM artifacts_fts"))
        conn.execute(
            text(
                """
                INSERT INTO artifacts_fts(rowid, name, summary_short, summary_normal,
                                          summary_technical, doc_text)
                SELECT id, name, summary_short, summary_normal, summary_technical, doc_text
                FROM artifacts
                """
            )
        )
        count = conn.execute(text("SELECT count(*) FROM artifacts_fts")).scalar_one()
    return count
