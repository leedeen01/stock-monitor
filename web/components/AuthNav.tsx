import Link from "next/link";

import { signOut } from "@/app/auth-actions";
import { currentUser } from "@/lib/auth";

/**
 * Account controls in the header.
 *
 * A server component, so a signed-out browser never receives the private links
 * at all rather than having them hidden with CSS.
 */
export async function AuthNav() {
  const user = await currentUser();
  if (!user) return null;

  return (
    <div className="flex items-center gap-2">
      <Link
        href="/portfolio"
        className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:border-neutral-700 dark:hover:bg-neutral-900"
      >
        Portfolio
      </Link>
      <form action={signOut} className="flex items-center gap-2">
        <span
          className="max-w-[10rem] truncate text-xs text-neutral-400 dark:text-neutral-500"
          title={user.email}
        >
          {user.email}
        </span>
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
