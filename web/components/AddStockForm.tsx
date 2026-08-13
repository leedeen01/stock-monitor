"use client";

import { useActionState, useEffect, useRef, useState } from "react";
import { useFormStatus } from "react-dom";

import { addStock, type AddStockResult } from "@/app/actions";
import type { GroupRef } from "@/lib/queries";

/**
 * Add-stock popover.
 *
 * The trigger button stays mounted and the panel floats above the page rather
 * than expanding in the header flow — opening the form used to reflow the
 * header and shove the whole table down.
 *
 * Dismissal uses a document listener rather than a backdrop element, so a click
 * on a neighbouring control still reaches it instead of being swallowed. While a
 * backfill is running the panel refuses to close: the request takes 30-60s and
 * losing the progress text mid-flight would leave you with no idea whether it
 * worked.
 */
export function AddStockForm({ groups }: { groups: GroupRef[] }) {
  const [open, setOpen] = useState(false);
  const [state, action] = useActionState<AddStockResult | null, FormData>(
    addStock,
    null,
  );
  const root = useRef<HTMLDivElement>(null);
  const pendingRef = useRef(false);

  // Close once a ticker lands; leave it open on failure so the message is
  // attached to the form that produced it and the input can be corrected.
  useEffect(() => {
    if (state?.ok) setOpen(false);
  }, [state]);

  useEffect(() => {
    if (!open) return;

    const onPointerDown = (e: MouseEvent) => {
      if (pendingRef.current) return;
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !pendingRef.current) setOpen(false);
    };

    document.addEventListener("mousedown", onPointerDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={root} className="relative flex items-center gap-3">
      {state?.message && !open && (
        <span
          className={`max-w-xs truncate text-xs ${
            state.ok
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-rose-600 dark:text-rose-400"
          }`}
          title={state.message}
        >
          {state.message}
        </span>
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
          //
          // Widths avoid calc(): Tailwind arbitrary values can't hold bare
          // spaces, and `calc(100vw-2rem)` without them is invalid CSS, so the
          // rule is dropped and the panel collapses to content width.
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

            <Submit pendingRef={pendingRef} />

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

function Submit({
  pendingRef,
}: {
  pendingRef: React.RefObject<boolean>;
}) {
  const { pending } = useFormStatus();
  // Surfaced to the parent so an outside click can't dismiss a running backfill.
  pendingRef.current = pending;

  return (
    <>
      <button
        type="submit"
        disabled={pending}
        className="mt-3 w-full rounded-md bg-neutral-900 px-3 py-1.5 text-sm text-white disabled:opacity-60 dark:bg-neutral-100 dark:text-neutral-900"
      >
        {pending ? "Fetching filings…" : "Add and backfill"}
      </button>
      {pending && (
        <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-400">
          Pulling EDGAR filings and deriving history. This takes 30-60 seconds.
        </p>
      )}
    </>
  );
}
