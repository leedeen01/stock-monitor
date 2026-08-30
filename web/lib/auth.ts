import "server-only";

import { createHmac, timingSafeEqual } from "node:crypto";
import { cookies, headers } from "next/headers";

/**
 * Single-user session auth.
 *
 * There is one account — yours — so there is no user table, no registration
 * and no password reset. A session is a signed expiry stamp in an HttpOnly
 * cookie; nothing is stored server-side, so restarts don't sign you out.
 *
 * Fails closed: if AUTH_SECRET or AUTH_PASSWORD is missing, nobody is signed in
 * and the gated pages stay shut. A misconfigured deploy hides things rather
 * than exposing them.
 */

const COOKIE = "stock-monitor-session";
const MAX_AGE_SECONDS = 60 * 60 * 24 * 30; // 30 days

function secret(): string | null {
  return process.env.AUTH_SECRET || null;
}

function expectedPassword(): string | null {
  return process.env.AUTH_PASSWORD || null;
}

/** Whether login is usable at all. Surfaced so the UI can say why, rather than
 *  silently rejecting a correct password. */
export function authConfigured(): boolean {
  return Boolean(secret() && expectedPassword());
}

function sign(payload: string, key: string): string {
  return createHmac("sha256", key).update(payload).digest("hex");
}

/** Constant time, and length-safe — timingSafeEqual throws on a length
 *  mismatch, which would itself leak the length. */
function safeEqual(a: string, b: string): boolean {
  const left = Buffer.from(a, "utf8");
  const right = Buffer.from(b, "utf8");
  if (left.length !== right.length) return false;
  return timingSafeEqual(left, right);
}

export function passwordMatches(input: string): boolean {
  const expected = expectedPassword();
  if (!expected) return false;
  return safeEqual(input, expected);
}

export async function createSession(): Promise<void> {
  const key = secret();
  if (!key) throw new Error("AUTH_SECRET is not set");

  const expiresAt = String(Date.now() + MAX_AGE_SECONDS * 1000);
  const store = await cookies();
  store.set(COOKIE, `${expiresAt}.${sign(expiresAt, key)}`, {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    // The NAS is reached over HTTPS through the tunnel; localhost dev is not.
    secure: process.env.NODE_ENV === "production",
    maxAge: MAX_AGE_SECONDS,
  });
}

export async function destroySession(): Promise<void> {
  (await cookies()).delete(COOKIE);
}

export async function isSignedIn(): Promise<boolean> {
  const key = secret();
  if (!key || !expectedPassword()) return false;

  const raw = (await cookies()).get(COOKIE)?.value;
  if (!raw) return false;

  const [expiresAt, signature] = raw.split(".");
  if (!expiresAt || !signature) return false;
  if (!safeEqual(signature, sign(expiresAt, key))) return false;

  return Number(expiresAt) > Date.now();
}

/**
 * Brute force throttle.
 *
 * One password on a publicly reachable site is exactly the thing worth
 * rate-limiting. In-memory is enough here: the app is a single Node process,
 * and a restart clearing the counters is not a weakness worth a table.
 */
const WINDOW_MS = 10 * 60 * 1000;
const MAX_ATTEMPTS = 8;

const attempts = new Map<string, { count: number; resetAt: number }>();

async function clientKey(): Promise<string> {
  const h = await headers();
  return (
    h.get("cf-connecting-ip") ??
    h.get("x-forwarded-for")?.split(",")[0]?.trim() ??
    "unknown"
  );
}

export async function throttled(): Promise<boolean> {
  const key = await clientKey();
  const now = Date.now();
  const entry = attempts.get(key);
  if (!entry || entry.resetAt < now) return false;
  return entry.count >= MAX_ATTEMPTS;
}

export async function recordFailure(): Promise<void> {
  const key = await clientKey();
  const now = Date.now();
  const entry = attempts.get(key);
  if (!entry || entry.resetAt < now) {
    attempts.set(key, { count: 1, resetAt: now + WINDOW_MS });
    return;
  }
  entry.count += 1;
}

export async function clearFailures(): Promise<void> {
  attempts.delete(await clientKey());
}
