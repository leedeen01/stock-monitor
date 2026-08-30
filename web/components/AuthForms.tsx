"use client";

import Link from "next/link";
import { useActionState } from "react";

import { linkIbkr, signIn, signUp, type FormResult } from "@/app/auth-actions";
import { Field, FormError, SubmitButton } from "@/components/ui";

/**
 * The three credential forms.
 *
 * Each is the same shape — an action, a pending flag, an error — so they share
 * one hook signature and differ only in their fields. Anything visual lives in
 * components/ui.
 */

function useAuthForm(
  action: (prev: FormResult, data: FormData) => Promise<FormResult>,
) {
  return useActionState<FormResult, FormData>(action, null);
}

export function LoginForm({ next }: { next: string }) {
  const [state, action, pending] = useAuthForm(signIn);

  return (
    <form action={action} className="flex flex-col gap-3">
      <input type="hidden" name="next" value={next} />
      <Field label="Email" name="email" type="email" autoFocus autoComplete="username" />
      <Field
        label="Password"
        name="password"
        type="password"
        autoComplete="current-password"
      />
      <SubmitButton pending={pending} idle="Sign in" busy="Checking…" />
      <FormError error={state?.error} />
    </form>
  );
}

export function SignupForm({ needsInvite }: { needsInvite: boolean }) {
  const [state, action, pending] = useAuthForm(signUp);

  return (
    <form action={action} className="flex flex-col gap-3">
      <Field label="Email" name="email" type="email" autoFocus autoComplete="username" />
      <Field
        label="Password"
        hint="at least 10 characters"
        name="password"
        type="password"
        autoComplete="new-password"
      />
      <Field
        label="Confirm password"
        name="confirm"
        type="password"
        autoComplete="new-password"
      />
      {needsInvite && (
        <Field label="Invite code" name="invite" autoComplete="off" />
      )}
      <SubmitButton pending={pending} idle="Create account" busy="Creating…" />
      <FormError error={state?.error} />
    </form>
  );
}

export function LinkIbkrForm() {
  const [state, action, pending] = useAuthForm(linkIbkr);

  return (
    <form action={action} className="flex flex-col gap-3">
      <Field
        label="Flex query id"
        name="queryId"
        mono
        inputMode="numeric"
        autoFocus
        autoComplete="off"
        placeholder="e.g. 1234567"
      />
      <Field
        label="Flex Web Service token"
        name="token"
        type="password"
        mono
        autoComplete="off"
      />
      <Field label="Label" hint="optional" name="label" autoComplete="off" />
      <SubmitButton pending={pending} idle="Link account" busy="Saving…" />
      <FormError error={state?.error} />
      <Link
        href="/portfolio"
        className="text-center text-xs text-neutral-500 hover:underline dark:text-neutral-400"
      >
        Skip for now
      </Link>
    </form>
  );
}
