"use client";

import { useActionState, useEffect, useRef, useState } from "react";

import { addStock, type StartResult } from "@/app/actions";
import { useAdoptRunning, useJob } from "@/components/useJob";
import type { GroupRef } from "@/lib/queries";

/**
 * Add-stock popover.
 *
 * The trigger button stays mounted and the panel floats above the page rather
 * than expanding in the header flow — opening the form used to reflow the
 * header and shove the whole table down.
 *
 * Submitting no longer waits for the backfill. The action starts a detached job
 * and returns immediately, so the panel closes at once and progress shows
 * beside the trigger. Previously the popover had to refuse to close for 30-60s
 * to avoid losing the only progress indicator, which also froze navigation.
 */
export function AddStockForm({ groups }: { groups: GroupRef[] }) {
  const [open, setOpen] = useState(false);
  const [state, action] = useActionState<StartResult | null, FormData>(
    addStock,
    null,
  );
  const root = useRef<HTMLDivElement>(null);

  const [jobId, setJobId] = useState<number | null>(null);
  const [notice, setNotice] = useState<{ text: string; ok: boolean } | null>(null);

  const job = useJob(jobId, (finished) => {
    setNotice({
      text: finished.detail ?? (finished.status === "ok" ? "Added." : "Add failed."),
      ok: finished.status === "ok",
    });
    setJobId(null);
  });

  useAdoptRunning("add", setJobId);

  // A started job closes the panel; a rejected one keeps it open so the message
  // stays attached to the input that produced it.
  useEffect(() => {
    if (state?.ok && state.jobId !== undefined) {
      setJobId(state.jobId);
      setNotice(null);
      setOpen(false);
    }
  }, [state]);

  useEffect(() => {
    if (!open) return;

    const onPointerDown = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };

    document.addEventListener("mousedown", onPointerDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const running = jobId !== null;

  return (
    <div ref={root} className="relative flex items-center gap-3">
      {running ? (
        <span className="max-w-xs truncate text-xs text-neutral-500 dark:text-neutral-400">
          {job?.step ?? "Starting…"}
        </span>
      ) : (
        notice &&
        !open && (
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
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="dialog"
        className={`rounded-md border px-3 py-1.5 text-sm transition-colors ${
          open
            ? "border-neutral-900 bg-neutral-900 text-white dark:border-neutral-100 dark:bg-neutral-100 dark:text-neutral-900"
            : "border-neutral-300 hover:bg-neutral-50 dark:border-neutral-700 dark:hover:bg-neutral-900"
        }`}
      >
        + Add stock
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Add a stock to the watchlist"
          // Anchored popover on desktop; viewport-centred sheet on mobile.
          // Right-aligning to the trigger overflows once the header wraps —
          // at 375px the button's right edge sits at 321, so a 338px panel
          // hangs 17px off the left. Centring on the viewport is immune to
          // wherever the button ends up.
          className="fixed left-4 right-4 top-1/2 z-40 -translate-y-1/2 rounded-lg border border-neutral-200 bg-white p-4 text-left shadow-xl dark:border-neutral-700 dark:bg-neutral-900 sm:absolute sm:left-auto sm:right-0 sm:top-full sm:mt-2 sm:w-[22rem] sm:max-w-[90vw] sm:translate-y-0"
        >
          <form action={action}>
            <input
              name="ticker"
              placeholder="Ticker, e.g. INTC"
              autoFocus
              autoComplete="off"
              className="w-full rounded-md border border-neutral-300 px-2.5 py-1.5 font-mono text-sm uppercase dark:border-neutral-700 dark:bg-neutral-950"
            />

            <fieldset className="mt-3">
              <legend className="text-xs text-neutral-500 dark:text-neutral-400">
                Groups — these decide which metrics the deep dive shows
              </legend>
              <div className="mt-1.5 space-y-1">
                {groups.map((g) => (
                  <label
                    key={g.id}
                    className="flex cursor-pointer items-center gap-2 text-sm"
                  >
                    <input
                      type="checkbox"
                      name="groups"
                      value={g.id}
                      className="rounded border-neutral-400"
                    />
                    {g.name}
                  </label>
                ))}
              </div>
            </fieldset>

            <button
              type="submit"
              className="mt-3 w-full rounded-md bg-neutral-900 px-3 py-1.5 text-sm text-white disabled:opacity-60 dark:bg-neutral-100 dark:text-neutral-900"
            >
              Add and backfill
            </button>

            <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-400">
              Runs in the background — you can keep using the page.
            </p>

            {state?.message && !state.ok && (
              <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">
                {state.message}
              </p>
            )}
          </form>
        </div>
      )}
    </div>
  );
}
