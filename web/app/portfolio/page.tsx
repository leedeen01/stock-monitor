import Link from "next/link";

import { unlinkIbkr } from "@/app/auth-actions";
import { HoldingsTable } from "@/components/HoldingsTable";
import { SyncIbkrButton } from "@/components/SyncIbkrButton";
import { Notice } from "@/components/ui";
import { requirePage } from "@/lib/guard";
import { getLink } from "@/lib/ibkr";
import { formatBig, percentileTone } from "@/lib/format";
import { getHoldings, getPortfolioSummary } from "@/lib/queries";

export const dynamic = "force-dynamic";

/**
 * The per-user side of the app.
 *
 * Everything here belongs to the signed-in account: the brokerage link, the
 * holdings and the weighted valuation reading. The shared part of the app is
 * the market data behind them.
 */
export default async function PortfolioPage() {
  const user = await requirePage("/portfolio");
  const link = getLink(user.id);
  const holdings = getHoldings(user.id);
  const summary = getPortfolioSummary(user.id);

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
              <SyncIbkrButton />
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

      {holdings.length > 0 && summary ? (
        <>
          <section className="mt-8 grid gap-3 sm:grid-cols-4">
            <Stat label="Portfolio" value={summary.total ? formatBig(summary.total) : formatBig(summary.stockValue)} />
            <Stat
              label="Unrealised"
              value={formatBig(summary.unrealizedPnl)}
              tone={summary.unrealizedPnl >= 0 ? "good" : "bad"}
            />
            <Stat
              label="Cash"
              value={summary.cashPct !== null ? `${(summary.cashPct * 100).toFixed(1)}%` : "—"}
            />
            <Stat
              label="Weighted percentile"
              value={
                summary.weightedPercentile !== null
                  ? String(Math.round(summary.weightedPercentile))
                  : "—"
              }
              tone={
                summary.weightedPercentile !== null
                  ? undefined
                  : undefined
              }
              className={
                summary.weightedPercentile !== null
                  ? percentileTone(summary.weightedPercentile)
                  : undefined
              }
              hint={
                summary.weightedCoverage < 0.999
                  ? `${Math.round(summary.weightedCoverage * 100)}% of the book has enough history`
                  : "value-weighted across the book"
              }
            />
          </section>

          {summary.currencies.length > 1 && (
            <div className="mt-3">
              <Notice tone="warn">
                Holdings span {summary.currencies.join(", ")}. Nothing is
                converted, so the totals above cover{" "}
                <strong>{summary.baseCurrency}</strong> positions only —
                a figure mixing currencies would be confidently wrong.
              </Notice>
            </div>
          )}

          <p className="mt-3 text-xs text-neutral-500 dark:text-neutral-400">
            {summary.positions} position{summary.positions === 1 ? "" : "s"} as of{" "}
            {summary.reportDate} — IBKR data refreshes overnight, so this lags the
            market by a day by design.
          </p>

          <div className="mt-4">
            <HoldingsTable holdings={holdings} />
          </div>
        </>
      ) : link.linked ? (
        <section className="mt-6 rounded-lg border border-dashed border-neutral-300 p-8 text-sm text-neutral-500 dark:border-neutral-700">
          Linked, but nothing imported yet. Hit <strong>Sync now</strong> above, or
          wait for the 06:00 job.
        </section>
      ) : null}

    </main>
  );
}

/** One figure with its label. Four of these read faster than a paragraph. */
function Stat({
  label,
  value,
  hint,
  tone,
  className,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "good" | "bad";
  className?: string;
}) {
  const toneClass =
    tone === "good"
      ? "text-emerald-600 dark:text-emerald-400"
      : tone === "bad"
        ? "text-rose-600 dark:text-rose-400"
        : "";
  return (
    <div className="rounded-lg border border-neutral-200 px-4 py-3 dark:border-neutral-800">
      <div className="text-xs text-neutral-500 dark:text-neutral-400">{label}</div>
      <div
        className={`mt-1 font-mono text-lg tabular-nums ${className ?? toneClass}`}
      >
        {value}
      </div>
      {hint && (
        <div className="mt-0.5 text-[10px] leading-tight text-neutral-400">{hint}</div>
      )}
    </div>
  );
}
