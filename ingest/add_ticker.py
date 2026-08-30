"""Add one ticker and backfill it, end to end.

This chain used to live in the web server action, which meant the browser held a
request open for the whole 30-60 seconds and Next blocked navigation behind it.
Moving it here lets the action spawn and return: the work is pipeline
orchestration, which is what this package is for.

Steps are reported into `jobs` so the UI can show which phase it's in rather
than a bare spinner. Anything that fails after the fundamentals land leaves the
ticker on the watchlist with partial data, which is recoverable by re-adding;
failing to resolve the ticker at all leaves nothing behind.
"""

import argparse
import sys
import traceback
from datetime import datetime

import backfill
import db
import derive
import funds
import jobs
import markets
import pipeline
import prices
import run_log
import segments




def add_ticker(conn, ticker: str, group_ids: list[int], user_id: int,
               market_code: str = markets.DEFAULT_MARKET,
               job_id: int | None = None) -> dict:
    ticker = ticker.upper()

    market = markets.get(market_code)
    jobs.set_step(conn, job_id, f"Resolving {ticker} on {market.label}")

    if market.fundamentals == "edgar":
        try:
            provider = pipeline.ingest_fundamentals(conn, ticker, market.code, verbose=True)
        except KeyError:
            # Not an SEC filer. Almost always an ETF or a foreign listing —
            # worth holding, impossible to value from filings. Track the price.
            jobs.set_step(conn, job_id, f"{ticker} has no filings — checking for a price")
            found = funds.register(conn, ticker)
            provider = "none"
            print(f"{ticker}: no SEC filings; tracking price as {found['price_symbol']}")
    else:
        # A market with its own provider, or none at all. Register the ticker
        # first so the provider knows which symbol and currency to use.
        conn.execute(
            """
            INSERT INTO tickers (ticker, market, quote_currency, price_symbol,
                                 supported, first_seen_at)
            VALUES (?, ?, ?, ?, 1, datetime('now'))
            ON CONFLICT(ticker) DO UPDATE SET
                market = excluded.market,
                quote_currency = COALESCE(tickers.quote_currency, excluded.quote_currency),
                price_symbol = COALESCE(tickers.price_symbol, excluded.price_symbol)
            """,
            (ticker, market.code, market.currency,
             markets.price_symbol(ticker, market.code)),
        )
        conn.commit()
        provider = pipeline.ingest_fundamentals(conn, ticker, market.code, verbose=True)
        if provider == "none":
            funds.register(conn, ticker)

    row = conn.execute(
        "SELECT supported, unsupported_reason FROM tickers WHERE ticker = ?",
        (ticker,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"{ticker} could not be registered")
    if not row["supported"]:
        raise RuntimeError(f"{ticker} cannot be tracked: {row['unsupported_reason']}")

    jobs.set_step(conn, job_id, "Fetching price history and splits")
    prices.backfill_prices(conn, ticker, verbose=False)

    jobs.set_step(conn, job_id, "Deriving the daily ratio series")
    pipeline.derive_for(conn, ticker, verbose=True)

    # Deliberately non-fatal. Plenty of filers publish no breakdown that
    # reconciles - AMD, Chevron, ExxonMobil among them - and that is a fact
    # about the filer rather than a failure to add the stock.
    if provider == "edgar":
        jobs.set_step(conn, job_id, "Looking for a product revenue breakdown")
        try:
            segments.backfill_segments(conn, ticker, verbose=False)
        except Exception as exc:  # noqa: BLE001
            print(f"segments: skipped - {type(exc).__name__}: {exc}")

    # The ingest registry now knows this ticker; put it on THIS user's list.
    jobs.set_step(conn, job_id, "Assigning groups")
    conn.execute(
        "INSERT OR IGNORE INTO watchlist (user_id, ticker, added_at) VALUES (?, ?, ?)",
        (user_id, ticker, datetime.now().isoformat(timespec="seconds")),
    )
    for gid in group_ids:
        conn.execute(
            "INSERT OR IGNORE INTO stock_groups (ticker, group_id, user_id) "
            "VALUES (?, ?, ?)",
            (ticker, gid, user_id),
        )
    if group_ids:
        conn.execute(
            "UPDATE watchlist SET default_group_id = ? WHERE ticker = ? AND user_id = ?",
            (group_ids[0], ticker, user_id),
        )

    # Price at the moment of adding, so the grid can show since-added return.
    latest = conn.execute(
        "SELECT close FROM ratios_daily WHERE ticker = ? ORDER BY date DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    if latest and latest["close"]:
        conn.execute(
            "UPDATE watchlist SET added_price = ? WHERE ticker = ? "
            "AND user_id = ? AND added_price IS NULL",
            (latest["close"], ticker, user_id),
        )
    conn.commit()

    days = conn.execute(
        "SELECT COUNT(*) AS c FROM ratios_daily WHERE ticker = ?", (ticker,)
    ).fetchone()["c"]
    lines = conn.execute(
        "SELECT COUNT(DISTINCT label) AS c FROM segment_revenue "
        "WHERE ticker = ? AND is_subtotal = 0",
        (ticker,),
    ).fetchone()["c"]

    return {"ticker": ticker, "days": days, "lines": lines}


def main() -> int:
    parser = argparse.ArgumentParser(description="Add a ticker and backfill it")
    parser.add_argument("ticker")
    parser.add_argument("--groups", default="",
                        help="Comma-separated metric_groups ids")
    parser.add_argument("--market", default=markets.DEFAULT_MARKET,
                        choices=markets.codes(),
                        help="Which market the ticker trades on")
    parser.add_argument("--user-id", type=int, required=True,
                        help="Whose watchlist this is added to")
    parser.add_argument("--job-id", type=int, default=None)
    args = parser.parse_args()

    group_ids = [int(g) for g in args.groups.split(",") if g.strip().isdigit()]

    conn = db.connect()
    db.init_schema(conn)
    try:
        result = add_ticker(conn, args.ticker, group_ids, args.user_id,
                            args.market, args.job_id)
    except Exception as exc:  # noqa: BLE001 - the message is the UI's error text
        traceback.print_exc()
        # The message goes straight to the UI, so lead with what happened
        # rather than the exception class. edgar raises KeyError with a full
        # sentence in it, which would otherwise render as KeyError: '...'.
        message = str(exc).strip("'\"") or type(exc).__name__
        jobs.finish(conn, args.job_id, "error", message[:500])
        conn.close()
        return 1

    detail = (
        f"Added {result['ticker']} - {result['days']:,} days of derived history"
        + (f", {result['lines']} product lines."
           if result["lines"] else ". No product breakdown published.")
    )
    jobs.finish(conn, args.job_id, "ok", detail)
    print(detail)
    conn.close()
    return 0


if __name__ == "__main__":
    run_log.tee_stdio("add")
    sys.exit(main())
