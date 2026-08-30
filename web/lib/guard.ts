import "server-only";

import { redirect } from "next/navigation";

import { currentUser, type User } from "@/lib/auth";

/**
 * The authorisation boundary.
 *
 * Every page, route handler and server action goes through one of these. Page
 * redirects alone would be theatre: server actions are POST endpoints and route
 * handlers are GET endpoints, both reachable without ever loading a page, so
 * each has to check for itself.
 */

/** For pages. Sends signed-out visitors to sign in, and back afterwards. */
export async function requirePage(next: string): Promise<User> {
  const user = await currentUser();
  if (!user) redirect(`/login?next=${encodeURIComponent(next)}`);
  return user;
}

/**
 * For server actions. Throws rather than redirects — an action reached without
 * a session is not a navigation, it is a request that should not have been
 * made, and failing loudly beats bouncing it somewhere.
 */
export async function requireAction(): Promise<User> {
  const user = await currentUser();
  if (!user) throw new Error("Not signed in.");
  return user;
}

/** For route handlers. Returns null so the caller can send its own 401. */
export async function requireApi(): Promise<User | null> {
  return currentUser();
}
