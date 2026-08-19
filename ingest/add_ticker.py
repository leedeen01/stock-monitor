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
import jobs
import prices
import segments
from config import DATA_DIR

LOG_DIR = DATA_DIR / "logs"


class _Tee:
    def __init__(self, stream, handle):
        self._stream, self._handle = stream, handle

    def write(self, text: str) -> None:
        self._stream.write(text)
        self._handle.write(text)
        self._handle.flush()

    def flush(self) -> None:
        self._stream.flush()
        self._handle.flush()


def _open_log():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    return (LOG_DIR / f"add_{stamp}.log").open("a", encoding="utf-8")


def add_ticker(conn, ticker: str, group_ids: list[int],
               job_id: int | None = None) -> dict:
    ticker = ticker.upper()

    jobs.set_step(conn, job_id, f"Resolving {ticker} with the SEC registry")
    backfill.backfill_fundamentals(conn, ticker, verbose=True)

    row = conn.execute(
        "SELECT supported, unsupported_reason FROM watchlist WHERE ticker = ?",
        (ticker,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"{ticker} was not found in the SEC registry")
    if not row["supported"]:
        raise RuntimeError(f"{ticker} cannot be tracked: {row['unsupported_reason']}")

    jobs.set_step(conn, job_id, "Fetching price history and splits")
    prices.backfill_prices(conn, ticker, verbose=False)

    jobs.set_step(conn, job_id, "Deriving the daily ratio series")
    derive.derive_ticker(conn, ticker, verbose=True)

    # Deliberately non-fatal. Plenty of filers publish no breakdown that
    # reconciles - AMD, Chevron, ExxonMobil among them - and that is a fact
    # about the filer rather than a failure to add the stock.
    jobs.set_step(conn, job_id, "Looking for a product revenue breakdown")
    try:
        segments.backfill_segments(conn, ticker, verbose=False)
    except Exception as exc:  # noqa: BLE001
        print(f"segments: skipped - {type(exc).__name__}: {exc}")

    jobs.set_step(conn, job_id, "Assigning groups")
    for gid in group_ids:
        conn.execute(
            "INSERT OR IGNORE INTO stock_groups (ticker, group_id) VALUES (?, ?)",
            (ticker, gid),
        )
    if group_ids:
        conn.execute(
            "UPDATE watchlist SET default_group_id = ? WHERE ticker = ?",
            (group_ids[0], ticker),
        )

    # Price at the moment of adding, so the grid can show since-added return.
    latest = conn.execute(
        "SELECT close FROM ratios_daily WHERE ticker = ? ORDER BY date DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    if latest and latest["close"]:
        conn.execute(
            "UPDATE watchlist SET added_price = ? WHERE ticker = ? "
            "AND added_price IS NULL",
            (latest["close"], ticker),
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
    parser.add_argument("--job-id", type=int, default=None)
    args = parser.parse_args()

    group_ids = [int(g) for g in args.groups.split(",") if g.strip().isdigit()]

    conn = db.connect()
    db.init_schema(conn)
    try:
        result = add_ticker(conn, args.ticker, group_ids, args.job_id)
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
    _log = _open_log()
    sys.stdout = _Tee(sys.__stdout__, _log)
    sys.stderr = _Tee(sys.__stderr__, _log)
    sys.exit(main())
