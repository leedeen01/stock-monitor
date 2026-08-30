"""Daily prices, splits and dividends via yfinance — plus share normalization.

The subtle part of this module is `split_factor`.

EDGAR share counts are as-filed: Apple's FY2019 10-K reports 4.649bn diluted
shares, because that was true in 2019. yfinance's price history, by contrast, is
retroactively split-adjusted — Apple's 2019 closes are restated into post-split
terms after the 4:1 in August 2020.

Multiply one by the other and market cap is wrong by exactly the split ratio,
with no error and nothing that looks out of place. So every as-filed share count
gets carried forward through any split that happened after it was filed:

    shares_in_current_terms = as_filed_shares * product(splits after filed_at)

For Apple FY2019 that is 4.649bn * 4 = 18.596bn, which is precisely the figure
Apple itself restated to in the FY2020 10-K. The two paths agree, which is the
check that the logic is right.

This is a unit conversion, not a peek at the future: market cap is invariant
under it, so it does not violate the no-lookahead rule.
"""

import argparse
import sqlite3
import warnings

import yfinance as yf

import db

warnings.filterwarnings("ignore", category=FutureWarning)


def _iso(ts) -> str:
    return ts.date().isoformat()


def backfill_prices(conn: sqlite3.Connection, ticker: str, verbose: bool = True) -> dict:
    """Pull full available history. Storage is cheap and longer history makes
    percentile bands span more of the cycle, which is the whole point."""
    ticker = ticker.upper()
    handle = yf.Ticker(ticker)

    # auto_adjust=False keeps `Close` as the split-adjusted actual closing
    # price. `Adj Close` additionally back-adjusts for dividends, which would
    # distort market cap — we want what the stock actually traded at.
    hist = handle.history(period="max", auto_adjust=False, actions=True)
    if hist.empty:
        raise ValueError(f"no price history returned for {ticker}")

    price_rows = [
        (ticker, _iso(idx), float(row["Close"]),
         int(row["Volume"]) if row["Volume"] == row["Volume"] else None)
        for idx, row in hist.iterrows()
        if row["Close"] == row["Close"]  # drop NaN closes
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO prices (ticker, date, close, volume) VALUES (?, ?, ?, ?)",
        price_rows,
    )

    split_rows = [
        (ticker, _iso(idx), float(row["Stock Splits"]))
        for idx, row in hist.iterrows()
        if row.get("Stock Splits", 0) not in (0, None) and row["Stock Splits"] == row["Stock Splits"]
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO share_splits (ticker, date, ratio) VALUES (?, ?, ?)",
        split_rows,
    )

    dividend_rows = [
        (ticker, _iso(idx), float(row["Dividends"]))
        for idx, row in hist.iterrows()
        if row.get("Dividends", 0) not in (0, None) and row["Dividends"] == row["Dividends"]
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO dividends (ticker, date, amount) VALUES (?, ?, ?)",
        dividend_rows,
    )
    conn.commit()

    if verbose:
        print(f"{ticker}: {len(price_rows):,} closes "
              f"({price_rows[0][1]} -> {price_rows[-1][1]}), "
              f"{len(split_rows)} splits, {len(dividend_rows)} dividends")
        for _, date, ratio in split_rows:
            print(f"    split {ratio:g}:1 on {date}")

    return {
        "ticker": ticker,
        "prices": len(price_rows),
        "splits": len(split_rows),
        "dividends": len(dividend_rows),
    }


def load_splits(conn: sqlite3.Connection, ticker: str) -> list[tuple[str, float]]:
    return [
        (r["date"], r["ratio"])
        for r in conn.execute(
            "SELECT date, ratio FROM share_splits WHERE ticker = ? ORDER BY date",
            (ticker.upper(),),
        )
    ]


def split_factor(splits: list[tuple[str, float]], filed_at: str) -> float:
    """Cumulative split ratio applied AFTER `filed_at`.

    Multiply an as-filed share count by this to express it in current-share
    terms, matching yfinance's split-adjusted prices.
    """
    factor = 1.0
    for split_date, ratio in splits:
        if split_date > filed_at:
            factor *= ratio
    return factor


def normalize_shares(conn: sqlite3.Connection, ticker: str, shares: float, filed_at: str) -> float:
    return shares * split_factor(load_splits(conn, ticker), filed_at)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill prices, splits, dividends")
    parser.add_argument("tickers", nargs="*", help="Defaults to all supported watchlist tickers")
    args = parser.parse_args()

    conn = db.connect()
    db.init_schema(conn)

    tickers = [t.upper() for t in args.tickers] or [
        r["ticker"] for r in conn.execute(
            "SELECT ticker FROM tickers WHERE supported = 1 ORDER BY ticker"
        )
    ]

    for ticker in tickers:
        try:
            backfill_prices(conn, ticker)
        except Exception as exc:  # noqa: BLE001 - report and continue the batch
            print(f"{ticker}: FAILED - {type(exc).__name__}: {exc}")
    conn.close()


if __name__ == "__main__":
    main()
