"use server";

import { redirect } from "next/navigation";

import {
  authConfigured,
  clearFailures,
  createSession,
  currentUser,
  destroySession,
  recordFailure,
  registerUser,
  signupMode,
  throttled,
  verifyCredentials,
} from "@/lib/auth";
import { removeLink, saveLink } from "@/lib/ibkr";
import { encryptionConfigured } from "@/lib/secrets";

export type FormResult = { error: string } | null;

/** Same-origin paths only, or the next param becomes an open redirect. */
function safeNext(raw: unknown, fallback = "/"): string {
  const value = typeof raw === "string" ? raw : "";
  return value.startsWith("/") && !value.startsWith("//") ? value : fallback;
}

export async function signIn(
  _prev: FormResult,
  formData: FormData,
): Promise<FormResult> {
  if (!authConfigured()) {
    return { error: "Sign-in is not configured on this server — AUTH_SECRET is unset." };
  }
  if (await throttled("signin")) {
    return { error: "Too many attempts. Try again in a few minutes." };
  }

  const email = String(formData.get("email") ?? "");
  const password = String(formData.get("password") ?? "");

  const user = await verifyCredentials(email, password);
  if (!user) {
    await recordFailure("signin");
    // One message for both wrong-email and wrong-password: anything more
    // specific tells a stranger which addresses have accounts.
    return { error: "Incorrect email or password." };
  }

  await clearFailures("signin");
  await createSession(user.id);
  redirect(safeNext(formData.get("next")));
}

export async function signUp(
  _prev: FormResult,
  formData: FormData,
): Promise<FormResult> {
  if (!authConfigured()) {
    return { error: "Sign-up is not configured on this server — AUTH_SECRET is unset." };
  }
  if (signupMode() === "closed") {
    return { error: "Registration is closed on this server." };
  }
  if (await throttled("signup")) {
    return { error: "Too many attempts. Try again in a few minutes." };
  }

  const email = String(formData.get("email") ?? "");
  const password = String(formData.get("password") ?? "");
  const confirm = String(formData.get("confirm") ?? "");
  const invite = String(formData.get("invite") ?? "");

  if (password !== confirm) {
    return { error: "The two passwords do not match." };
  }

  const result = await registerUser(email, password, invite);
  if (!result.ok) {
    await recordFailure("signup");
    return { error: result.error };
  }

  await clearFailures("signup");
  await createSession(result.user.id);

  // Straight into linking a brokerage account — that is the reason to have an
  // account here at all. The page itself allows skipping.
  redirect("/link-ibkr?welcome=1");
}

export async function signOut(): Promise<void> {
  await destroySession();
  redirect("/");
}

export async function linkIbkr(
  _prev: FormResult,
  formData: FormData,
): Promise<FormResult> {
  const user = await currentUser();
  if (!user) redirect("/login?next=/link-ibkr");

  if (!encryptionConfigured()) {
    return {
      error:
        "Credential storage is not configured on this server — ENCRYPTION_KEY is unset.",
    };
  }

  const queryId = String(formData.get("queryId") ?? "").trim();
  const token = String(formData.get("token") ?? "").trim();
  const label = String(formData.get("label") ?? "").trim();

  if (!/^\d{3,15}$/.test(queryId)) {
    return { error: "The Flex query id is the numeric id shown next to your query." };
  }
  if (token.length < 12) {
    return { error: "That token looks too short — copy the whole value from IBKR." };
  }

  saveLink(user.id, queryId, token, label || null);
  redirect("/portfolio?linked=1");
}

export async function unlinkIbkr(): Promise<void> {
  const user = await currentUser();
  if (!user) redirect("/login?next=/portfolio");

  removeLink(user.id);
  redirect("/portfolio?unlinked=1");
}
