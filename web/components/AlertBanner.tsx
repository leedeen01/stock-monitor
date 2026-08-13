"use client";

import Link from "next/link";
import { useEffect, useState, useTransition } from "react";

import { acknowledgeAlert, acknowledgeAll } from "@/app/alert-actions";
import type { AlertEvent } from "@/lib/queries";

// Remembers the newest alert id that was visible when you hid the banner.
const HIDDEN_KEY = "stock-monitor.alerts-hidden-through";

/**
 * Open alerts. Each one is a crossing — the session a condition became true —
 * not a restatement of a condition that has been true for weeks. That's what
 * keeps this list short enough to read.
 *
 * Hiding and dismissing are deliberately different actions. Dismiss
 * acknowledges permanently; hide just collapses the panel, and un-hides itself
 * the moment something new fires. A control that quietly suppressed future
 * alerts would be the worst possible outcome for a monitoring tool.
 */
export function AlertBanner({ alerts }: { alerts: AlertEvent[] }) {
  const [dismissed, setDismissed] = useState<Set<number>>(new Set());
  const [hidden, setHidden] = useState(false);
  const [pending, startTransition] = useTransition();

  const visible = alerts.filter((a) => !dismissed.has(a.id));
  const newestId = alerts.reduce((max, a) => Math.max(max, a.id), 0);

  // Read the stored preference after mount — touching localStorage during
  // render would desync the server-rendered HTML.
  useEffect(() => {
    const stored = Number(window.localStorage.getItem(HIDDEN_KEY) ?? 0);
    // Anything newer than what was hidden reopens the panel: you asked to hide
    // what you had seen, not to stop being told about what happens next.
    setHidden(newestId > 0 && stored >= newestId);
  }, [newestId]);

  function hide() {
    window.localStorage.setItem(HIDDEN_KEY, String(newestId));
    setHidden(true);
  }

  function show() {
    window.localStorage.removeItem(HIDDEN_KEY);
    setHidden(false);
  }

  if (visible.length === 0) return null;

  if (hidden) {
    return (
      <div className="mb-6 flex items-center gap-3 rounded-lg border border-neutral-200 px-3 py-2 text-xs text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber-500" />
        <span>
          {visible.length} alert{visible.length === 1 ? "" : "s"} hidden
        </span>
        <button
          onClick={show}
          className="underline-offset-2 hover:underline hover:text-neutral-800 dark:hover:text-neutral-200"
        >
          Show
        </button>
        <Link
          href="/alerts"
          className="ml-auto underline-offset-2 hover:underline hover:text-neutral-800 dark:hover:text-neutral-200"
        >
          Manage rules
        </Link>
      </div>
    );
  }

  return (
    <section className="mb-6 rounded-lg border border-amber-300 bg-amber-50/60 p-4 dark:border-amber-900 dark:bg-amber-950/30">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium text-amber-900 dark:text-amber-200">
          {visible.length} alert{visible.length === 1 ? "" : "s"}
        </h2>
        <div className="flex items-center gap-3 text-xs">
          <Link
            href="/alerts"
            className="text-amber-800 underline-offset-2 hover:underline dark:text-amber-300"
          >
            Manage rules
          </Link>
          <button
            onClick={hide}
            title="Collapse the panel. Alerts stay open and it reopens when something new fires."
            className="text-amber-700 hover:text-amber-900 dark:text-amber-400 dark:hover:text-amber-200"
          >
            Hide
          </button>
          <button
            disabled={pending}
            title="Mark every alert as seen. They won't come back until the condition crosses again."
            onClick={() =>
              startTransition(async () => {
                setDismissed(new Set(alerts.map((a) => a.id)));
                await acknowledgeAll();
              })
            }
            className="text-amber-700 hover:text-amber-900 disabled:opacity-50 dark:text-amber-400 dark:hover:text-amber-200"
          >
            Dismiss all
          </button>
        </div>
      </div>

      <ul className="space-y-1">
        {visible.map((alert) => (
          <li
            key={alert.id}
            className="flex items-start justify-between gap-3 text-sm"
          >
            <span className="text-amber-900 dark:text-amber-100">
              <Link
                href={`/stock/${alert.ticker}`}
                className="font-medium underline-offset-2 hover:underline"
              >
                {alert.ticker}
              </Link>{" "}
              <span className="text-amber-800/90 dark:text-amber-200/80">
                {stripTicker(alert.detail, alert.ticker)}
              </span>
              <span className="ml-2 text-xs text-amber-700/70 dark:text-amber-300/60">
                {alert.ruleName} · {alert.triggerDate}
              </span>
            </span>
            <button
              disabled={pending}
              aria-label={`Dismiss alert for ${alert.ticker}`}
              onClick={() =>
                startTransition(async () => {
                  setDismissed((prev) => new Set(prev).add(alert.id));
                  await acknowledgeAlert(alert.id);
                })
              }
              className="shrink-0 rounded px-1.5 text-amber-600 hover:bg-amber-100 hover:text-amber-900 disabled:opacity-50 dark:text-amber-500 dark:hover:bg-amber-900/40 dark:hover:text-amber-200"
            >
              ×
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The ticker is already a link, so drop the duplicate leading symbol. */
function stripTicker(detail: string, ticker: string): string {
  return detail.startsWith(`${ticker} `) ? detail.slice(ticker.length + 1) : detail;
}
