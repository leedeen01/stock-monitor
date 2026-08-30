import Link from "next/link";

import { signOut } from "@/app/auth-actions";
import { isSignedIn } from "@/lib/auth";

/**
 * Sign-in state in the header.
 *
 * A server component, so the signed-in branch never reaches a signed-out
 * browser — the private links are absent from the HTML rather than hidden by
 * CSS.
 */
export async function AuthNav() {
  const signedIn = await isSignedIn();

  const linkClass =
    "rounded-md border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:border-neutral-700 dark:hover:bg-neutral-900";

  if (!signedIn) {
    return (
      <Link href="/login" className={linkClass}>
        Sign in
      </Link>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <Link href="/portfolio" className={linkClass}>
        Portfolio
      </Link>
      <form action={signOut}>
        <button
          type="submit"
          className="text-xs text-neutral-500 hover:underline dark:text-neutral-400"
        >
          Sign out
        </button>
      </form>
    </div>
  );
}
