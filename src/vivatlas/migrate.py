"""Bring the database up to the current schema.

A full-blown migration system (Alembic) is dead weight for a single user and a
single database file. Three steps are enough here: create missing tables,
add missing columns, rebuild the full-text search table.
Every step can be repeated any number of times.
"""

import logging

from sqlalchemy import text

from vivatlas.db import engine
from vivatlas.models import Base

log = logging.getLogger(__name__)

# Columns added after the first release: table → column → type.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "artifacts": {
        "file_paths": "TEXT DEFAULT ''",
        "category_id": "INTEGER",
        "private_to_user_id": "INTEGER",
        "hidden": "BOOLEAN DEFAULT 0",
        "is_new": "BOOLEAN DEFAULT 0",
        "owner_user_id": "INTEGER",
        # No DEFAULT on purpose: existing rows get NULL here, which flags them as
        # "not yet migrated". Once derived from private_to_user_id the NULL is gone,
        # and a repeat run touches nothing.
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
        # Empty = shared (admin) folder; set = a user's personal folder. Existing
        # folders get NULL — those are the former shared ones. No separate
        # backfill needed.
        "owner_user_id": "INTEGER",
    },
    "users": {
        # Default avatar from the preset set. Existing rows get '' — the
        # backfill below assigns each a random one.
        "avatar_preset": "VARCHAR(32) DEFAULT ''",
        # Administrator flag; existing rows default to not-admin (the owner already
        # has is_owner and is treated as an admin in code).
        "is_admin": "BOOLEAN DEFAULT 0",
    },
}

# Indexes the model declares on columns added later. create_all doesn't touch an
# already-existing table, and ALTER ADD COLUMN comes without an index, so on an
# upgraded production database they'd have to be created by hand. Names match what
# SQLAlchemy gives (ix_<table>_<column>) so a fresh database doesn't double them.
_ADDED_INDEXES = [
    ("ix_artifacts_owner_user_id", "artifacts", "owner_user_id"),
    ("ix_artifacts_shared", "artifacts", "shared"),
    ("ix_repositories_user_removed", "repositories", "user_removed"),
    ("ix_categories_owner_user_id", "categories", "owner_user_id"),
]

# Full-text search. unicode61 parses both Russian and English;
# remove_diacritics erases the difference between the Russian letters yo and ye.
#
# The table keeps its own copy of the text (normal mode, without content=''). The
# contentless mode saves space but can't delete rows — and we need to
# update cards. At our volume the text copy weighs a few megabytes.
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
    """Create the full-text search table.

    Kept separate from ensure_schema so tests bring up the same schema as the
    production database: create_all doesn't create virtual tables, and without
    this full-text search fails out of the gate.
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
    """Derive owner_user_id/shared from the old private_to_user_id flag ONCE.

    User set — they're the owner, the card is private (shared=0); empty — a
    shared seed with no owner (shared=1). The "not yet migrated" flag is shared IS
    NULL; after a run the NULL is gone, and a repeat touches 0 rows (idempotent).
    Returns how many cards were converted. 0 — the columns aren't there yet or
    everything is already converted. Order matters: called AFTER the private fixup in ensure_schema.
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


# New categories table: the name is NO longer globally unique (else two people
# couldn't both make a "Design" folder and someone else's folder would leak via a
# rejection). Uniqueness is now per-owner + a partial index for shared names. The
# schema mirrors the Category model so ALTER ADD COLUMN creates nothing later.
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
    """Does categories still carry the old GLOBAL name uniqueness? The tell is a
    unique auto-index (origin='u') over exactly one column [name]. After the
    rebuild uniqueness becomes composite (owner_user_id, name), its auto-index
    covers two columns, and this check returns False — i.e. idempotent."""
    for _seq, name, unique, origin, *_ in cur.execute("PRAGMA index_list(categories)").fetchall():
        if unique == 1 and origin == "u":
            cols = [ci[2] for ci in cur.execute(f"PRAGMA index_info('{name}')").fetchall()]
            if cols == ["name"]:
                return True
    return False


def rebuild_categories_scope() -> bool:
    """Rebuild categories once, dropping global name uniqueness and adding
    owner_user_id. SQLite can't drop an inline UNIQUE via ALTER — only via a full
    table rebuild. Done OUTSIDE the main transaction and with foreign_keys=OFF:
    otherwise dropping the old table would cascade (ArtifactCategory.category_id ON
    DELETE CASCADE) and wipe already-migrated membership, and via artifacts.category_id
    (SET NULL) the very value we migrate from. Returns True if it
    rebuilt. Idempotent: on the second pass _needs_category_rebuild is False."""
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
        # On the oldest database some columns may not exist yet (they're in
        # _ADDED_COLUMNS, and the rebuild runs BEFORE ALTER ADD COLUMN). We take a
        # column only if it exists, otherwise substitute a default — COALESCE
        # saves us from NULL, but not from "no such column".
        owner_sel = "owner_user_id" if "owner_user_id" in cols else "NULL"
        names_sel = "COALESCE(names_json, '')" if "names_json" in cols else "''"
        icon_sel = "COALESCE(icon, '')" if "icon" in cols else "''"
        old_iso = dbapi.isolation_level
        dbapi.isolation_level = None  # manual BEGIN/COMMIT control
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
            # Check ONLY the tables touched by the rebuild: a global
            # foreign_key_check would crash the migration over any unrelated
            # dangling reference in the database that has nothing to do with folders.
            bad = (
                cur.execute("PRAGMA foreign_key_check(categories)").fetchall()
                + cur.execute("PRAGMA foreign_key_check(artifact_categories)").fetchall()
            )
            if bad:
                raise RuntimeError(f"foreign_key_check after categories rebuild: {bad}")
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
    """Migrate the old single artifacts.category_id into the ArtifactCategory
    link table ONCE. NOT EXISTS makes the step idempotent: a repeat inserts
    0 rows. Former categories were shared, so the migrated membership lands
    straight in shared folders — no extra owner fixup needed."""
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
    """Assign a default avatar from the preset set to whoever doesn't have one yet.

    Randomness lives on the Python side (SQLite has no handy pick from a list of
    keys). Idempotent: we touch only empty/NULL — a repeat yields 0. Returns
    the number updated. 0 — the column/preset set is missing or everyone already has one."""
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
    """Bring the database up to the current schema. Returns a list of what was done."""
    done: list[str] = []

    Base.metadata.create_all(engine)

    # Rebuild categories RIGHT after create_all and BEFORE the main
    # transaction: it needs foreign_keys=OFF, which can't be toggled inside
    # an open transaction.
    if rebuild_categories_scope():
        done.append("category folders rebuilt for personal/shared")

    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if not _table_exists(conn, table):
                continue
            present = _existing_columns(conn, table)
            for column, ddl in columns.items():
                if column not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                    done.append(f"added column {table}.{column}")

        # Create the indexes on the added columns (all visibility rests on these
        # fields — without an index every catalogue view scans the table).
        for ix, table, column in _ADDED_INDEXES:
            if _table_exists(conn, table) and column in _existing_columns(conn, table):
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {ix} ON {table}({column})"))

        # One-time data fixup: for already-scanned cards of personal
        # sources the private zone rode on the source owner. The visibility
        # model moved to an explicit private_to_user_id flag — set it on
        # those that don't have it yet, otherwise they'd become public. Idempotent:
        # we touch only NULL.
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
                done.append(f"private zone set on {fixed} cards of personal sources")

        # Derive ownership and "shared" from the old private_to_user_id flag.
        derived = derive_ownership(conn)
        if derived:
            done.append(f"ownership/shared derived from the old zone for {derived} cards")

        # Partial unique index for SHARED names (owner empty): the composite
        # uniqueness (owner, name) doesn't cover it, since SQLite treats NULLs
        # as distinct. On rebuilt and fresh databases it already exists — here just in case.
        if _table_exists(conn, "categories") and "owner_user_id" in _existing_columns(
            conn, "categories"
        ):
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_shared_category_name "
                    "ON categories(name) WHERE owner_user_id IS NULL"
                )
            )

        # Migrate the old single category_id into the ArtifactCategory link table.
        filed = backfill_artifact_categories(conn)
        if filed:
            done.append(f"folder membership migrated for {filed} cards")

        # Whoever lacks a default avatar — assign a random one from the set.
        seeded = backfill_avatar_presets(conn)
        if seeded:
            done.append(f"default avatar assigned to {seeded} users")

        # The first version of the table was contentless — that can't delete
        # rows, and we need to update cards. Recreate it.
        if _fts_is_contentless(conn):
            conn.execute(text("DROP TABLE artifacts_fts"))
            done.append("full-text search table recreated")

        if not _table_exists(conn, "artifacts_fts"):
            conn.execute(text(_FTS_SQL))
            if "full-text search table recreated" not in done:
                done.append("full-text search table created")

    return done


def rebuild_fts() -> int:
    """Refill the full-text search table from the cards."""
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
