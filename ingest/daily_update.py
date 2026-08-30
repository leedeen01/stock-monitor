"""Scheduled daily refresh: fundamentals, prices, then derived ratios.

Runs from Windows Task Scheduler. Three design choices worth knowing:

1. **New sessions are detected from the data, not a calendar.** This machine is
   on UTC+8 while the market it tracks is on US Eastern, so the US close at 4pm
   ET lands at 4-5am the following local morning. A local Monday run sits
   against Sunday evening in New York and has no session behind it, and US
   market holidays don't line up with local ones either. Comparing the latest
   stored price date against what yfinance returns sidesteps all of that — no
   holiday table to maintain and nothing to correct twice a year for DST.

2. **Derivation is skipped when nothing arrived.** derive.py rebuilds
   ratios_daily from scratch, so there is no point paying for it on a day with
   no new closes and no new filings.

3. **Every run is recorded, including failures.** The grid reads the last run
   and says how fresh the data is. A job that quietly stops is the failure mode
   that matters here: the numbers still render, they're just wrong by however
   long it's been broken.
"""

import argparse
import sys
import traceback
from datetime import datetime, timezone

import alerts
import backfill
import db
import derive
import ibkr
import jobs
import pipeline
import prices
import run_log
import segments




def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _latest_session(conn) -> str | None:
    row = conn.execute("SELECT MAX(date) AS d FROM prices").fetchone()
    return row["d"] if row else None


def _latest_filing(conn) -> str | None:
    """Newest filing date on record. Derivation has to react to this as well as
    to new prices — an earnings release changes every multiple without adding a
    single price row, and a concept-mapping change rewrites history with no new
    market data at all."""
    row = conn.execute("SELECT MAX(filed_at) AS f FROM fundamentals").fetchone()
    return row["f"] if row else None


def _supported_tickers(conn) -> list[str]:
    return [
        r["ticker"]
        for r in conn.execute(
            "SELECT ticker FROM tickers WHERE supported = 1 ORDER BY ticker"
        )
    ]


def start_run(conn) -> int:
    cur = conn.execute(
        "INSERT INTO pipeline_runs (started_at, status) VALUES (?, 'running')",
        (_now(),),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id: int, status: str, **fields) -> None:
    conn.execute(
        """
        UPDATE pipeline_runs
        SET finished_at = ?, status = ?, tickers = ?, new_price_rows = ?,
            latest_session = ?, detail = ?
        WHERE id = ?
        """,
        (
            _now(), status,
            fields.get("tickers"), fields.get("new_price_rows"),
            fields.get("latest_session"), fields.get("detail"),
            run_id,
        ),
    )
    conn.commit()


def main(job_id: int | None = None) -> int:
    conn = db.connect()
    db.init_schema(conn)
    run_id = start_run(conn)

    tickers = _supported_tickers(conn)
    if not tickers:
        finish_run(conn, run_id, "skipped", detail="watchlist is empty")
        jobs.finish(conn, job_id, "ok", "Nothing on the watchlist.")
        print("nothing on the watchlist")
        return 0

    before_session = _latest_session(conn)
    before_filing = _latest_filing(conn)
    before_rows = conn.execute("SELECT COUNT(*) AS c FROM prices").fetchone()["c"]
    failures: list[str] = []

    print(f"=== daily update {_now()} ===")
    print(f"tickers: {', '.join(tickers)}")
    print(f"latest stored session: {before_session}")

    # Fundamentals first. companyfacts is cached for a day, so this is close to
    # free most days and picks up new filings the moment they land.
    # IBKR first, deliberately. A holding is the strongest signal that its
    # filings are wanted, so discovering positions before the EDGAR pass means
    # a stock bought yesterday gets its full valuation history in the same
    # run rather than waiting a day for the next one.
    jobs.set_step(conn, job_id, "Syncing brokerage holdings")
    print("")
    print("--- ibkr ---")
    try:
        _ibkr = ibkr.sync_all(conn, verbose=True)
        print(f"{_ibkr['ok']} account(s) synced, {_ibkr['failed']} failed")
        for _symbol in _ibkr['unmatched']:
            if _symbol not in tickers:
                print(f'  held but not in the registry: {_symbol} - ingesting')
                tickers.append(_symbol)
    except Exception as exc:  # noqa: BLE001 - brokerage data is not the main job
        failures.append(f'ibkr: {type(exc).__name__}: {exc}')
        print(f'ibkr sync FAILED - {exc}')

    jobs.set_step(conn, job_id, "Checking EDGAR for new filings")
    print("\n--- fundamentals ---")
    for ticker in tickers:
        try:
            # Routed by market: EDGAR for US, yfinance for SGX, nothing for a
            # fund. Adding a market never touches this loop.
            if pipeline.ingest_fundamentals(conn, ticker, verbose=True) == "none":
                continue
        except KeyError:
            # Not in the SEC registry. Almost always a non-US ETF or fund
            # arriving from the brokerage — real to hold, impossible to value
            # from filings. Recording that stops it being retried daily.
            backfill.mark_unsupported(
                conn, ticker,
                "not an SEC filer - no filings to value it from",
            )
            print(f"{ticker}: not an SEC filer, marked unsupported")
        except Exception as exc:  # noqa: BLE001 - one bad ticker shouldn't stop the run
            failures.append(f"{ticker} fundamentals: {type(exc).__name__}: {exc}")
            print(f"{ticker}: FAILED - {exc}")

    jobs.set_step(conn, job_id, "Fetching the latest closes")
    print("\n--- prices ---")
    for ticker in tickers:
        try:
            prices.backfill_prices(conn, ticker, verbose=False)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{ticker} prices: {type(exc).__name__}: {exc}")
            print(f"{ticker}: FAILED - {exc}")

    after_session = _latest_session(conn)
    after_filing = _latest_filing(conn)
    after_rows = conn.execute("SELECT COUNT(*) AS c FROM prices").fetchone()["c"]
    new_rows = after_rows - before_rows
    new_filing = after_filing != before_filing

    if new_rows == 0 and after_session == before_session and not new_filing:
        # Weekend here, holiday there, or simply run twice in a day.
        status = "skipped" if not failures else "partial"
        detail = f"no new session (still {after_session})"
        if failures:
            detail += "; " + "; ".join(failures)
        finish_run(conn, run_id, status,
                   tickers=len(tickers), new_price_rows=0,
                   latest_session=after_session, detail=detail)
        # ASCII only in log output: Task Scheduler runs without a UTF-8 console,
        # so anything fancier lands in the log file as mojibake.
        jobs.finish(conn, job_id, "ok",
                    f"Already current - no new session since {after_session}.")
        print(f"\nno new market data - latest session is still {after_session}")
        return 0

    reason = f"{new_rows:,} new price rows"
    if new_filing:
        reason += f", new filing ({after_filing})"
    jobs.set_step(conn, job_id, "Recomputing the ratio series")
    print(f"\n--- deriving ({reason}) ---")
    for ticker in tickers:
        try:
            pipeline.derive_for(conn, ticker, verbose=True)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{ticker} derive: {type(exc).__name__}: {exc}")
            print(f"{ticker}: FAILED - {exc}")

    # Product revenue only changes when a 10-K lands, and each refresh costs
    # several fetches per ticker, so it hangs off the new-filing signal rather
    # than running daily.
    if new_filing:
        jobs.set_step(conn, job_id, "Refreshing product revenue")
        print("\n--- revenue by product (new filing detected) ---")
        for ticker in tickers:
            try:
                result = segments.backfill_segments(conn, ticker, verbose=False)
                if result["parsed"]:
                    print(f"  {ticker}: {result['parsed']}/{result['filings']} filings, "
                          f"{result['rows']} lines")
            except Exception as exc:  # noqa: BLE001 - never fail the refresh over this
                failures.append(f"{ticker} segments: {type(exc).__name__}: {exc}")
                print(f"  {ticker}: FAILED - {exc}")

    # Alerts run after derivation, since a crossing is defined against the
    # freshly derived session. Nothing to evaluate on a day with no new
    # session — the earlier early-return already covered that case.
    jobs.set_step(conn, job_id, "Evaluating alerts")
    print("\n--- alerts ---")
    try:
        fired = alerts.evaluate_all(conn, verbose=True)
    except Exception as exc:  # noqa: BLE001 - a rule bug shouldn't fail the refresh
        fired = []
        failures.append(f"alerts: {type(exc).__name__}: {exc}")
        print(f"alert evaluation FAILED - {exc}")

    status = "ok" if not failures else "partial"
    detail = "clean" if not failures else "; ".join(failures)
    if fired:
        detail = f"{len(fired)} alert(s); {detail}"
    finish_run(conn, run_id, status,
               tickers=len(tickers), new_price_rows=new_rows,
               latest_session=after_session, detail=detail)

    print(f"\n=== {status}: {len(tickers)} tickers, {new_rows:,} new price rows, "
          f"latest session {after_session} ===")
    for failure in failures:
        print(f"  FAILURE: {failure}")

    jobs.finish(conn, job_id, "ok" if not failures else "error",
                f"Updated - {new_rows:,} new price rows, session {after_session}."
                if not failures else f"Ran with failures: {detail[:300]}")
    conn.close()
    return 1 if failures else 0


if __name__ == "__main__":
    _parser = argparse.ArgumentParser(description="Daily refresh")
    _parser.add_argument("--job-id", type=int, default=None)
    _ARGS = _parser.parse_args()
    run_log.tee_stdio("daily")
    try:
        sys.exit(main(_ARGS.job_id))
    except Exception:
        # Last resort: mark the run failed so the UI shows staleness rather than
        # leaving a 'running' row behind forever.
        traceback.print_exc()
        try:
            conn = db.connect()
            row = conn.execute(
                "SELECT id FROM pipeline_runs WHERE status = 'running' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row:
                finish_run(conn, row["id"], "error", detail=traceback.format_exc()[-500:])
            jobs.finish(conn, _ARGS.job_id, "error", traceback.format_exc()[-300:])
        except Exception:
            pass
        sys.exit(2)
