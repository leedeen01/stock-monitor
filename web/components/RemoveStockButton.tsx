"use client";

import { useState, useTransition } from "react";

import { removeStock } from "@/app/actions";

/**
 * Two-step remove. A single-click delete sitting in every table row is far too
 * easy to hit by accident, so the first click only arms it.
 */
export function RemoveStockButton({ ticker }: { ticker: string }) {
  const [armed, setArmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  if (pending) {
    return <span className="text-xs text-neutral-400">removing…</span>;
  }

  if (!armed) {
    return (
      <button
        onClick={() => setArmed(true)}
        aria-label={`Remove ${ticker} from watchlist`}
        title={`Remove ${ticker}`}
        className="rounded px-1.5 py-0.5 text-neutral-300 transition-colors hover:bg-rose-50 hover:text-rose-600 dark:text-neutral-600 dark:hover:bg-rose-950 dark:hover:text-rose-400"
      >
        ×
      </button>
    );
  }

  return (
    <span className="flex items-center gap-1 whitespace-nowrap">
      <button
        onClick={() =>
          startTransition(async () => {
            const result = await removeStock(ticker);
            if (!result.ok) {
              setError(result.message);
              setArmed(false);
            }
          })
        }
        className="rounded bg-rose-600 px-1.5 py-0.5 text-[10px] font-medium text-white hover:bg-rose-700"
      >
        Remove
      </button>
      <button
        onClick={() => setArmed(false)}
        className="px-1 text-[10px] text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200"
      >
        Cancel
      </button>
      {error && <span className="text-[10px] text-rose-500">{error}</span>}
    </span>
  );
}
