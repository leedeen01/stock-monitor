"use client";

import { useActionState } from "react";

import { signIn, type SignInResult } from "@/app/auth-actions";

export function LoginForm({
  next,
  configured,
}: {
  next: string;
  configured: boolean;
}) {
  const [state, action, pending] = useActionState<SignInResult, FormData>(
    signIn,
    null,
  );

  return (
    <form action={action} className="flex flex-col gap-3">
      <input type="hidden" name="next" value={next} />

      <label className="flex flex-col gap-1.5">
        <span className="text-xs text-neutral-500 dark:text-neutral-400">
          Password
        </span>
        <input
          name="password"
          type="password"
          autoFocus
          autoComplete="current-password"
          disabled={!configured}
          className="rounded-md border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-950"
        />
      </label>

      <button
        type="submit"
        disabled={pending || !configured}
        className="rounded-md bg-neutral-900 px-3 py-2 text-sm text-white disabled:opacity-60 dark:bg-neutral-100 dark:text-neutral-900"
      >
        {pending ? "Checking…" : "Sign in"}
      </button>

      {state?.error && (
        <p className="text-xs text-rose-600 dark:text-rose-400">{state.error}</p>
      )}
    </form>
  );
}
