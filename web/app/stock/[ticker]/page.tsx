import Link from "next/link";
import { notFound } from "next/navigation";

import { PercentileBar } from "@/components/PercentileBar";
import { ProductRevenue } from "@/components/ProductRevenue";
import { Sparkline, yearTicks } from "@/components/Sparkline";
import {
  changeTone,
  formatBig,
  formatMetric,
  formatPercentChange,
  formatPrice,
} from "@/lib/format";
import { SECTIONS, SECTION_ORDER, metric } from "@/lib/metrics";
import { getSegments, getStockDetail } from "@/lib/queries";

export const dynamic = "force-dynamic";

export default async function StockPage(props: PageProps<"/stock/[ticker]">) {
  const { ticker } = await props.params;
  const search = await props.searchParams;
  const groupParam = Number(search.group);

  const detail = getStockDetail(
    ticker.toUpperCase(),
    Number.isInteger(groupParam) ? groupParam : undefined,
  );
  if (!detail) notFound();

  // Null for filers that publish no product breakdown that reconciles — AMD
  // and the energy majors among them — so the section simply does not render.
  const segments = getSegments(ticker.toUpperCase());

  const sections = [...detail.sections].sort(
    (a, b) => SECTION_ORDER.indexOf(a.section) - SECTION_ORDER.indexOf(b.section),
  );

  const series = detail.series;
  const dates = series.map((s) => s.date as string);
  const rangePosition =
    detail.close && detail.low52 !== null && detail.high52 !== null
      ? ((detail.close - detail.low52) / (detail.high52 - detail.low52)) * 100
      : null;

  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6">
      <Link
        href="/"
        className="text-sm text-neutral-500 hover:underline dark:text-neutral-400"
      >
        ← Watchlist
      </Link>

      <header className="mt-4 border-b border-neutral-200 pb-5 dark:border-neutral-800">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">
              {detail.ticker}
              <span className="ml-3 text-base font-normal text-neutral-500">
                {detail.name}
              </span>
            </h1>
            <div className="mt-1 flex items-baseline gap-3">
              <span className="font-mono text-xl tabular-nums">
                {formatPrice(detail.close)}
              </span>
              <span
                className={`font-mono text-sm tabular-nums ${changeTone(detail.changePct)}`}
              >
                {formatPercentChange(detail.changePct)}
              </span>
              <span className="text-xs text-neutral-400">as of {detail.date}</span>
            </div>
          </div>

          <dl className="flex gap-6 text-sm">
            <Stat label="Market cap" value={detail.marketCap ? formatBig(detail.marketCap) : "—"} />
            <Stat label="EV" value={detail.enterpriseValue ? formatBig(detail.enterpriseValue) : "—"} />
            <Stat label="Net debt" value={detail.netDebt !== null ? formatBig(detail.netDebt) : "—"} />
          </dl>
        </div>

        {rangePosition !== null && (
          <div className="mt-4 max-w-sm">
            <div className="flex justify-between text-xs text-neutral-500">
              <span className="font-mono">{formatPrice(detail.low52)}</span>
              <span>52-week range</span>
              <span className="font-mono">{formatPrice(detail.high52)}</span>
            </div>
            <div className="relative mt-1 h-1 rounded-full bg-neutral-200 dark:bg-neutral-800">
              <div
                className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-neutral-900 dark:bg-neutral-100"
                style={{ left: `${Math.max(2, Math.min(98, rangePosition))}%` }}
              />
            </div>
          </div>
        )}
      </header>

      {detail.groups.length > 1 && (
        <nav className="mt-5 flex flex-wrap items-center gap-2">
          <span className="text-xs text-neutral-500">Viewing as</span>
          {detail.groups.map((g) => {
            const active = g.id === detail.activeGroup?.id;
            return (
              <Link
                key={g.id}
                href={`/stock/${detail.ticker}?group=${g.id}`}
                className={`rounded-full px-3 py-1 text-xs ${
                  active
                    ? "bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900"
                    : "border border-neutral-300 text-neutral-600 hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-400 dark:hover:bg-neutral-900"
                }`}
              >
                {g.name}
              </Link>
            );
          })}
          <span className="text-xs text-neutral-400">
            — different groups, different metrics
          </span>
        </nav>
      )}

      <div className="mt-6 grid gap-8 lg:grid-cols-2">
        {sections.map((section) => (
          <section key={section.section}>
            <h2 className="mb-1 text-sm font-medium">
              {SECTIONS[section.section] ?? section.section}
            </h2>
            <div className="divide-y divide-neutral-100 dark:divide-neutral-900">
              {section.metrics.map((stats) => (
                <PercentileBar key={stats.key} stats={stats} />
              ))}
            </div>
          </section>
        ))}

        {segments && <ProductRevenue data={segments} />}

        <section className="lg:col-span-2">
          <h2 className="mb-3 text-sm font-medium">History</h2>
          <div className="grid gap-6 lg:grid-cols-2">
            <Chart
              title="Revenue growth YoY"
              hint="The input that justifies any multiple"
              points={series.map((s) => s.revenue_growth_yoy as number | null)}
              dates={dates}
              latest={series.at(-1)?.revenue_growth_yoy as number | null}
              fmt="percent"
              zeroLine
            />
            <Chart
              title="Operating margin"
              hint="Direction matters more than level"
              points={series.map((s) => s.operating_margin as number | null)}
              dates={dates}
              latest={series.at(-1)?.operating_margin as number | null}
              fmt="percent"
            />
            <Chart
              title="Gross margin"
              hint="Separates software economics from resale"
              points={series.map((s) => s.gross_margin as number | null)}
              dates={dates}
              latest={series.at(-1)?.gross_margin as number | null}
              fmt="percent"
            />
            <Chart
              title="Diluted share count"
              hint="Rising means your slice is shrinking"
              points={series.map((s) => s.shares_diluted as number | null)}
              dates={dates}
              latest={series.at(-1)?.shares_diluted as number | null}
              fmt="number"
            />
            <Chart
              title="FCF conversion"
              hint="Below 0.8 for long: profit isn't becoming cash"
              points={series.map((s) => s.fcf_conversion as number | null)}
              dates={dates}
              latest={series.at(-1)?.fcf_conversion as number | null}
              fmt="ratio"
            />
            <Chart
              title="ROIC"
              hint="Value is only created above cost of capital"
              points={series.map((s) => s.roic as number | null)}
              dates={dates}
              latest={series.at(-1)?.roic as number | null}
              fmt="percent"
            />
          </div>
          <p className="mt-3 text-xs text-neutral-400">
            {dates.length > 1 && `${dates[0]} to ${dates.at(-1)}, `}sampled weekly.
            Series are aligned on filing date, so nothing appears before it was
            public.
          </p>
        </section>
      </div>
    </main>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-neutral-500">{label}</dt>
      <dd className="font-mono tabular-nums">{value}</dd>
    </div>
  );
}

function Chart({
  title,
  hint,
  points,
  dates,
  latest,
  fmt,
  zeroLine = false,
}: {
  title: string;
  hint: string;
  points: (number | null)[];
  dates: string[];
  latest: number | null | undefined;
  fmt: Parameters<typeof formatMetric>[1];
  zeroLine?: boolean;
}) {
  const ticks = yearTicks(dates);
  const values = points.filter((p): p is number => p !== null);
  const hasRange = values.length >= 2;
  const low = hasRange ? Math.min(...values, ...(zeroLine ? [0] : [])) : null;
  const high = hasRange ? Math.max(...values, ...(zeroLine ? [0] : [])) : null;

  return (
    <div className="rounded-lg border border-neutral-200 p-4 dark:border-neutral-800">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm text-neutral-700 dark:text-neutral-300">
          {title}
        </span>
        <span className="font-mono text-base tabular-nums">
          {formatMetric(latest ?? null, fmt)}
        </span>
      </div>

      <div className="relative mt-3">
        <Sparkline
          points={points}
          height={160}
          zeroLine={zeroLine}
          gridPositions={ticks.map((t) => t.pct)}
        />
        {hasRange && (
          <>
            <span className="pointer-events-none absolute left-0 top-0 font-mono text-[10px] tabular-nums text-neutral-400">
              {formatMetric(high, fmt)}
            </span>
            <span className="pointer-events-none absolute bottom-0 left-0 font-mono text-[10px] tabular-nums text-neutral-400">
              {formatMetric(low, fmt)}
            </span>
          </>
        )}
      </div>

      {/* Year axis. Positions come from yearTicks so they line up with the
          plotted points rather than with elapsed calendar time. */}
      <div className="relative mt-1 h-4 border-t border-neutral-200 dark:border-neutral-800">
        {ticks.map((t) => (
          <span
            key={t.year}
            className="absolute top-1 -translate-x-1/2 font-mono text-[10px] tabular-nums text-neutral-400"
            style={{ left: `${t.pct}%` }}
          >
            {t.year}
          </span>
        ))}
      </div>

      <p className="mt-2 text-[11px] leading-tight text-neutral-400">{hint}</p>
    </div>
  );
}
