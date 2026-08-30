import type { ReactNode } from "react";

/**
 * Shared form and messaging primitives.
 *
 * No "use client" directive on purpose: none of these use hooks, so they can be
 * imported by server components (Notice, on the auth pages) and pulled into a
 * client bundle by the forms without maintaining two versions.
 *
 * The point is that a style decision lives in exactly one place. Before this,
 * the input class string appeared nine times and the warning box four, which is
 * how a design drifts one careless edit at a time.
 */

const INPUT =
  "rounded-md border border-neutral-300 px-3 py-2 text-sm " +
  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-neutral-500 " +
  "dark:border-neutral-700 dark:bg-neutral-950";

export function Field({
  label,
  name,
  type = "text",
  hint,
  mono = false,
  ...rest
}: {
  label: string;
  name: string;
  type?: string;
  hint?: string;
  mono?: boolean;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "name" | "type">) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs text-neutral-500 dark:text-neutral-400">
        {label}
        {hint && (
          <span className="text-neutral-400 dark:text-neutral-500"> — {hint}</span>
        )}
      </span>
      <input
        name={name}
        type={type}
        className={mono ? `${INPUT} font-mono` : INPUT}
        {...rest}
      />
    </label>
  );
}

export function SubmitButton({
  pending,
  idle,
  busy,
}: {
  pending: boolean;
  idle: string;
  busy: string;
}) {
  return (
    <button
      type="submit"
      disabled={pending}
      className="rounded-md bg-neutral-900 px-3 py-2 text-sm text-white disabled:opacity-60 dark:bg-neutral-100 dark:text-neutral-900"
    >
      {pending ? busy : idle}
    </button>
  );
}

const TONES = {
  warn: "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300",
  error:
    "border-rose-300 bg-rose-50 text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300",
  info: "border-neutral-200 bg-neutral-50 text-neutral-600 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-400",
} as const;

export function Notice({
  tone = "info",
  children,
}: {
  tone?: keyof typeof TONES;
  children: ReactNode;
}) {
  return (
    <p className={`rounded-md border p-3 text-xs ${TONES[tone]}`}>{children}</p>
  );
}

/** Inline validation message for a form. Renders nothing when there is none,
 *  so callers don't each repeat the null check. */
export function FormError({ error }: { error?: string | null }) {
  if (!error) return null;
  return (
    <p className="text-xs text-rose-600 dark:text-rose-400" role="alert">
      {error}
    </p>
  );
}
