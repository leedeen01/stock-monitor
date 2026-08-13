import Link from "next/link";

import { AddStockForm } from "@/components/AddStockForm";
import { AlertBanner } from "@/components/AlertBanner";
import { RefreshButton } from "@/components/RefreshButton";
import { WatchlistGrid } from "@/components/WatchlistGrid";
import {
  getGroups,
  getOpenAlerts,
  getPipelineStatus,
  getWatchlist,
  type PipelineStatus,
} from "@/lib/queries";

export const dynamic = "force-dynamic";

export default function Home() {
  const rows = getWatchlist();
  const groups = getGroups();
  const pipeline = getPipelineStatus();
  const alerts = getOpenAlerts();

  return (
    <main className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Watchlist</h1>
          <Freshness status={pipeline} />
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Link
            href="/alerts"
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:border-neutral-700 dark:hover:bg-neutral-900"
          >
            Alerts
            {alerts.length > 0 && (
              <span className="ml-1.5 rounded-full bg-amber-500 px-1.5 text-xs text-white">
                {alerts.length}
              </span>
            )}
          </Link>
          <RefreshButton />
          <AddStockForm groups={groups} />
        </div>
      </header>

      <AlertBanner alerts={alerts} />

      {rows.length === 0 ? <EmptyState /> : <WatchlistGrid rows={rows} />}
    </main>
  );
}

/**
 * Data freshness. A refresh job that quietly stops is the failure mode that
 * matters here — the grid still renders confident-looking numbers that are
 * wrong by however long it has been broken. So the state of the last run stays
 * on screen rather than buried in a log.
 */
function Freshness({ status }: { status: PipelineStatus }) {
  if (!status.status) {
    return (
      <p className="mt-2 text-xs text-amber-600 dark:text-amber-500">
        No scheduled run recorded yet.
      </p>
    );
  }

  const ago =
    status.hoursAgo === null
      ? "unknown"
      : status.hoursAgo < 1
        ? `${Math.round(status.hoursAgo * 60)}m ago`
        : status.hoursAgo < 48
          ? `${Math.round(status.hoursAgo)}h ago`
          : `${Math.round(status.hoursAgo / 24)}d ago`;

  const stale = status.stale;
  const partial = status.status === "partial";
  const tone = stale
    ? "text-rose-600 dark:text-rose-400"
    : partial
      ? "text-amber-600 dark:text-amber-500"
      : "text-neutral-400 dark:text-neutral-500";
  const dot = stale ? "bg-rose-500" : partial ? "bg-amber-500" : "bg-emerald-500";

  return (
    <p className={`mt-2 text-xs ${tone}`}>
      <span
        className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle ${dot}`}
      />
      Updated {ago}
      {status.latestSession && ` · session ${status.latestSession}`}
      {partial && " · some tickers failed"}
      {stale && " · refresh may have stopped"}
    </p>
  );
}

function EmptyState() {
  return (
    <div className="rounded-lg border border-dashed border-neutral-300 p-12 text-center dark:border-neutral-700">
      <p className="text-sm text-neutral-500">
        No stocks yet. Add one above, or run the ingest pipeline:
      </p>
      <code className="mt-2 block font-mono text-xs text-neutral-400">
        python backfill.py AAPL &amp;&amp; python prices.py &amp;&amp; python derive.py
      </code>
    </div>
  );
}
