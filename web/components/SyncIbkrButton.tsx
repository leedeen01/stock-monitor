"use client";

import { useState } from "react";

import { syncIbkr } from "@/app/ibkr-actions";
import { useAdoptRunning, useJob } from "@/components/useJob";

/**
 * Pull holdings now instead of waiting for the 06:00 job.
 *
 * Same shape as the refresh button: the click returns immediately and this
 * polls, so fetching a statement — which can take a minute while IBKR builds
 * it — never freezes the page.
 */
export function SyncIbkrButton() {
  const [jobId, setJobId] = useState<number | null>(null);
  const [notice, setNotice] = useState<{ text: string; ok: boolean } | null>(null);

  const job = useJob(jobId, (finished) => {
    setNotice({
      text: finished.detail ?? (finished.status === "ok" ? "Synced." : "Sync failed."),
      ok: finished.status === "ok",
    });
    setJobId(null);
  });

  useAdoptRunning("ibkr", setJobId);

  const running = jobId !== null;

  const start = async () => {
    setNotice(null);
    const result = await syncIbkr();
    if (result.ok && result.jobId !== undefined) setJobId(result.jobId);
    else setNotice({ text: result.message, ok: false });
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        onClick={start}
        disabled={running}
        className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-50 disabled:opacity-60 dark:border-neutral-700 dark:hover:bg-neutral-900"
      >
        {running ? "Syncing…" : "↻ Sync now"}
      </button>

      {running ? (
        // IBKR builds the report on demand, so the wait is genuine. Naming the
        // step is the difference between patience and suspicion.
        <span className="text-xs text-neutral-500 dark:text-neutral-400">
          {job?.step ?? "Starting…"}
        </span>
      ) : (
        notice && (
          <span
            className={`max-w-md truncate text-xs ${
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
    </div>
  );
}
