"use server";

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { refresh } from "next/cache";

import { db } from "@/lib/db";
import { INGEST_DIR, PYTHON } from "@/lib/paths";

const run = promisify(execFile);

export type AddStockResult = {
  ok: boolean;
  message: string;
};

/**
 * Add a ticker and backfill it.
 *
 * This runs the Python pipeline synchronously — EDGAR fetch, price history,
 * ratio derivation — which takes 10-30s for a new ticker. Awaiting keeps the
 * result definite: when the form returns, either the row is on the grid with
 * real numbers behind it, or you get the reason it isn't.
 */
export async function addStock(
  _prev: AddStockResult | null,
  formData: FormData,
): Promise<AddStockResult> {
  const ticker = String(formData.get("ticker") ?? "")
    .trim()
    .toUpperCase();
  const groupIds = formData
    .getAll("groups")
    .map((g) => Number(g))
    .filter((n) => Number.isInteger(n) && n > 0);

  if (!/^[A-Z][A-Z.\-]{0,9}$/.test(ticker)) {
    return { ok: false, message: `"${ticker}" is not a valid ticker symbol.` };
  }
  if (groupIds.length === 0) {
    return { ok: false, message: "Pick at least one group." };
  }

  try {
    await run(PYTHON, ["backfill.py", ticker], {
      cwd: INGEST_DIR,
      timeout: 180_000,
    });

    const conn = db();
    const row = conn
      .prepare("SELECT supported, unsupported_reason FROM watchlist WHERE ticker = ?")
      .get(ticker) as
      | { supported: number; unsupported_reason: string | null }
      | undefined;

    if (!row) {
      return { ok: false, message: `${ticker} was not found in the SEC registry.` };
    }
    if (!row.supported) {
      return {
        ok: false,
        message: `${ticker} cannot be tracked: ${row.unsupported_reason}`,
      };
    }

    await run(PYTHON, ["prices.py", ticker], { cwd: INGEST_DIR, timeout: 180_000 });
    await run(PYTHON, ["derive.py", ticker], { cwd: INGEST_DIR, timeout: 180_000 });

    // Product revenue. Deliberately non-fatal: plenty of companies publish no
    // breakdown that reconciles (AMD, Chevron, ExxonMobil), and that is a fact
    // about the filer rather than a failure to add the stock. The scheduled
    // job only refreshes segments when a new 10-K lands, so without this a
    // newly added ticker would show nothing until some unrelated filing
    // happened to trigger it.
    await run(PYTHON, ["segments.py", ticker], {
      cwd: INGEST_DIR,
      timeout: 300_000,
    }).catch(() => undefined);

    // Group assignment, and the first group becomes the default view.
    const insert = conn.prepare(
      "INSERT OR IGNORE INTO stock_groups (ticker, group_id) VALUES (?, ?)",
    );
    for (const id of groupIds) insert.run(ticker, id);
    conn
      .prepare("UPDATE watchlist SET default_group_id = ? WHERE ticker = ?")
      .run(groupIds[0], ticker);

    // Record the price at the moment of adding, so the grid can show
    // since-added return.
    const latest = conn
      .prepare("SELECT close FROM ratios_daily WHERE ticker = ? ORDER BY date DESC LIMIT 1")
      .get(ticker) as { close: number | null } | undefined;
    if (latest?.close) {
      conn
        .prepare("UPDATE watchlist SET added_price = ? WHERE ticker = ? AND added_price IS NULL")
        .run(latest.close, ticker);
    }

    const count = conn
      .prepare("SELECT COUNT(*) c FROM ratios_daily WHERE ticker = ?")
      .get(ticker) as { c: number };

    const segmentLines = conn
      .prepare(
        "SELECT COUNT(DISTINCT label) c FROM segment_revenue WHERE ticker = ? AND is_subtotal = 0",
      )
      .get(ticker) as { c: number };

    refresh();
    return {
      ok: true,
      message:
        `Added ${ticker} — ${count.c.toLocaleString()} days of derived history` +
        (segmentLines.c > 0
          ? `, ${segmentLines.c} product lines.`
          : ". No product breakdown published."),
    };
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    return { ok: false, message: `Backfill failed: ${detail.slice(0, 300)}` };
  }
}

export type RefreshResult = {
  ok: boolean;
  message: string;
};

/** A run still marked 'running' after this long crashed without recording it. */
const STALE_RUN_MINUTES = 30;

/**
 * Run the daily refresh on demand.
 *
 * Invokes `daily_update.py` directly rather than poking the Windows task, so
 * this takes the identical code path — same skip logic, same logging, same
 * pipeline_runs record — while staying synchronous enough to report what
 * actually happened. Start-ScheduledTask would be fire-and-forget with nothing
 * to show the user.
 */
export async function refreshNow(): Promise<RefreshResult> {
  const conn = db();

  // The scheduled job may already be mid-run. Two concurrent derivations would
  // both DELETE and rewrite ratios_daily for the same tickers.
  const inFlight = conn
    .prepare(
      "SELECT started_at FROM pipeline_runs WHERE status = 'running' ORDER BY id DESC LIMIT 1",
    )
    .get() as { started_at: string } | undefined;

  if (inFlight) {
    const minutes = (Date.now() - new Date(inFlight.started_at).getTime()) / 60_000;
    if (minutes < STALE_RUN_MINUTES) {
      return {
        ok: false,
        message: `A refresh started ${Math.round(minutes)}m ago is still running.`,
      };
    }
  }

  try {
    // Exit code 1 means "ran, but some tickers failed" — a real outcome, not a
    // failure to run. So the database is the source of truth here, not the
    // exit status, and a non-zero exit is deliberately not rethrown.
    await run(PYTHON, ["daily_update.py"], {
      cwd: INGEST_DIR,
      timeout: 600_000,
      maxBuffer: 10 * 1024 * 1024,
    }).catch(() => undefined);

    const row = conn
      .prepare(
        `SELECT status, new_price_rows, latest_session, detail
         FROM pipeline_runs ORDER BY id DESC LIMIT 1`,
      )
      .get() as
      | {
          status: string; new_price_rows: number | null;
          latest_session: string | null; detail: string | null;
        }
      | undefined;

    refresh();

    if (!row) return { ok: false, message: "Refresh produced no run record." };

    switch (row.status) {
      case "ok":
        return {
          ok: true,
          message: `Updated — ${row.new_price_rows ?? 0} new price rows, session ${row.latest_session}.`,
        };
      case "skipped":
        return {
          ok: true,
          message: `Already current — no new session since ${row.latest_session}.`,
        };
      case "partial":
        return {
          ok: false,
          message: `Ran with failures: ${(row.detail ?? "").slice(0, 200)}`,
        };
      default:
        return {
          ok: false,
          message: `Refresh ${row.status}: ${(row.detail ?? "").slice(0, 200)}`,
        };
    }
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    return { ok: false, message: `Refresh failed: ${detail.slice(0, 300)}` };
  }
}

export type RemoveStockResult = {
  ok: boolean;
  message: string;
};

/**
 * Remove a ticker from the watchlist.
 *
 * The ingested history is deliberately kept — only the watchlist row and group
 * assignments go. Re-adding is then instant and touches no network, and an
 * accidental click costs nothing. Purging the underlying data is available via
 * `manage.py remove --purge`, where it takes a deliberate flag.
 */
export async function removeStock(ticker: string): Promise<RemoveStockResult> {
  const symbol = ticker.trim().toUpperCase();
  if (!/^[A-Z][A-Z.\-]{0,9}$/.test(symbol)) {
    return { ok: false, message: "Invalid ticker." };
  }

  const conn = db();
  const existing = conn
    .prepare("SELECT ticker FROM watchlist WHERE ticker = ?")
    .get(symbol) as { ticker: string } | undefined;
  if (!existing) {
    return { ok: false, message: `${symbol} is not on the watchlist.` };
  }

  const tx = conn.transaction(() => {
    conn.prepare("DELETE FROM stock_groups WHERE ticker = ?").run(symbol);
    conn.prepare("DELETE FROM watchlist WHERE ticker = ?").run(symbol);
  });
  tx();

  refresh();
  return { ok: true, message: `Removed ${symbol}. History kept — re-adding is instant.` };
}
