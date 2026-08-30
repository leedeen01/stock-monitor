import Link from "next/link";

import { unlinkIbkr } from "@/app/auth-actions";
import { requirePage } from "@/lib/guard";
import { getLink } from "@/lib/ibkr";
import { Notice } from "@/components/ui";

export const dynamic = "force-dynamic";

/**
 * The per-user side of the app.
 *
 * The watchlist is shared across everyone; holdings are not. This page shows
 * only what belongs to the signed-in account.
 */
export default async function PortfolioPage() {
  const user = await requirePage("/portfolio");
  const link = getLink(user.id);

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-10 sm:px-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Portfolio</h1>
          <p className="mt-1 text-sm text-neutral-500 dark:text-neutral-400">
            {user.email}
          </p>
        </div>
        <Link
          href="/"
          className="text-xs text-neutral-500 hover:underline dark:text-neutral-400"
        >
          ← Watchlist
        </Link>
      </header>

      {!link.linked ? (
        <section className="mt-6 rounded-lg border border-dashed border-neutral-300 p-8 dark:border-neutral-700">
          <h2 className="text-sm font-medium">No brokerage account linked</h2>
          <p className="mt-1 max-w-prose text-sm text-neutral-500 dark:text-neutral-400">
            Linking IBKR brings in cost basis and position sizes, so the
            valuation history you already have can be weighted by what you
            actually own.
          </p>
          <Link
            href="/link-ibkr"
            className="mt-4 inline-block rounded-md bg-neutral-900 px-3 py-1.5 text-sm text-white dark:bg-neutral-100 dark:text-neutral-900"
          >
            Link IBKR
          </Link>
        </section>
      ) : (
        <section className="mt-6 rounded-lg border border-neutral-200 p-5 dark:border-neutral-800">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-medium">
                IBKR linked
                {link.accountLabel ? ` — ${link.accountLabel}` : ""}
              </h2>
              <p className="mt-1 font-mono text-xs text-neutral-500 dark:text-neutral-400">
                query {link.queryId}
              </p>
            </div>
            <div className="flex items-center gap-3">
              <Link
                href="/link-ibkr"
                className="text-xs text-neutral-500 hover:underline dark:text-neutral-400"
              >
                Update
              </Link>
              <form action={unlinkIbkr}>
                <button
                  type="submit"
                  className="text-xs text-rose-600 hover:underline dark:text-rose-400"
                >
                  Unlink
                </button>
              </form>
            </div>
          </div>

          {link.unreadable && (
            <div className="mt-3"><Notice tone="error">
              The stored token cannot be decrypted — usually because
              <code> ENCRYPTION_KEY</code> changed. Re-enter it to restore the
              link.
            </Notice></div>
          )}

          <p className="mt-3 text-xs text-neutral-500 dark:text-neutral-400">
            {link.lastSyncAt
              ? `Last synced ${link.lastSyncAt}`
              : "Not synced yet — the holdings importer is the next piece to build."}
          </p>
        </section>
      )}

      <section className="mt-6 rounded-lg border border-dashed border-neutral-300 p-6 text-sm text-neutral-500 dark:border-neutral-700">
        <p className="font-medium text-neutral-700 dark:text-neutral-300">
          Next up
        </p>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>Daily holdings snapshot from the Flex Web Service</li>
          <li>Real cost basis, replacing the manually recorded add price</li>
          <li>Position-weighted valuation percentile across the book</li>
          <li>Group exposure in dollars, reusing the existing metric groups</li>
        </ul>
      </section>
    </main>
  );
}
