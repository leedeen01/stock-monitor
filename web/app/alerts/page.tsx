import Link from "next/link";

import { RuleManager } from "@/components/RuleManager";
import { requirePage } from "@/lib/guard";
import { getAlertRules, getGroups, getOpenAlerts, getWatchlist } from "@/lib/queries";

export const dynamic = "force-dynamic";

export default async function AlertsPage() {
  const user = await requirePage("/alerts");

  const rules = getAlertRules(user.id);
  const groups = getGroups(user.id);
  const tickers = getWatchlist(user.id);
  const open = getOpenAlerts(user.id, 100);

  return (
    <main className="mx-auto w-full max-w-4xl px-4 py-8 sm:px-6">
      <Link
        href="/"
        className="text-sm text-neutral-500 hover:underline dark:text-neutral-400"
      >
        ← Watchlist
      </Link>

      <header className="mt-4 mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Alerts</h1>
        <p className="mt-1 text-sm text-neutral-500 dark:text-neutral-400">
          Evaluated after each daily refresh. {open.length} open.
        </p>
      </header>

      <RuleManager rules={rules} groups={groups} tickers={tickers} />
    </main>
  );
}
