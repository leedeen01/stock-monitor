import { MetricInfo } from "@/components/MetricInfo";
import type { MetricStats } from "@/lib/queries";
import { formatMetric, ordinal, percentileTone } from "@/lib/format";
import { metric } from "@/lib/metrics";

/**
 * A multiple rendered in context: current value, where it sits in the stock's
 * own range, and the group median for peer comparison.
 *
 * The bare number is deliberately never shown alone. "EV/EBITDA 28.4" says
 * nothing; "28.4, 91st percentile of its own 10 years, group median 19.7" is a
 * statement you can act on.
 */
export function PercentileBar({ stats }: { stats: MetricStats }) {
  const m = metric(stats.key);
  const { value, percentile, median, low, high, sufficient, groupMedian } = stats;

  if (value === null) {
    return (
      <div className="py-3">
        <div className="flex items-baseline justify-between">
          <span className="flex items-center gap-1.5 text-sm text-neutral-600 dark:text-neutral-400">
            {m.label}
            <MetricInfo
              label={m.label}
              description={m.description}
              usage={m.usage}
            />
          </span>
          <span className="font-mono text-sm text-neutral-400">—</span>
        </div>
        <p className="mt-1 text-xs text-neutral-400 dark:text-neutral-600">
          No data — see the metric notes for why this can be absent.
        </p>
      </div>
    );
  }

  // Where the current value sits within the observed range, for the marker.
  const span = high !== null && low !== null ? high - low : 0;
  const position = span > 0 ? ((value - low!) / span) * 100 : 50;
  const medianPosition =
    span > 0 && median !== null ? ((median - low!) / span) * 100 : null;

  return (
    <div className="py-3">
      <div className="flex items-baseline justify-between gap-3">
        <span className="flex items-center gap-1.5 text-sm text-neutral-700 dark:text-neutral-300">
          {m.label}
          <MetricInfo
            label={m.label}
            description={m.description}
            usage={m.usage}
          />
        </span>
        <span className="font-mono text-sm font-medium tabular-nums">
          {formatMetric(value, m.fmt)}
        </span>
      </div>

      {sufficient ? (
        <>
          <div className="relative mt-2 h-1.5 rounded-full bg-neutral-200 dark:bg-neutral-800">
            {medianPosition !== null && (
              <div
                className="absolute top-[-3px] h-[12px] w-px bg-neutral-400 dark:bg-neutral-600"
                style={{ left: `${clamp(medianPosition)}%` }}
                title={`Median ${formatMetric(median, m.fmt)}`}
              />
            )}
            <div
              className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-neutral-900 dark:border-neutral-950 dark:bg-neutral-100"
              style={{ left: `${clamp(position)}%` }}
            />
          </div>
          <div className="mt-1.5 flex justify-between text-xs text-neutral-500 dark:text-neutral-500">
            <span className="font-mono tabular-nums">
              {formatMetric(low, m.fmt)}
            </span>
            <span className={percentileTone(percentile)}>
              {percentile !== null ? `${ordinal(percentile)} pctile` : "—"}
              {median !== null && (
                <span className="ml-1.5 text-neutral-400 dark:text-neutral-600">
                  · med {formatMetric(median, m.fmt)}
                </span>
              )}
            </span>
            <span className="font-mono tabular-nums">
              {formatMetric(high, m.fmt)}
            </span>
          </div>
        </>
      ) : (
        <p className="mt-1.5 text-xs text-amber-600 dark:text-amber-500">
          Only {stats.years.toFixed(1)}y of history — too short for a meaningful
          percentile.
        </p>
      )}

      {groupMedian !== null && m.peer_comparable && (
        <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-500">
          Group median{" "}
          <span className="font-mono tabular-nums">
            {formatMetric(groupMedian, m.fmt)}
          </span>
        </p>
      )}
    </div>
  );
}

function clamp(n: number): number {
  return Math.max(2, Math.min(98, n));
}
