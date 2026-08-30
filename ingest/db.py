"""SQLite connection and schema management."""

import sqlite3
from pathlib import Path

import migrations
from config import DB_PATH

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def connect(db_path=None) -> sqlite3.Connection:
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL lets the Next.js reader query while a backfill is writing.
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# Columns added after the initial schema shipped. CREATE TABLE IF NOT EXISTS
# won't add them to an existing database, so apply them idempotently.
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("watchlist", "reporting_currency", "TEXT"),
    ("watchlist", "adr_ratio", "REAL"),
    ("watchlist", "supported", "INTEGER NOT NULL DEFAULT 1"),
    ("watchlist", "unsupported_reason", "TEXT"),
)


def init_schema(conn: sqlite3.Connection, verbose: bool = False) -> None:
    """Create anything missing, then run versioned migrations.

    Idempotent, and run on every container start — that is how a release
    needing a new table reaches a database that already exists.
    """
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _apply_migrations(conn)
    conn.commit()
    migrations.apply(conn, verbose=verbose)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, column, decl in MIGRATIONS:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def main() -> None:
    conn = connect()
    init_schema(conn, verbose=True)
    tables = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]
    print(f"schema initialized at {DB_PATH}")
    print(f"tables: {', '.join(tables)}")
    conn.close()


if __name__ == "__main__":
    main()
