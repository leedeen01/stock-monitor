"use server";

import { refresh } from "next/cache";

import { db } from "@/lib/db";
import { requireAction } from "@/lib/guard";
import { expireStaleJobs, jobRunning, startJob, STALE_JOB_MINUTES } from "@/lib/jobs";

/**
 * Long work runs detached, not awaited.
 *
 * These actions used to `await` the Python pipeline — 30-60s for an add, up to
 * several minutes for a refresh. A server action that stays pending blocks more
 * than its own button: Next queues client navigations behind an in-flight
 * action, so clicking into a stock did nothing until the job finished.
 *
 * Now each action inserts a `jobs` row, spawns the process detached, and
 * returns in milliseconds. The browser polls /api/jobs for progress. Output
 * goes to data/logs/ rather than a pipe, which is why stdio can be discarded —
 * both scripts tee to a log file of their own.
 */

export type StartResult = {
  ok: boolean;
  jobId?: number;
  message: string;
};

export async function addStock(
  _prev: StartResult | null,
  formData: FormData,
): Promise<StartResult> {
  const user = await requireAction();

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

  // Which market decides everything downstream: the filings source, the
  // yfinance symbol, the currency. Asked rather than guessed — probing for it
  // once resolved SPYL to an entirely different fund.
  const market = String(formData.get("market") ?? "US").toUpperCase();
  if (!/^[A-Z]{2,6}$/.test(market)) {
    return { ok: false, message: "Invalid market." };
  }

  const conn = db();
  expireStaleJobs();

  const already = conn
    .prepare("SELECT ticker FROM watchlist WHERE ticker = ? AND user_id = ?")
    .get(ticker, user.id);
  if (already) {
    return { ok: false, message: `${ticker} is already on your watchlist.` };
  }

  if (jobRunning("add", { userId: user.id, target: ticker })) {
    return { ok: false, message: `${ticker} is already being added.` };
  }

  const jobId = startJob({
    script: "add_ticker.py",
    args: [
      ticker,
      "--groups", groupIds.join(","),
      "--user-id", String(user.id),
      "--market", market,
    ],
    kind: "add",
    userId: user.id,
    target: ticker,
    firstStep: `Starting ${ticker}`,
  });

  return { ok: true, jobId, message: `Adding ${ticker}…` };
}

export async function refreshNow(): Promise<StartResult> {
  const user = await requireAction();

  const conn = db();
  expireStaleJobs();

  if (jobRunning("refresh")) {
    return { ok: false, message: "A refresh is already running." };
  }

  // The scheduled job leaves no `jobs` row, so check its record too. Two
  // concurrent derivations would both DELETE and rewrite ratios_daily.
  const scheduled = conn
    .prepare(
      "SELECT started_at FROM pipeline_runs WHERE status = 'running' ORDER BY id DESC LIMIT 1",
    )
    .get() as { started_at: string } | undefined;
  if (scheduled) {
    const minutes = (Date.now() - new Date(scheduled.started_at).getTime()) / 60_000;
    if (minutes < STALE_JOB_MINUTES) {
      return {
        ok: false,
        message: `The scheduled refresh started ${Math.round(minutes)}m ago is still running.`,
      };
    }
  }

  const jobId = startJob({
    script: "daily_update.py",
    args: [],
    kind: "refresh",
    userId: user.id,
    firstStep: "Starting",
  });
  return { ok: true, jobId, message: "Refresh started." };
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
 *
 * Stays synchronous: it is two DELETEs, not a pipeline.
 */
export async function removeStock(ticker: string): Promise<RemoveStockResult> {
  const user = await requireAction();

  const symbol = ticker.trim().toUpperCase();
  if (!/^[A-Z][A-Z.\-]{0,9}$/.test(symbol)) {
    return { ok: false, message: "Invalid ticker." };
  }

  const conn = db();
  const existing = conn
    .prepare("SELECT ticker FROM watchlist WHERE ticker = ? AND user_id = ?")
    .get(symbol, user.id) as { ticker: string } | undefined;
  if (!existing) {
    return { ok: false, message: `${symbol} is not on your watchlist.` };
  }

  const tx = conn.transaction(() => {
    conn.prepare("DELETE FROM stock_groups WHERE ticker = ? AND user_id = ?")
      .run(symbol, user.id);
    conn.prepare("DELETE FROM watchlist WHERE ticker = ? AND user_id = ?")
      .run(symbol, user.id);
  });
  tx();

  refresh();
  return { ok: true, message: `Removed ${symbol}. History kept — re-adding is instant.` };
}
