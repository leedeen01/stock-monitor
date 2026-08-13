"""Backfill orchestration: EDGAR -> normalize -> SQLite."""

import argparse
import sqlite3
from datetime import date, datetime, timezone

import concepts
import db
import edgar
from config import MIN_EXPECTED_HISTORY_YEARS

FUNDAMENTAL_COLUMNS = (
    "ticker", "concept", "period_start", "period_end", "duration_days",
    "fiscal_year", "fiscal_period", "form", "filed_at", "accession",
    "unit", "value",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def store_fundamentals(conn: sqlite3.Connection, ticker: str, rows: list[dict]) -> int:
    """Replace a ticker's normalized rows.

    Replace rather than upsert, because the concept mapping changes over time.
    An insert-only store leaves rows behind under their old concept names, and
    those stale rows keep winning: when `Depreciation` was demoted out of the
    D&A tag list, Microsoft and Alphabet carried on resolving D&A to the old
    depreciation-only figure, which understates EBITDA and inflates EV/EBITDA.
    The fix silently did nothing until the stale rows were cleared.

    Safe to do wholesale because companyfacts returns a company's complete
    filing history on every call — the normalized output is the whole truth for
    that ticker, not a delta.
    """
    placeholders = ", ".join(["?"] * len(FUNDAMENTAL_COLUMNS))
    with conn:  # single transaction: never leave a ticker with no fundamentals
        conn.execute("DELETE FROM fundamentals WHERE ticker = ?", (ticker.upper(),))
        conn.executemany(
            f"INSERT OR REPLACE INTO fundamentals ({', '.join(FUNDAMENTAL_COLUMNS)}) "
            f"VALUES ({placeholders})",
            [[row[c] for c in FUNDAMENTAL_COLUMNS] for row in rows],
        )
    return len(rows)


def ensure_watchlisted(conn: sqlite3.Connection, ticker: str, name: str, cik: int) -> None:
    conn.execute(
        """
        INSERT INTO watchlist (ticker, name, cik, added_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET name = excluded.name, cik = excluded.cik
        """,
        (ticker.upper(), name, cik, _now()),
    )
    conn.commit()


def log_ingest(conn: sqlite3.Connection, ticker: str, source: str, status: str, detail: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO ingest_log (ticker, source, ran_at, status, detail) VALUES (?, ?, ?, ?, ?)",
        (ticker.upper(), source, _now(), status, detail),
    )
    conn.commit()


def backfill_fundamentals(conn: sqlite3.Connection, ticker: str, verbose: bool = True) -> dict:
    ticker = ticker.upper()
    meta = edgar.resolve_ticker(ticker)
    ensure_watchlisted(conn, ticker, meta["name"], meta["cik"])

    # A reincorporated company's filings are split across CIKs; merge them.
    rows: list[dict] = []
    annual_only = False
    for cik in meta["ciks"]:
        facts = edgar.get_companyfacts(cik)
        rows.extend(concepts.normalize_companyfacts(ticker, facts))
        annual_only = annual_only or concepts.is_annual_only_filer(facts)

    count = store_fundamentals(conn, ticker, rows)
    coverage = concepts.coverage_report(rows)
    missing = concepts.missing_required(rows)
    currency = concepts.detect_reporting_currency(rows)

    span = conn.execute(
        "SELECT MIN(period_end) AS first, MAX(period_end) AS last FROM fundamentals WHERE ticker = ?",
        (ticker,),
    ).fetchone()
    years = _history_years(span["first"], span["last"])

    warnings = []
    unsupported_reason = None

    # A non-USD filer needs a daily FX series and, for ADRs, an ordinary-shares
    # -per-ADR ratio. Neither comes from EDGAR. Until both exist, refuse to
    # derive ratios rather than publish numbers that are wrong by the FX rate.
    if currency and currency != "USD":
        unsupported_reason = (
            f"reports in {currency}, priced in USD; needs FX conversion"
            + (" and an ADR ratio" if annual_only else "")
        )
        warnings.append(unsupported_reason)

    if annual_only:
        warnings.append("annual-only filer (20-F/IFRS): ~1 data point per year, coarse percentiles")
    if years is not None and years < MIN_EXPECTED_HISTORY_YEARS:
        warnings.append(
            f"only {years:.1f}y of history — likely a predecessor CIK; add it to CIK_OVERRIDES"
        )
    if missing:
        warnings.append(f"missing required concepts: {', '.join(missing)}")

    conn.execute(
        """
        UPDATE watchlist
        SET reporting_currency = ?, supported = ?, unsupported_reason = ?
        WHERE ticker = ?
        """,
        (currency, 0 if unsupported_reason else 1, unsupported_reason, ticker),
    )
    conn.commit()

    log_ingest(
        conn, ticker, "edgar",
        "ok" if not warnings else "partial",
        f"{count} facts from CIK(s) {meta['ciks']}; " + ("; ".join(warnings) or "clean"),
    )

    if verbose:
        cik_note = f"CIK {meta['cik']}" if len(meta["ciks"]) == 1 else f"CIKs {meta['ciks']}"
        print(f"{ticker} ({meta['name']}, {cik_note}): {count} facts stored")
        print(f"  period coverage: {span['first']} -> {span['last']}"
              + (f"  ({years:.1f}y)" if years is not None else ""))
        for w in warnings:
            print(f"  WARNING: {w}")

    return {
        "ticker": ticker,
        "facts": count,
        "missing": missing,
        "coverage": coverage,
        "annual_only": annual_only,
        "years": years,
        "warnings": warnings,
    }


def _history_years(first: str | None, last: str | None) -> float | None:
    if not first or not last:
        return None
    return (date.fromisoformat(last) - date.fromisoformat(first)).days / 365.25


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill EDGAR fundamentals")
    parser.add_argument("tickers", nargs="+", help="Ticker symbols, e.g. AAPL NVDA")
    args = parser.parse_args()

    conn = db.connect()
    db.init_schema(conn)
    for ticker in args.tickers:
        try:
            backfill_fundamentals(conn, ticker)
        except Exception as exc:  # noqa: BLE001 - report and continue the batch
            print(f"{ticker}: FAILED - {type(exc).__name__}: {exc}")
            log_ingest(conn, ticker, "edgar", "error", str(exc))
    conn.close()


if __name__ == "__main__":
    main()
