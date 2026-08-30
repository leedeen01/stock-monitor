import Link from "next/link";
import { redirect } from "next/navigation";

import { LoginForm } from "@/components/LoginForm";
import { authConfigured, isSignedIn } from "@/lib/auth";

export const dynamic = "force-dynamic";

export default async function LoginPage(props: PageProps<"/login">) {
  const search = await props.searchParams;
  const raw = typeof search.next === "string" ? search.next : "/";
  // Same-origin only; an unchecked next param is an open redirect.
  const next = raw.startsWith("/") && !raw.startsWith("//") ? raw : "/";

  if (await isSignedIn()) redirect(next);

  const configured = authConfigured();

  return (
    <main className="mx-auto flex w-full max-w-sm flex-col gap-6 px-4 py-20">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Sign in</h1>
        <p className="mt-1 text-sm text-neutral-500 dark:text-neutral-400">
          The watchlist is public. Signing in unlocks the private views.
        </p>
      </div>

      {configured ? (
        <LoginForm next={next} configured={configured} />
      ) : (
        <p className="rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
          Login is not configured on this server. Set <code>AUTH_PASSWORD</code>{" "}
          and <code>AUTH_SECRET</code> in <code>.env</code> and restart.
        </p>
      )}

      <Link
        href="/"
        className="text-xs text-neutral-500 hover:underline dark:text-neutral-400"
      >
        ← Back to the watchlist
      </Link>
    </main>
  );
}
