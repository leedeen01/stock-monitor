"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { RemoveStockButton } from "@/components/RemoveStockButton";
import {
  changeTone,
  formatMetric,
  formatPercentChange,
  formatPrice,
  percentileTone,
} from "@/lib/format";
import { metric } from "@/lib/metrics";
import type { WatchlistRow } from "@/lib/queries";

type SortKey = "ticker" | "changePct" | "multiple" | "percentile" | "revenueGrowth" | "flags";

type Direction = "asc" | "desc";

type Column = {
  key: SortKey;
  label: string;
  align: "left" | "right";
  /** Which direction to apply on the first click of this column. */
  initial: Direction;
  /** Fixed share of the table. See the table-layout note on WatchlistGrid. */
  width: string;
  value: (row: WatchlistRow) => number | string | null;
};

/**
 * Deliberately short. This page is triage — "what deserves attention today" —
 * and every extra column costs a scan. Two were dropped:
 *
 *   Market cap  — static, already known, and not an input to valuation.
 *   Price       — folded under the change, because absolute price is
 *                 orientation rather than a decision input, and sorting by it
 *                 across different companies means nothing.
 *
 * What's left is: what you're paying (multiple), whether that's high for this
 * stock (percentile), whether growth earns it (Rev YoY), and what changed
 * (Chg, flags).
 */
const COLUMNS: Column[] = [
  { key: "ticker", label: "Ticker", align: "left", initial: "asc", width: "24%",
    value: (r) => r.ticker },
  // Sorts on the change, not the price — a $300 stock isn't "more" than a $100 one.
  { key: "changePct", label: "Price", align: "right", initial: "desc", width: "11%",
    value: (r) => r.changePct },
  // Different groups lead with different multiples, so this only compares
  // like with like inside a group — which is how it reads.
  { key: "multiple", label: "Multiple", align: "right", initial: "asc", width: "14%",
    value: (r) => r.primary?.value ?? null },
  { key: "percentile", label: "vs own history", align: "right", initial: "asc", width: "15%",
    value: (r) => (r.primary?.sufficient ? r.primary.percentile : null) },
  { key: "revenueGrowth", label: "Rev YoY", align: "right", initial: "desc", width: "11%",
    value: (r) => r.revenueGrowth },
  { key: "flags", label: "Flags", align: "left", initial: "desc", width: "21%",
    value: (r) => r.flags.length },
];

const ACTION_WIDTH = "4%";
const TOTAL_COLUMNS = COLUMNS.length + 1;

/**
 * One table for every group, not one table per group.
 *
 * Separate tables size their columns independently from their own content, so
 * "ADVANCED MICRO DEVICES INC" in one group pushed every later column out of
 * line with the group above it. Rendering a single table with `table-fixed` and
 * an explicit colgroup makes alignment structural rather than coincidental —
 * widths come from the column definitions, not from whichever ticker happens to
 * have the longest name. It also drops three duplicate header rows.
 *
 * Groups become <tbody> sections with a label row, which keeps the visual
 * grouping while guaranteeing the columns line up.
 *
 * Sorting is client-side: everything is already on the page, so a round-trip
 * per click would only add latency. Sort state is shared across all groups, so
 * a chosen order means the same thing everywhere.
 */
export function WatchlistGrid({ rows }: { rows: WatchlistRow[] }) {
  // Cheapest-against-its-own-history first — the opinionated default that puts
  // the interesting rows where the eye lands.
  const [sortKey, setSortKey] = useState<SortKey>("percentile");
  const [direction, setDirection] = useState<Direction>("asc");

  const byGroup = useMemo(() => {
    const column = COLUMNS.find((c) => c.key === sortKey)!;
    const factor = direction === "asc" ? 1 : -1;

    const compare = (a: WatchlistRow, b: WatchlistRow) => {
      const av = column.value(a);
      const bv = column.value(b);
      // Missing values sink to the bottom in both directions. Letting them
      // lead a descending sort would bury every row that has data.
      if (av === null && bv === null) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      if (typeof av === "string" || typeof bv === "string") {
        return String(av).localeCompare(String(bv)) * factor;
      }
      return (av - bv) * factor;
    };

    const map = new Map<string, WatchlistRow[]>();
    for (const row of rows) {
      const key = row.group?.name ?? "Ungrouped";
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(row);
    }
    for (const list of map.values()) list.sort(compare);
    return map;
  }, [rows, sortKey, direction]);

  const toggle = (column: Column) => {
    if (column.key === sortKey) {
      setDirection((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(column.key);
      setDirection(column.initial);
    }
  };

  return (
    <div className="overflow-x-auto rounded-lg border border-neutral-200 dark:border-neutral-800">
      <table className="w-full min-w-[680px] table-fixed text-sm">
        <colgroup>
          {COLUMNS.map((c) => (
            <col key={c.key} style={{ width: c.width }} />
          ))}
          <col style={{ width: ACTION_WIDTH }} />
        </colgroup>

        <thead>
          <tr className="border-b border-neutral-200 text-xs text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
            {COLUMNS.map((column) => {
              const active = column.key === sortKey;
              return (
                <th
                  key={column.key}
                  aria-sort={
                    active
                      ? direction === "asc"
                        ? "ascending"
                        : "descending"
                      : "none"
                  }
                  className={`px-3 py-2 font-medium ${
                    column.align === "left" ? "text-left" : "text-right"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => toggle(column)}
                    className={`inline-flex max-w-full items-center gap-1 truncate hover:text-neutral-900 dark:hover:text-neutral-100 ${
                      active ? "text-neutral-900 dark:text-neutral-100" : ""
                    }`}
                  >
                    {column.label}
                    <span
                      className={active ? "opacity-100" : "opacity-0"}
                      aria-hidden="true"
                    >
                      {direction === "asc" ? "▲" : "▼"}
                    </span>
                  </button>
                </th>
              );
            })}
            <th />
          </tr>
        </thead>

        {[...byGroup.entries()].map(([groupName, groupRows], groupIndex) => (
          <tbody key={groupName}>
            <tr
              className={
                groupIndex > 0
                  ? "border-t border-neutral-200 dark:border-neutral-800"
                  : ""
              }
            >
              <th
                scope="colgroup"
                colSpan={TOTAL_COLUMNS}
                className="bg-neutral-50 px-3 py-1.5 text-left text-xs font-medium text-neutral-700 dark:bg-neutral-900/60 dark:text-neutral-300"
              >
                {groupName}
                <span className="ml-2 font-normal text-neutral-400">
                  {groupRows.length}
                </span>
              </th>
            </tr>
            {groupRows.map((row) => (
              <Row key={row.ticker} row={row} />
            ))}
          </tbody>
        ))}
      </table>
    </div>
  );
}

function Row({ row }: { row: WatchlistRow }) {
  const primaryFmt = row.primary ? metric(row.primary.key).fmt : "multiple";
  return (
    <tr className="border-t border-neutral-100 hover:bg-neutral-50 dark:border-neutral-900 dark:hover:bg-neutral-900/50">
      <td className="px-3 py-2.5">
        <Link
          href={`/stock/${row.ticker}`}
          className="font-medium hover:underline"
        >
          {row.ticker}
        </Link>
        {/* truncate takes its bound from the fixed column width. */}
        <div className="truncate text-xs text-neutral-500 dark:text-neutral-500">
          {row.name}
        </div>
      </td>

      {/* Price sits under the change: the move is the daily signal, the level
          is just orientation. */}
      <Td>
        <span className={changeTone(row.changePct)}>
          {formatPercentChange(row.changePct)}
        </span>
        <div className="text-[11px] text-neutral-400">{formatPrice(row.close)}</div>
      </Td>

      <Td>
        {formatMetric(row.primary?.value ?? null, primaryFmt)}
        <div className="truncate font-sans text-[10px] text-neutral-400">
          {row.primaryLabel}
        </div>
      </Td>

      <PercentileCell row={row} />

      <Td className={changeTone(row.revenueGrowth)}>
        {formatMetric(row.revenueGrowth, "percent")}
      </Td>

      <td className="px-3 py-2.5">
        <div className="flex flex-wrap gap-1">
          {row.flags.map((flag) => (
            <span
              key={flag}
              className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] leading-tight text-amber-800 dark:bg-amber-950 dark:text-amber-300"
            >
              {flag}
            </span>
          ))}
        </div>
      </td>

      <td className="px-2 py-2.5 text-right">
        <RemoveStockButton ticker={row.ticker} />
      </td>
    </tr>
  );
}

/**
 * The percentile, given the visual weight it earns.
 *
 * This is the number the whole tool exists to produce — where a multiple sits
 * in its own history — and as a plain grey integer it read as just another
 * figure. The track makes position legible at a glance without having to
 * interpret the number.
 */
function PercentileCell({ row }: { row: WatchlistRow }) {
  const stats = row.primary;

  if (!stats?.sufficient || stats.percentile === null) {
    return (
      <td className="px-3 py-2.5 text-right">
        <span className="font-mono text-xs text-neutral-300 dark:text-neutral-700">
          —
        </span>
        <div className="text-[10px] text-neutral-400">
          {stats && stats.years > 0 ? `${stats.years.toFixed(0)}y only` : "no history"}
        </div>
      </td>
    );
  }

  const pct = stats.percentile;
  return (
    <td className="px-3 py-2.5">
      <div className="flex flex-col items-end gap-1">
        <span
          className={`font-mono text-sm font-medium tabular-nums ${percentileTone(pct)}`}
        >
          {Math.round(pct)}
        </span>
        <div className="relative h-1 w-16 rounded-full bg-neutral-200 dark:bg-neutral-800">
          <span
            className={`absolute top-1/2 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full ${percentileDot(pct)}`}
            style={{ left: `${Math.max(6, Math.min(94, pct))}%` }}
          />
        </div>
      </div>
    </td>
  );
}

/** Matches percentileTone, as a background rather than text colour. */
function percentileDot(pct: number): string {
  if (pct <= 20) return "bg-emerald-500";
  if (pct <= 40) return "bg-teal-500";
  if (pct <= 60) return "bg-neutral-500";
  if (pct <= 80) return "bg-amber-500";
  return "bg-rose-500";
}

function Td({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <td className={`px-3 py-2.5 text-right font-mono tabular-nums ${className}`}>
      {children}
    </td>
  );
}
