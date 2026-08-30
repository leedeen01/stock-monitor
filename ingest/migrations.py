"""Versioned schema migrations.

`schema.sql` creates tables that do not exist yet; it cannot reshape ones that
do. Anything that splits a table, adds a column to an existing one, or moves
data belongs here, numbered and applied once.

Each migration is a (version, name, function) and runs inside a transaction. The
applied set lives in `schema_migrations`, so a container that restarts twenty
times still applies each exactly once.

Migrations must be safe to run against a database that already has the target
shape — someone will always end up running an old build against a new database.
"""

import sqlite3

CREATE_LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    if _table_exists(conn, table) and column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


# --- 001 --------------------------------------------------------------------


def _001_multi_tenant(conn: sqlite3.Connection) -> None:
    """Split the ingest registry from each user's watchlist, and scope
    preferences to a user.

    `watchlist` was doing two unrelated jobs: recording which tickers to ingest
    (a global concern — two users following AAPL must not each fetch it from
    EDGAR) and recording what someone chose to follow and at what price (a per
    user one). They separate cleanly, and the split is what makes the rest of
    multi-tenancy straightforward.

    Rows are left with user_id NULL rather than guessed at. The first account to
    register adopts them, so an existing single-user install keeps its data.
    """
    # The global registry.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tickers (
            ticker             TEXT PRIMARY KEY,
            name               TEXT,
            cik                INTEGER,
            reporting_currency TEXT,
            adr_ratio          REAL,
            supported          INTEGER NOT NULL DEFAULT 1,
            unsupported_reason TEXT,
            first_seen_at      TEXT
        )
        """
    )

    if _table_exists(conn, "watchlist") and "cik" in _columns(conn, "watchlist"):
        # Pre-split shape: carry the registry columns across, then rebuild
        # watchlist as a per-user table.
        conn.execute(
            """
            INSERT OR IGNORE INTO tickers
                (ticker, name, cik, reporting_currency, adr_ratio,
                 supported, unsupported_reason, first_seen_at)
            SELECT ticker, name, cik, reporting_currency, adr_ratio,
                   COALESCE(supported, 1), unsupported_reason, added_at
              FROM watchlist
            """
        )
        conn.execute(
            """
            CREATE TABLE watchlist_new (
                user_id          INTEGER REFERENCES users(id) ON DELETE CASCADE,
                ticker           TEXT NOT NULL REFERENCES tickers(ticker),
                added_at         TEXT NOT NULL,
                added_price      REAL,
                default_group_id INTEGER REFERENCES metric_groups(id),
                UNIQUE (user_id, ticker)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO watchlist_new
                (user_id, ticker, added_at, added_price, default_group_id)
            SELECT NULL, ticker, added_at, added_price, default_group_id
              FROM watchlist
            """
        )
        conn.execute("DROP TABLE watchlist")
        conn.execute("ALTER TABLE watchlist_new RENAME TO watchlist")

    # Preferences become per-user. group_metrics and alert_rule_state are
    # deliberately untouched: they hang off metric_groups and alert_rules
    # respectively, so they inherit their owner rather than storing it twice.
    #
    # alert_events is the exception — it stores user_id even though it could
    # join through its rule, because the homepage reads it on every render and
    # an event's owner never changes.
    for table in ("alert_rules", "alert_events"):
        _add_column(conn, table, "user_id", "INTEGER REFERENCES users(id)")

    # stock_groups referenced watchlist(ticker). The rebuilt watchlist has no
    # unique ticker of its own, so that foreign key is now a mismatch — and it
    # was pointing at the wrong table anyway: membership is about a ticker in
    # the registry, not about someone's watchlist row.
    if "user_id" not in _columns(conn, "stock_groups"):
        conn.execute(
            """
            CREATE TABLE stock_groups_new (
                user_id  INTEGER REFERENCES users(id) ON DELETE CASCADE,
                ticker   TEXT NOT NULL REFERENCES tickers(ticker),
                group_id INTEGER NOT NULL REFERENCES metric_groups(id) ON DELETE CASCADE,
                UNIQUE (user_id, ticker, group_id)
            )
            """
        )
        conn.execute(
            "INSERT INTO stock_groups_new (user_id, ticker, group_id) "
            "SELECT NULL, ticker, group_id FROM stock_groups"
        )
        conn.execute("DROP TABLE stock_groups")
        conn.execute("ALTER TABLE stock_groups_new RENAME TO stock_groups")

    # metric_groups needs a rebuild rather than an added column: its name is
    # UNIQUE, and once groups are per-user two people must each be able to have
    # a "Big Tech". SQLite cannot drop a constraint, so the table is recreated.
    if "user_id" not in _columns(conn, "metric_groups"):
        conn.execute(
            """
            CREATE TABLE metric_groups_new (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          INTEGER REFERENCES users(id) ON DELETE CASCADE,
                name             TEXT NOT NULL,
                primary_multiple TEXT NOT NULL,
                description      TEXT,
                created_at       TEXT NOT NULL,
                UNIQUE (user_id, name)
            )
            """
        )
        # Ids are preserved, so group_metrics and stock_groups keep pointing at
        # the right rows without being rewritten.
        conn.execute(
            """
            INSERT INTO metric_groups_new
                (id, user_id, name, primary_multiple, description, created_at)
            SELECT id, NULL, name, primary_multiple, description, created_at
              FROM metric_groups
            """
        )
        conn.execute("DROP TABLE metric_groups")
        conn.execute("ALTER TABLE metric_groups_new RENAME TO metric_groups")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist (user_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_stock_groups_user ON stock_groups (user_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_alert_events_user "
        "ON alert_events (user_id, acknowledged, created_at DESC)"
    )


MIGRATIONS: tuple[tuple[int, str, object], ...] = (
    (1, "multi_tenant", _001_multi_tenant),
)


def apply(conn: sqlite3.Connection, verbose: bool = False) -> list[str]:
    """Run every migration not yet recorded. Returns the names applied."""
    conn.execute(CREATE_LEDGER)
    done = {
        r["version"] for r in conn.execute("SELECT version FROM schema_migrations")
    }

    applied: list[str] = []
    for version, name, fn in MIGRATIONS:
        if version in done:
            continue
        if verbose:
            print(f"migration {version:03d} {name}: applying")

        # SQLite cannot alter a constraint, so reshaping a table means creating
        # a replacement and renaming it over the original. That fails while
        # children still point at the old parent, hence foreign keys off for
        # the duration. The pragma is a no-op inside a transaction, so it has
        # to sit outside one.
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            fn(conn)
            conn.commit()
        finally:
            conn.execute("PRAGMA foreign_keys = ON")

        broken = conn.execute("PRAGMA foreign_key_check").fetchall()
        if broken:
            raise RuntimeError(
                f"migration {version:03d} {name} left {len(broken)} broken "
                f"foreign key reference(s): {broken[:5]}"
            )
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) "
            "VALUES (?, ?, datetime('now'))",
            (version, name),
        )
        conn.commit()
        applied.append(name)

    return applied


def adopt_orphans(conn: sqlite3.Connection, user_id: int) -> dict[str, int]:
    """Hand every ownerless row to `user_id`.

    Called when the first account registers, so an install that predates
    multi-user keeps its watchlist, groups and alerts instead of appearing
    empty to the person who built it.
    """
    counts: dict[str, int] = {}
    for table in ("watchlist", "metric_groups", "stock_groups",
                  "alert_rules", "alert_events"):
        if not _table_exists(conn, table):
            continue
        cur = conn.execute(
            f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (user_id,)
        )
        if cur.rowcount:
            counts[table] = cur.rowcount
    conn.commit()
    return counts
