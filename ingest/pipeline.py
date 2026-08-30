"""Routes a ticker to the right providers for its market.

The one place that knows a US stock is valued from EDGAR while a Singapore one
comes from yfinance and a London fund from nowhere at all. Everything else —
adding a ticker, the daily refresh — asks here, so adding a market means an
entry in markets.py and a branch here, and cannot reach a code path another
market depends on.
"""

import sqlite3

import backfill
import derive
import markets
import yf_fundamentals


def market_of(conn: sqlite3.Connection, ticker: str) -> str | None:
    row = conn.execute(
        "SELECT market FROM tickers WHERE ticker = ?", (ticker.upper(),)
    ).fetchone()
    return row["market"] if row else None


def is_fund(conn: sqlite3.Connection, ticker: str) -> bool:
    row = conn.execute(
        "SELECT kind FROM tickers WHERE ticker = ?", (ticker.upper(),)
    ).fetchone()
    return bool(row) and row["kind"] == "fund"


def ingest_fundamentals(conn: sqlite3.Connection, ticker: str,
                        market: str | None = None, verbose: bool = True) -> str:
    """Fetch filings for a ticker from whichever source its market has.

    Returns the provider that ran, or "none" when the market has no source —
    a fund, or a listing whose filings are not machine-readable. That is a fact
    about the instrument rather than a failure, so it does not raise.
    """
    ticker = ticker.upper()
    provider = markets.get(market or market_of(conn, ticker)).fundamentals

    if is_fund(conn, ticker) or provider is None:
        return "none"

    if provider == "edgar":
        backfill.backfill_fundamentals(conn, ticker, verbose=verbose)
    elif provider == "yfinance":
        yf_fundamentals.backfill(conn, ticker, verbose=verbose)
    else:
        raise ValueError(f"{ticker}: unknown fundamentals provider {provider!r}")

    return provider


def derive_for(conn: sqlite3.Connection, ticker: str, verbose: bool = True) -> None:
    """Full ratio series where there are fundamentals, price-only where not."""
    ticker = ticker.upper()
    has_facts = conn.execute(
        "SELECT 1 FROM fundamentals WHERE ticker = ? LIMIT 1", (ticker,)
    ).fetchone()

    if has_facts and not is_fund(conn, ticker):
        derive.derive_ticker(conn, ticker, verbose=verbose)
    else:
        derive.derive_fund(conn, ticker, verbose=verbose)
