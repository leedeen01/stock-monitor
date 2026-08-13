"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Explains how to actually use a metric, not just what it measures.
 *
 * Click to toggle rather than hover: the guidance runs to a few sentences, and
 * a hover tooltip that vanishes when the pointer drifts is useless for reading.
 * Click also works on touch, where hover doesn't exist.
 *
 * Dismissal uses a document listener rather than a full-screen backdrop
 * element. A backdrop swallows the next click anywhere — including on a
 * neighbouring info button — so moving between two metrics would cost two
 * clicks: one to dismiss, one to open. This way each open panel closes itself
 * when a click lands outside it, and the click still reaches whatever it hit.
 */
export function MetricInfo({
  label,
  description,
  usage,
}: {
  label: string;
  description: string;
  usage: string;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement>(null);

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

  return (
    <span ref={root} className="relative inline-flex align-middle">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={`How to use ${label}`}
        className={`flex h-4 w-4 items-center justify-center rounded-full border text-[9px] font-semibold leading-none transition-colors ${
          open
            ? "border-neutral-700 bg-neutral-800 text-white dark:border-neutral-300 dark:bg-neutral-200 dark:text-neutral-900"
            : "border-neutral-300 text-neutral-400 hover:border-neutral-500 hover:text-neutral-700 dark:border-neutral-700 dark:text-neutral-500 dark:hover:border-neutral-500 dark:hover:text-neutral-300"
        }`}
      >
        i
      </button>

      {open && (
        <span
          role="dialog"
          aria-label={`How to use ${label}`}
          className="absolute left-0 top-6 z-30 block w-[20rem] max-w-[85vw] rounded-lg border border-neutral-200 bg-white p-3 text-left shadow-lg dark:border-neutral-700 dark:bg-neutral-900"
        >
          <span className="block text-xs font-semibold">{label}</span>
          <span className="mt-1 block text-xs leading-relaxed text-neutral-500 dark:text-neutral-400">
            {description}
          </span>
          {usage && (
            <span className="mt-2.5 block border-t border-neutral-100 pt-2.5 dark:border-neutral-800">
              <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wide text-neutral-400">
                How to use it
              </span>
              {usage.split("\n\n").map((para, i) => (
                <span
                  key={i}
                  className="mt-1.5 block text-xs leading-relaxed text-neutral-700 first:mt-0 dark:text-neutral-300"
                >
                  {para}
                </span>
              ))}
            </span>
          )}
        </span>
      )}
    </span>
  );
}
