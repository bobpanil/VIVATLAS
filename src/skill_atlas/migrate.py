"""Приведение базы к текущей схеме.

Полноценная система миграций (Alembic) для одного пользователя и одного файла
базы — лишний вес. Здесь хватает трёх шагов: создать недостающие таблицы,
дописать недостающие столбцы, пересобрать таблицу полнотекстового поиска.
Все шаги можно повторять сколько угодно раз.
"""

import logging

from sqlalchemy import text

from skill_atlas.db import engine
from skill_atlas.models import Base

log = logging.getLogger(__name__)

# Столбцы, добавленные после первого выпуска: таблица → столбец → тип.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "artifacts": {
        "file_paths": "TEXT DEFAULT ''",
    },
}

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


def ensure_schema() -> list[str]:
    """Довести базу до текущей схемы. Возвращает список того, что сделали."""
    done: list[str] = []

    Base.metadata.create_all(engine)

    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if not _table_exists(conn, table):
                continue
            present = _existing_columns(conn, table)
            for column, ddl in columns.items():
                if column not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                    done.append(f"добавлен столбец {table}.{column}")

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
