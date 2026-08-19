"use client";

import { useState } from "react";

import { refreshNow } from "@/app/actions";
import { useAdoptRunning, useJob } from "@/components/useJob";

/**
 * Runs the same job the scheduler runs, on demand — without holding the page.
 *
 * The click returns in milliseconds; the work continues in a detached process
 * and this polls for it. So the rest of the page stays live: you can open a
 * stock while a refresh is running, and the grid re-renders itself when the
 * numbers change.
 */
export function RefreshButton() {
  const [jobId, setJobId] = useState<number | null>(null);
  const [notice, setNotice] = useState<{ text: string; ok: boolean } | null>(null);

  const job = useJob(jobId, (finished) => {
    setNotice({
      text: finished.detail ?? (finished.status === "ok" ? "Done." : "Refresh failed."),
      ok: finished.status === "ok",
    });
    setJobId(null);
  });

  useAdoptRunning("refresh", setJobId);

  const running = jobId !== null;

  const start = async () => {
    setNotice(null);
    const result = await refreshNow();
    if (result.ok && result.jobId !== undefined) {
      setJobId(result.jobId);
    } else {
      setNotice({ text: result.message, ok: false });
    }
  };

  return (
    <div className="flex items-center gap-2">
      {running ? (
        // The step text is worth more than a spinner: the wait is EDGAR and
        // yfinance, and knowing which one it's on tells you nothing is stuck.
        <span className="max-w-xs truncate text-xs text-neutral-500 dark:text-neutral-400">
          {job?.step ?? "Starting…"}
        </span>
      ) : (
        notice && (
          <span
            className={`max-w-xs truncate text-xs ${
              notice.ok
                ? "text-emerald-600 dark:text-emerald-400"
                : "text-rose-600 dark:text-rose-400"
            }`}
            title={notice.text}
          >
            {notice.text}
          </span>
        )
      )}

      <button
        onClick={start}
        disabled={running}
        className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-50 disabled:opacity-60 dark:border-neutral-700 dark:hover:bg-neutral-900"
      >
        {running ? "Refreshing…" : "↻ Refresh"}
      </button>
    </div>
  );
}
