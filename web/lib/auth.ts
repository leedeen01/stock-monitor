import "server-only";

import { createHmac, randomBytes, scrypt, timingSafeEqual } from "node:crypto";
import { promisify } from "node:util";
import { cookies, headers } from "next/headers";

import { spawn } from "node:child_process";

import { db } from "@/lib/db";
import { INGEST_DIR, PYTHON } from "@/lib/paths";

/**
 * Accounts and sessions.
 *
 * The public watchlist needs none of this. It gates the private area, which is
 * where each user's holdings and cost basis live — so registration is
 * invite-gated by default and everything here fails closed. A misconfigured
 * deploy should hide the private side, never open it.
 *
 * A session is a signed `userId.expiry` stamp in an HttpOnly cookie. Nothing is
 * stored server-side, so restarts sign nobody out, and rotating AUTH_SECRET
 * revokes every session at once.
 */

const COOKIE = "stock-monitor-session";
const MAX_AGE_SECONDS = 60 * 60 * 24 * 30; // 30 days
const KEY_LENGTH = 64;

const scryptAsync = promisify(scrypt) as (
  password: string,
  salt: string,
  keylen: number,
) => Promise<Buffer>;

export type User = {
  id: number;
  email: string;
  role: string;
};

// --- configuration ----------------------------------------------------------

function secret(): string | null {
  return process.env.AUTH_SECRET || null;
}

export function authConfigured(): boolean {
  return Boolean(secret());
}

/**
 * Registration is closed unless explicitly opened.
 *
 *   invite code set   anyone holding the code may register
 *   SIGNUP_OPEN=true  anyone at all may register
 *   neither           closed
 *
 * Defaulting to closed matters more here than on a typical app: the private
 * area exposes brokerage positions, so an open form on a public hostname is a
 * different kind of mistake.
 */
export function signupMode(): "invite" | "open" | "closed" {
  if (process.env.SIGNUP_OPEN === "true") return "open";
  if (process.env.SIGNUP_INVITE_CODE) return "invite";
  return "closed";
}

// --- primitives -------------------------------------------------------------

function sign(payload: string, key: string): string {
  return createHmac("sha256", key).update(payload).digest("hex");
}

/**
 * Length-checked first: timingSafeEqual throws on a length mismatch, and that
 * throw would itself leak the length.
 */
function safeEqual(a: string, b: string): boolean {
  const left = Buffer.from(a, "utf8");
  const right = Buffer.from(b, "utf8");
  if (left.length !== right.length) return false;
  return timingSafeEqual(left, right);
}

function inviteCodeMatches(input: string): boolean {
  const expected = process.env.SIGNUP_INVITE_CODE ?? "";
  return expected.length > 0 && safeEqual(input, expected);
}

export async function hashPassword(password: string): Promise<string> {
  const salt = randomBytes(16).toString("hex");
  const key = await scryptAsync(password, salt, KEY_LENGTH);
  return ["scrypt", salt, key.toString("hex")].join("$");
}

async function passwordMatches(
  password: string,
  stored: string,
): Promise<boolean> {
  const [scheme, salt, key] = stored.split("$");
  if (scheme !== "scrypt" || !salt || !key) return false;

  const derived = await scryptAsync(password, salt, KEY_LENGTH);
  const expected = Buffer.from(key, "hex");
  if (derived.length !== expected.length) return false;
  return timingSafeEqual(derived, expected);
}

// --- accounts ---------------------------------------------------------------

type UserRow = {
  id: number;
  email: string;
  password_hash: string;
  role: string;
};

export function userCount(): number {
  const row = db().prepare("SELECT COUNT(*) AS c FROM users").get() as {
    c: number;
  };
  return row.c;
}

export async function registerUser(
  email: string,
  password: string,
  inviteCode: string,
): Promise<{ ok: true; user: User } | { ok: false; error: string }> {
  const mode = signupMode();
  if (mode === "closed") {
    return { ok: false, error: "Registration is closed on this server." };
  }
  if (mode === "invite" && !inviteCodeMatches(inviteCode)) {
    return { ok: false, error: "That invite code is not valid." };
  }

  const trimmed = email.trim();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed)) {
    return { ok: false, error: "Enter a valid email address." };
  }
  if (password.length < 10) {
    return { ok: false, error: "Use a password of at least 10 characters." };
  }

  const conn = db();
  const lower = trimmed.toLowerCase();

  if (conn.prepare("SELECT id FROM users WHERE email_lower = ?").get(lower)) {
    return { ok: false, error: "That email is already registered." };
  }

  // The first account owns the instance; everyone after is a member.
  const role = userCount() === 0 ? "owner" : "member";
  const hash = await hashPassword(password);

  const info = conn
    .prepare(
      "INSERT INTO users (email, email_lower, password_hash, role, created_at) " +
        "VALUES (?, ?, ?, ?, ?)",
    )
    .run(trimmed, lower, hash, role, new Date().toISOString());

  const id = Number(info.lastInsertRowid);
  provisionAccount(id, role === "owner");

  return { ok: true, user: { id, email: trimmed, role } };
}

/**
 * Give a new account something to look at.
 *
 * The owner adopts whatever has no user yet — an install predating multi-user
 * has a watchlist, groups and alerts sitting ownerless, and the person who
 * built it should find them rather than an empty page.
 *
 * Everyone else gets the default metric profiles and starter alert rules
 * seeded fresh. That runs detached because it is not worth making
 * registration wait on, and a failure leaves an account without its
 * defaults rather than without an account.
 */
function provisionAccount(userId: number, isOwner: boolean): void {
  if (isOwner) {
    const conn = db();
    const adopt = conn.transaction(() => {
      for (const table of [
        "watchlist",
        "metric_groups",
        "stock_groups",
        "alert_rules",
        "alert_events",
      ]) {
        conn
          .prepare(`UPDATE ${table} SET user_id = ? WHERE user_id IS NULL`)
          .run(userId);
      }
    });
    adopt();

    // Adoption may have found nothing — a genuinely fresh install still needs
    // its groups.
    const groups = conn
      .prepare("SELECT COUNT(*) AS c FROM metric_groups WHERE user_id = ?")
      .get(userId) as { c: number };
    if (groups.c > 0) return;
  }

  const child = spawn(PYTHON, ["provision.py", "--user-id", String(userId)], {
    cwd: INGEST_DIR,
    detached: true,
    stdio: "ignore",
    windowsHide: true,
  });
  child.unref();
}

export async function verifyCredentials(
  email: string,
  password: string,
): Promise<User | null> {
  const row = db()
    .prepare(
      "SELECT id, email, password_hash, role FROM users WHERE email_lower = ?",
    )
    .get(email.trim().toLowerCase()) as UserRow | undefined;

  // Hash anyway for an unknown address, so a missing account and a wrong
  // password take the same time. Otherwise the response time enumerates users.
  if (!row) {
    await hashPassword(password);
    return null;
  }

  if (!(await passwordMatches(password, row.password_hash))) return null;
  return { id: row.id, email: row.email, role: row.role };
}

// --- sessions ---------------------------------------------------------------

export async function createSession(userId: number): Promise<void> {
  const key = secret();
  if (!key) throw new Error("AUTH_SECRET is not set");

  const expiresAt = Date.now() + MAX_AGE_SECONDS * 1000;
  const payload = [userId, expiresAt].join(".");

  (await cookies()).set(COOKIE, [payload, sign(payload, key)].join("."), {
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

export async function currentUser(): Promise<User | null> {
  const key = secret();
  if (!key) return null;

  const raw = (await cookies()).get(COOKIE)?.value;
  if (!raw) return null;

  const [userId, expiresAt, signature] = raw.split(".");
  if (!userId || !expiresAt || !signature) return null;
  if (!safeEqual(signature, sign([userId, expiresAt].join("."), key))) {
    return null;
  }
  if (Number(expiresAt) <= Date.now()) return null;

  const row = db()
    .prepare("SELECT id, email, password_hash, role FROM users WHERE id = ?")
    .get(Number(userId)) as UserRow | undefined;
  // A deleted account invalidates its outstanding sessions for free.
  if (!row) return null;

  return { id: row.id, email: row.email, role: row.role };
}

export async function isSignedIn(): Promise<boolean> {
  return (await currentUser()) !== null;
}

// --- brute force throttle ---------------------------------------------------

const WINDOW_MS = 10 * 60 * 1000;
const MAX_ATTEMPTS = 8;

const attempts = new Map<string, { count: number; resetAt: number }>();

async function clientKey(scope: string): Promise<string> {
  const h = await headers();
  const ip =
    h.get("cf-connecting-ip") ??
    h.get("x-forwarded-for")?.split(",")[0]?.trim() ??
    "unknown";
  return [scope, ip].join(":");
}

export async function throttled(scope: string): Promise<boolean> {
  const entry = attempts.get(await clientKey(scope));
  if (!entry || entry.resetAt < Date.now()) return false;
  return entry.count >= MAX_ATTEMPTS;
}

export async function recordFailure(scope: string): Promise<void> {
  const key = await clientKey(scope);
  const now = Date.now();
  const entry = attempts.get(key);
  if (!entry || entry.resetAt < now) {
    attempts.set(key, { count: 1, resetAt: now + WINDOW_MS });
    return;
  }
  entry.count += 1;
}

export async function clearFailures(scope: string): Promise<void> {
  attempts.delete(await clientKey(scope));
}
