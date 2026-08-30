import Link from "next/link";
import { redirect } from "next/navigation";

import { LoginForm } from "@/components/AuthForms";
import { authConfigured, isSignedIn, signupMode } from "@/lib/auth";
import { Notice } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function LoginPage(props: PageProps<"/login">) {
  const search = await props.searchParams;
  const raw = typeof search.next === "string" ? search.next : "/";
  // Same-origin only; an unchecked next param is an open redirect.
  const next = raw.startsWith("/") && !raw.startsWith("//") ? raw : "/";

  if (await isSignedIn()) redirect(next);

  const canSignUp = signupMode() !== "closed";

  return (
    <main className="mx-auto flex w-full max-w-sm flex-col gap-6 px-4 py-20">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Stock Monitor</h1>
        <p className="mt-1 text-sm text-neutral-500 dark:text-neutral-400">
          Sign in to continue.
        </p>
      </div>

      {authConfigured() ? (
        <LoginForm next={next} />
      ) : (
        <Notice tone="warn">
          Sign-in is not configured on this server. Set <code>AUTH_SECRET</code>{" "}
          in <code>.env</code> and restart.
        </Notice>
      )}

      {canSignUp && (
        <p className="text-xs text-neutral-500 dark:text-neutral-400">
          No account yet?{" "}
          <Link href="/signup" className="underline">
            Create one
          </Link>
          .
        </p>
      )}
    </main>
  );
}
