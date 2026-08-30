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


# --- 002 --------------------------------------------------------------------


def _002_normalise(conn: sqlite3.Connection) -> None:
    """Remove duplicated and misplaced columns, and give jobs an owner.

    Three separate problems, all found by asking whether the schema is in 3NF:

    1. `watchlist` still carried reporting_currency, adr_ratio, supported and
       unsupported_reason. Migration 001 moved those to `tickers`, but the
       legacy additive migrations in db.py re-added them on every start — with
       supported defaulting to 1, so every watchlist row claimed support
       including the one ticker that has none. Duplicated AND wrong.

    2. `holdings` and `ibkr_trades` stored conid and asset_class per row. Both
       describe the instrument, not the holding, so they depended on ticker
       rather than on the whole key — a partial dependency, and one that
       repeats the same conid once per position per day forever.

    3. `jobs` had no user_id, so /api/jobs could hand any signed-in account
       another account's job. Not a normalisation issue but found in the same
       pass, and fixed here because it is the same table rebuild.
    """
    # 1. Drop the resurrected duplicates.
    if "supported" in _columns(conn, "watchlist"):
        conn.execute(
            """
            CREATE TABLE watchlist_clean (
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
            "INSERT INTO watchlist_clean (user_id, ticker, added_at, added_price, "
            "default_group_id) SELECT user_id, ticker, added_at, added_price, "
            "default_group_id FROM watchlist"
        )
        conn.execute("DROP TABLE watchlist")
        conn.execute("ALTER TABLE watchlist_clean RENAME TO watchlist")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist (user_id)"
        )

    # 2. Instrument attributes move to the instrument.
    _add_column(conn, "tickers", "ibkr_conid", "TEXT")
    _add_column(conn, "tickers", "asset_class", "TEXT")

    # Rebuilt with explicit definitions, not CREATE TABLE AS SELECT: that form
    # copies the data and silently drops the primary key, and holdings depends
    # on its key for the ON CONFLICT that makes a re-sync idempotent. Losing it
    # would duplicate every position on the second run and look like nothing at
    # all until the numbers doubled.
    REBUILT = {
        "holdings": (
            """
            CREATE TABLE holdings_clean (
                user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                report_date      TEXT NOT NULL,
                ticker           TEXT NOT NULL,
                currency         TEXT,
                quantity         REAL,
                cost_basis_price REAL,
                cost_basis_money REAL,
                mark_price       REAL,
                position_value   REAL,
                unrealized_pnl   REAL,
                percent_of_nav   REAL,
                PRIMARY KEY (user_id, report_date, ticker)
            )
            """,
            "user_id, report_date, ticker, currency, quantity, cost_basis_price, "
            "cost_basis_money, mark_price, position_value, unrealized_pnl, percent_of_nav",
        ),
        "ibkr_trades": (
            """
            CREATE TABLE ibkr_trades_clean (
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                trade_id     TEXT NOT NULL,
                ticker       TEXT,
                currency     TEXT,
                trade_date   TEXT,
                buy_sell     TEXT,
                quantity     REAL,
                price        REAL,
                commission   REAL,
                net_cash     REAL,
                open_close   TEXT,
                cost_basis   REAL,
                realized_pnl REAL,
                PRIMARY KEY (user_id, trade_id)
            )
            """,
            "user_id, trade_id, ticker, currency, trade_date, buy_sell, quantity, "
            "price, commission, net_cash, open_close, cost_basis, realized_pnl",
        ),
    }

    for table, (create_sql, columns) in REBUILT.items():
        if "conid" not in _columns(conn, table):
            continue
        # Keep any conid already collected before the column goes.
        conn.execute(
            "UPDATE tickers SET ibkr_conid = COALESCE(ibkr_conid, ("
            f"  SELECT conid FROM {table} t WHERE t.ticker = tickers.ticker "
            "   AND t.conid IS NOT NULL LIMIT 1))"
        )
        conn.execute(create_sql)
        conn.execute(
            f"INSERT INTO {table}_clean ({columns}) SELECT {columns} FROM {table}"
        )
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {table}_clean RENAME TO {table}")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_holdings_latest "
        "ON holdings (user_id, report_date DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ibkr_trades_ticker "
        "ON ibkr_trades (user_id, ticker, trade_date)"
    )

    # 3. A job belongs to whoever started it.
    _add_column(conn, "jobs", "user_id", "INTEGER REFERENCES users(id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs (user_id, status, id DESC)"
    )

# --- 003 --------------------------------------------------------------------


def _003_funds(conn: sqlite3.Connection) -> None:
    """Allow tickers that have prices but no filings.

    `supported` used to mean 'we can value this'. It now means 'this works in
    the app at all', and `kind` says how: an equity is valued from filings, a
    fund is priced only. Splitting the two is what lets an ETF onto a watchlist
    without pretending it has a P/E.

    `price_symbol` exists because IBKR reports a local symbol while yfinance
    wants an exchange suffix — SPYL against SPYL.L — and the two are not
    interchangeable.
    """
    _add_column(conn, "tickers", "kind", "TEXT NOT NULL DEFAULT 'equity'")
    _add_column(conn, "tickers", "price_symbol", "TEXT")
    conn.execute(
        "UPDATE tickers SET price_symbol = ticker WHERE price_symbol IS NULL"
    )

# --- 004 --------------------------------------------------------------------


def _004_markets(conn: sqlite3.Connection) -> None:
    """Record which market a ticker trades on, and in what currency.

    Deliberately separate from `reporting_currency`, which is the currency a
    company FILES in. A stock can file in one and trade in another, and a fund
    files in none at all — SPYL trades in USD on the LSE and in EUR in
    Amsterdam while reporting nothing anywhere.

    Nothing is converted. The currency is carried so it can be shown beside the
    number: a price in SGD displayed as though it were dollars is worse than no
    price, and converting would need historical rates on every derived figure.
    """
    _add_column(conn, "tickers", "market", "TEXT")
    _add_column(conn, "tickers", "quote_currency", "TEXT")

    # Everything ingested before this arrived through the SEC path, which only
    # accepts US-listed USD filers.
    conn.execute(
        "UPDATE tickers SET market = 'US' WHERE market IS NULL AND cik IS NOT NULL"
    )
    conn.execute(
        "UPDATE tickers SET quote_currency = 'USD' WHERE quote_currency IS NULL "
        "AND cik IS NOT NULL"
    )

# --- 005 --------------------------------------------------------------------


def _005_yield_and_group_markets(conn: sqlite3.Connection) -> None:
    """Trailing dividend yield, and groups that belong to a market.

    Dividends have been ingested since the beginning and used by nothing. For
    REITs and income names — most of what is worth holding on SGX — yield is
    the metric, so it finally earns its column.

    Groups gain a market because a REIT profile has no business appearing when
    adding a US stock, and because a change to the Singapore set must not touch
    the US one.
    """
    _add_column(conn, "ratios_daily", "dividend_yield", "REAL")
    _add_column(conn, "metric_groups", "market", "TEXT")

    # Everything seeded before this was for US listings.
    conn.execute("UPDATE metric_groups SET market = 'US' WHERE market IS NULL")


# --- 006 --------------------------------------------------------------------


def _006_seed_market_groups(conn: sqlite3.Connection) -> None:
    """Give existing accounts the group profiles added since they signed up.

    Groups are seeded once, at provisioning. Anyone who signed up before a
    market existed therefore never receives its profiles, and 005 only stamped
    the groups already present as 'US'.

    That is not merely cosmetic. The add-stock form derives its market list
    from the user's own groups, so an account holding only US profiles offers
    only US, and both the market picker and the watchlist tabs hide themselves
    as single-market. The result is a market that cannot be reached at all
    through the UI.

    Re-seeding is the fix and is safe to repeat: the seeder upserts on
    (user_id, name), and only `reset=True` touches which stocks sit in which
    group. Existing profiles keep their members.
    """
    # Deferred: db imports this module, and groups imports db.
    import groups

    for row in conn.execute("SELECT id FROM users ORDER BY id").fetchall():
        groups.seed(conn, user_id=row["id"], verbose=False)


MIGRATIONS: tuple[tuple[int, str, object], ...] = (
    (1, "multi_tenant", _001_multi_tenant),
    (2, "normalise", _002_normalise),
    (3, "funds", _003_funds),
    (4, "markets", _004_markets),
    (5, "yield_and_group_markets", _005_yield_and_group_markets),
    (6, "seed_market_groups", _006_seed_market_groups),
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
