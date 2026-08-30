"""Market routing: adding one market must not disturb another."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import markets
import pipeline
from conftest import seed_user


def test_each_market_declares_its_own_provider():
    assert markets.get("US").fundamentals == "edgar"
    assert markets.get("SGX").fundamentals == "yfinance"
    assert markets.get("LSE").fundamentals is None
    # A market with no provider is priced, not valued.
    assert markets.get("US").valuable and not markets.get("LSE").valuable


def test_unknown_market_falls_back_rather_than_raising():
    """A ticker with a market we do not recognise should behave like the
    common case, not break the run."""
    assert markets.get("MOON").code == "US"
    assert markets.get(None).code == "US"


def test_price_symbols_get_the_right_suffix():
    assert markets.price_symbol("AAPL", "US") == "AAPL"
    assert markets.price_symbol("D05", "SGX") == "D05.SI"
    assert markets.price_symbol("VOD", "LSE") == "VOD.L"


def test_an_already_qualified_symbol_is_not_suffixed_twice():
    """SPYL.L must not become SPYL.L.L."""
    assert markets.price_symbol("SPYL.L", "LSE") == "SPYL.L"


def test_a_fund_is_never_sent_to_a_fundamentals_provider(tmp_path):
    """Even on a market that has one — an ETF on SGX has no filings either."""
    conn = db.connect(tmp_path / "m.db")
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO tickers (ticker, market, kind, supported) VALUES ('X','SGX','fund',1)")
    conn.commit()
    assert pipeline.ingest_fundamentals(conn, "X", verbose=False) == "none"
    conn.close()


def test_a_market_with_no_provider_reports_none_rather_than_failing(tmp_path):
    conn = db.connect(tmp_path / "n.db")
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO tickers (ticker, market, kind, supported) VALUES ('Y','LSE','equity',1)")
    conn.commit()
    assert pipeline.ingest_fundamentals(conn, "Y", verbose=False) == "none"
    conn.close()


def test_derive_picks_the_right_path_from_the_data(tmp_path):
    """Full ratios where there are facts, price-only where there are none —
    decided by what exists rather than by a flag someone has to remember."""
    conn = db.connect(tmp_path / "d.db")
    db.init_schema(conn)
    seed_user(conn)
    conn.execute("INSERT INTO tickers (ticker, market, kind, supported) VALUES ('Z','SGX','equity',1)")
    conn.executemany("INSERT INTO prices (ticker,date,close) VALUES ('Z',?,?)",
                     [("2026-08-26", 10.0), ("2026-08-27", 10.5)])
    conn.commit()

    pipeline.derive_for(conn, "Z", verbose=False)   # no fundamentals yet
    row = conn.execute("SELECT * FROM ratios_daily WHERE ticker='Z' ORDER BY date DESC LIMIT 1").fetchone()
    assert row["close"] == 10.5 and row["pe_ttm"] is None
    conn.close()


def test_the_publication_lag_prevents_lookahead():
    """yfinance gives no filing dates. Assuming results were known on the day
    the period closed would flatter every historical percentile."""
    import pandas as pd

    import yf_fundamentals as yfd
    filed = yfd._filed_at(pd.Timestamp("2025-12-31"))
    assert filed > "2025-12-31", "results cannot be known before they are published"
    assert filed.startswith("2026-03"), filed


def test_an_existing_account_gains_groups_for_markets_added_later(tmp_path):
    """A market with no group profiles is unreachable, not merely unstyled.

    The add-stock form builds its market list from the user's own groups, and
    both that picker and the watchlist tabs hide themselves when only one
    market is present. So an account provisioned before SGX existed could not
    reach SGX at all. Migration 006 re-seeds; this guards the round trip.
    """
    import groups
    import migrations

    conn = db.connect(tmp_path / "late.db")
    db.init_schema(conn)
    user_id = seed_user(conn)

    # An account as it looked before the SGX profiles were written.
    groups.seed(conn, user_id=user_id, verbose=False)
    conn.execute(
        "DELETE FROM group_metrics WHERE group_id IN "
        "(SELECT id FROM metric_groups WHERE user_id = ? AND market = 'SGX')",
        (user_id,),
    )
    conn.execute(
        "DELETE FROM metric_groups WHERE user_id = ? AND market = 'SGX'", (user_id,)
    )
    # Something the user filed by hand, which re-seeding must not disturb.
    conn.execute(
        "INSERT OR IGNORE INTO tickers (ticker, market, first_seen_at) "
        "VALUES ('AAPL', 'US', '2024-01-01')"
    )
    group_id = conn.execute(
        "SELECT id FROM metric_groups WHERE user_id = ? AND name = 'Big Tech'",
        (user_id,),
    ).fetchone()["id"]
    conn.execute(
        "INSERT OR IGNORE INTO stock_groups (user_id, ticker, group_id) VALUES (?,?,?)",
        (user_id, "AAPL", group_id),
    )
    conn.execute("DELETE FROM schema_migrations WHERE version = 6")
    conn.commit()

    def offered():
        return {
            row["market"]
            for row in conn.execute(
                "SELECT DISTINCT market FROM metric_groups WHERE user_id = ?",
                (user_id,),
            )
        }

    assert offered() == {"US"}, "precondition: the account is US-only"

    migrations.apply(conn)

    assert "SGX" in offered()
    seeded = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM metric_groups WHERE user_id = ? AND market = 'SGX'",
            (user_id,),
        )
    }
    assert {"REITs", "Banks", "Dividend", "Others"} <= seeded
    # Profiles arrive with their metrics, not as empty shells.
    assert conn.execute(
        "SELECT 1 FROM group_metrics gm JOIN metric_groups g ON g.id = gm.group_id "
        "WHERE g.user_id = ? AND g.market = 'SGX'",
        (user_id,),
    ).fetchone()
    # The user's own filing survives.
    assert conn.execute(
        "SELECT 1 FROM stock_groups WHERE user_id = ? AND ticker = 'AAPL'", (user_id,)
    ).fetchone()

    # Safe to run twice: the seeder upserts on (user_id, name).
    before = conn.execute(
        "SELECT COUNT(*) c FROM metric_groups WHERE user_id = ?", (user_id,)
    ).fetchone()["c"]
    conn.execute("DELETE FROM schema_migrations WHERE version = 6")
    conn.commit()
    migrations.apply(conn)
    after = conn.execute(
        "SELECT COUNT(*) c FROM metric_groups WHERE user_id = ?", (user_id,)
    ).fetchone()["c"]
    assert before == after
