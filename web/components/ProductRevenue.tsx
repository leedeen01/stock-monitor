import { Sparkline } from "@/components/Sparkline";
import { formatBig, formatMetric } from "@/lib/format";
import type { SegmentBreakdown } from "@/lib/queries";

/**
 * Which product lines are growing and which are shrinking.
 *
 * Ordered by growth rather than size, because a small line turning down is
 * usually more informative than a large one holding steady. Share of total is
 * shown as context only — it answers a different question and answers this one
 * badly: a line growing 10% still loses share when the company grows 15%, so
 * ranking on share would show a genuinely growing product as falling.
 */
// Annual filers report roughly 60-90 days after fiscal year-end. Past 13
// months since the filing that carries the latest period, a newer 10-K almost
// certainly exists and simply hasn't been scraped yet — worth flagging rather
// than presenting this table as current.
const STALE_AFTER_DAYS = 395;

export function ProductRevenue({ data }: { data: SegmentBreakdown }) {
  const declining = data.lines.filter((l) => l.yoy !== null && l.yoy < 0).length;
  const span =
    data.periods.length > 1
      ? `${data.periods[0].slice(0, 4)}–${data.latestPeriod.slice(0, 4)}`
      : data.latestPeriod.slice(0, 4);

  const filedDaysAgo = Math.round(
    (Date.now() - new Date(data.filedAt).getTime()) / 86_400_000,
  );
  const stale = filedDaysAgo > STALE_AFTER_DAYS;

  return (
    <section className="lg:col-span-2">
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-medium">Revenue by product</h2>
        <span className="text-xs text-neutral-400">
          {span} · fiscal years · {data.lines.length} lines
          {declining > 0 && `, ${declining} shrinking`}
        </span>
      </div>

      <p
        className={`mb-3 text-xs ${
          stale
            ? "text-amber-600 dark:text-amber-500"
            : "text-neutral-400 dark:text-neutral-500"
        }`}
      >
        <span
          className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle ${
            stale ? "bg-amber-500" : "bg-neutral-300 dark:bg-neutral-700"
          }`}
        />
        As of the 10-K filed {data.filedAt}
        {stale &&
          " — over a year old; a newer filing likely exists and hasn't been picked up yet"}
      </p>

      <div className="overflow-x-auto rounded-lg border border-neutral-200 dark:border-neutral-800">
        <table className="w-full min-w-[620px] text-sm">
          <thead>
            <tr className="border-b border-neutral-200 text-xs text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
              <th className="px-3 py-2 text-left font-medium">Product line</th>
              <th className="px-3 py-2 text-right font-medium">Revenue</th>
              <th className="px-3 py-2 text-right font-medium">% of total</th>
              <th className="px-3 py-2 text-right font-medium">YoY</th>
              <th className="px-3 py-2 text-right font-medium">
                {data.lines[0]?.cagrYears ?? 3}-yr CAGR
              </th>
              <th className="w-28 px-3 py-2 text-left font-medium">Trend</th>
            </tr>
          </thead>
          <tbody>
            {data.lines.map((line) => (
              <tr
                key={line.label}
                className="border-b border-neutral-100 last:border-0 dark:border-neutral-900"
              >
                <td className="max-w-[260px] px-3 py-2 text-neutral-800 dark:text-neutral-200">
                  {line.label}
                </td>
                <td className="px-3 py-2 text-right font-mono tabular-nums">
                  {line.latest === null ? "—" : formatBig(line.latest)}
                </td>
                <td className="px-3 py-2 text-right font-mono tabular-nums text-neutral-500">
                  {formatMetric(line.share, "percent")}
                </td>
                <td className={`px-3 py-2 text-right font-mono tabular-nums ${growthTone(line.yoy)}`}>
                  {signed(line.yoy)}
                </td>
                <td className={`px-3 py-2 text-right font-mono tabular-nums ${growthTone(line.cagr)}`}>
                  {signed(line.cagr)}
                </td>
                <td className="px-3 py-2">
                  <Sparkline points={line.series} height={26} zeroLine={false} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="mt-2 text-xs text-neutral-400">
        Straight from the filings and reconciled against reported total revenue.
        Growth compares each line to itself a year earlier — blank when a filer
        reworded the line, rather than comparing two different products. Annual
        only, so a product that turned down mid-year shows up at the next 10-K.
      </p>
    </section>
  );
}

function signed(value: number | null): string {
  if (value === null) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(1)}%`;
}

function growthTone(value: number | null): string {
  if (value === null) return "text-neutral-300 dark:text-neutral-700";
  if (value > 0) return "text-emerald-600 dark:text-emerald-400";
  if (value < 0) return "text-rose-600 dark:text-rose-400";
  return "text-neutral-500";
}
