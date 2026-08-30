import Link from "next/link";
import { redirect } from "next/navigation";

import { SignupForm } from "@/components/AuthForms";
import { authConfigured, isSignedIn, signupMode, userCount } from "@/lib/auth";
import { Notice } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function SignupPage() {
  if (await isSignedIn()) redirect("/");

  const mode = signupMode();
  const first = userCount() === 0;

  return (
    <main className="mx-auto flex w-full max-w-sm flex-col gap-6 px-4 py-20">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Create an account</h1>
        <p className="mt-1 text-sm text-neutral-500 dark:text-neutral-400">
          {first
            ? "This is the first account, so it will own this instance."
            : "You will be asked to link a brokerage account next — that step is optional."}
        </p>
      </div>

      {!authConfigured() ? (
        <Notice tone="warn">
          Sign-up is not configured on this server. Set <code>AUTH_SECRET</code>{" "}
          in <code>.env</code> and restart.
        </Notice>
      ) : mode === "closed" ? (
        <Notice tone="warn">
          Registration is closed on this server. Set{" "}
          <code>SIGNUP_INVITE_CODE</code> to allow invited people in, or{" "}
          <code>SIGNUP_OPEN=true</code> to let anyone register.
        </Notice>
      ) : (
        <SignupForm needsInvite={mode === "invite"} />
      )}

      <p className="text-xs text-neutral-500 dark:text-neutral-400">
        Already have an account?{" "}
        <Link href="/login" className="underline">
          Sign in
        </Link>
        .
      </p>
    </main>
  );
}
