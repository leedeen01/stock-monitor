"use client";

import { useState, useTransition } from "react";

import { refreshNow, type RefreshResult } from "@/app/actions";

/**
 * Runs the same job the scheduler runs, on demand.
 *
 * It takes 30-60s across the watchlist, so the pending state names what it is
 * doing rather than showing a bare spinner — the wait is mostly EDGAR and
 * yfinance, and it's worth knowing nothing is stuck.
 */
export function RefreshButton() {
  const [result, setResult] = useState<RefreshResult | null>(null);
  const [pending, startTransition] = useTransition();

  return (
    <div className="flex items-center gap-2">
      {result && !pending && (
        <span
          className={`max-w-xs truncate text-xs ${
            result.ok
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-rose-600 dark:text-rose-400"
          }`}
          title={result.message}
        >
          {result.message}
        </span>
      )}
      <button
        onClick={() =>
          startTransition(async () => {
            setResult(null);
            setResult(await refreshNow());
          })
        }
        disabled={pending}
        className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-50 disabled:opacity-60 dark:border-neutral-700 dark:hover:bg-neutral-900"
      >
        {pending ? "Refreshing…" : "↻ Refresh"}
      </button>
    </div>
  );
}
