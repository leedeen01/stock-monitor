"use server";

import { redirect } from "next/navigation";

import {
  authConfigured,
  clearFailures,
  createSession,
  destroySession,
  passwordMatches,
  recordFailure,
  throttled,
} from "@/lib/auth";

export type SignInResult = { error: string } | null;

export async function signIn(
  _prev: SignInResult,
  formData: FormData,
): Promise<SignInResult> {
  if (!authConfigured()) {
    return {
      error:
        "Login is not configured on this server — AUTH_PASSWORD and AUTH_SECRET are unset.",
    };
  }

  if (await throttled()) {
    return { error: "Too many attempts. Try again in a few minutes." };
  }

  const password = String(formData.get("password") ?? "");
  if (!passwordMatches(password)) {
    await recordFailure();
    // Deliberately vague: the only thing a wrong guess should reveal is that it
    // was wrong.
    return { error: "Incorrect password." };
  }

  await clearFailures();
  await createSession();

  const next = String(formData.get("next") ?? "/");
  // Only same-origin paths, or an open redirect falls out of the next param.
  redirect(next.startsWith("/") && !next.startsWith("//") ? next : "/");
}

export async function signOut(): Promise<void> {
  await destroySession();
  redirect("/");
}
