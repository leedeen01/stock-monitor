import Link from "next/link";

import { formatBig, formatMetric, formatPercentChange, percentileTone } from "@/lib/format";
import type { Holding } from "@/lib/queries";

/**
 * What you own, with the valuation context the watchlist already computes.
 *
 * Ordered by position value rather than alphabetically: on a portfolio the
 * thing you own most of is the thing worth looking at first.
 *
 * One column layout for every row, like the watchlist grid — separate tables
 * size their columns from their own content and drift out of line.
 */
export function HoldingsTable({ holdings }: { holdings: Holding[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-neutral-200 dark:border-neutral-800">
      <table className="w-full min-w-[760px] table-fixed text-sm">
        <colgroup>
          <col style={{ width: "24%" }} />
          <col style={{ width: "10%" }} />
          <col style={{ width: "14%" }} />
          <col style={{ width: "13%" }} />
          <col style={{ width: "13%" }} />
          <col style={{ width: "13%" }} />
          <col style={{ width: "13%" }} />
        </colgroup>
        <thead>
          <tr className="border-b border-neutral-200 text-xs text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
            <th className="px-3 py-2 text-left font-medium">Holding</th>
            <th className="px-3 py-2 text-right font-medium">Weight</th>
            <th className="px-3 py-2 text-right font-medium">Value</th>
            <th className="px-3 py-2 text-right font-medium">Since bought</th>
            <th className="px-3 py-2 text-right font-medium">Multiple</th>
            <th className="px-3 py-2 text-right font-medium">vs own history</th>
            <th className="px-3 py-2 text-right font-medium">Rev YoY</th>
          </tr>
        </thead>
        <tbody>
          {holdings.map((h) => (
            <tr
              key={h.ticker}
              className="border-t border-neutral-100 hover:bg-neutral-50 dark:border-neutral-900 dark:hover:bg-neutral-900/50"
            >
              <td className="px-3 py-2.5">
                {h.onWatchlist ? (
                  <Link href={`/stock/${h.ticker}`} className="font-medium hover:underline">
                    {h.ticker}
                  </Link>
                ) : (
                  <span className="font-medium">{h.ticker}</span>
                )}
                <div className="truncate text-xs text-neutral-500">
                  {h.name ?? (
                    // Held but never ingested, so there is no valuation history
                    // behind it. Saying so beats a row of blanks.
                    <span className="text-amber-600 dark:text-amber-500">
                      not on your watchlist
                    </span>
                  )}
                </div>
              </td>

              <td className="px-3 py-2.5 text-right font-mono tabular-nums">
                {h.percentOfNav !== null ? `${h.percentOfNav.toFixed(1)}%` : "—"}
              </td>

              <td className="px-3 py-2.5 text-right font-mono tabular-nums">
                {h.positionValue !== null ? formatBig(h.positionValue) : "—"}
                <div className="text-[10px] text-neutral-400">
                  {h.quantity !== null ? `${h.quantity.toLocaleString()} sh` : ""}
                </div>
              </td>

              <td
                className={`px-3 py-2.5 text-right font-mono tabular-nums ${
                  h.returnPct === null
                    ? ""
                    : h.returnPct >= 0
                      ? "text-emerald-600 dark:text-emerald-400"
                      : "text-rose-600 dark:text-rose-400"
                }`}
              >
                {h.returnPct === null ? "—" : formatPercentChange(h.returnPct * 100)}
                <div className="text-[10px] text-neutral-400">
                  {h.costBasisPrice !== null ? `@ ${h.costBasisPrice.toFixed(2)}` : ""}
                </div>
              </td>

              <td className="px-3 py-2.5 text-right font-mono tabular-nums">
                {formatMetric(h.multiple, "multiple")}
                <div className="truncate font-sans text-[10px] text-neutral-400">
                  {h.multipleLabel ?? ""}
                </div>
              </td>

              <td
                className={`px-3 py-2.5 text-right font-mono tabular-nums ${
                  h.percentile !== null ? percentileTone(h.percentile) : "text-neutral-300 dark:text-neutral-700"
                }`}
              >
                {h.percentile !== null ? Math.round(h.percentile) : "—"}
              </td>

              <td
                className={`px-3 py-2.5 text-right font-mono tabular-nums ${
                  h.revenueGrowth === null
                    ? ""
                    : h.revenueGrowth >= 0
                      ? "text-emerald-600 dark:text-emerald-400"
                      : "text-rose-600 dark:text-rose-400"
                }`}
              >
                {formatMetric(h.revenueGrowth, "percent")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
