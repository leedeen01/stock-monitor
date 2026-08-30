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
