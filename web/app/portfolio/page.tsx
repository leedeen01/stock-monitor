import Link from "next/link";
import { redirect } from "next/navigation";

import { isSignedIn } from "@/lib/auth";

export const dynamic = "force-dynamic";

/**
 * The private side of the app.
 *
 * Empty for now on purpose — this is where the IBKR-backed views land, and
 * having the gate in place first means those features are never briefly
 * public while they are being built.
 */
export default async function PortfolioPage() {
  if (!(await isSignedIn())) redirect("/login?next=/portfolio");

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-10 sm:px-6">
      <h1 className="text-2xl font-semibold tracking-tight">Portfolio</h1>
      <p className="mt-2 text-sm text-neutral-500 dark:text-neutral-400">
        Signed in. Nothing here yet — this is where the IBKR holdings, cost
        basis and position weighting will go.
      </p>

      <div className="mt-6 rounded-lg border border-dashed border-neutral-300 p-8 text-sm text-neutral-500 dark:border-neutral-700">
        <p className="font-medium text-neutral-700 dark:text-neutral-300">
          Next up
        </p>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>Daily holdings snapshot from the IBKR Flex Web Service</li>
          <li>Real cost basis, replacing the manually recorded add price</li>
          <li>Position-weighted valuation percentile across the book</li>
          <li>Group exposure in dollars, reusing the existing metric groups</li>
        </ul>
      </div>

      <Link
        href="/"
        className="mt-6 inline-block text-xs text-neutral-500 hover:underline dark:text-neutral-400"
      >
        ← Back to the watchlist
      </Link>
    </main>
  );
}
