import type { MetricFormat } from "./metrics";

export function formatMetric(
  value: number | null | undefined,
  fmt: MetricFormat,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";

  switch (fmt) {
    case "multiple":
      return `${value.toFixed(1)}x`;
    case "ratio":
      return value.toFixed(2);
    case "percent":
      return `${(value * 100).toFixed(1)}%`;
    case "days":
      return `${Math.round(value)}d`;
    case "currency":
      return formatBig(value);
    case "number":
      return formatBig(value, false);
    default:
      return String(value);
  }
}

export function formatBig(value: number, currency = true): string {
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  const prefix = currency ? "$" : "";
  if (abs >= 1e12) return `${sign}${prefix}${(abs / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${sign}${prefix}${(abs / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `${sign}${prefix}${(abs / 1e6).toFixed(1)}M`;
  return `${sign}${prefix}${abs.toLocaleString(undefined, {
    maximumFractionDigits: 0,
  })}`;
}

export function formatPrice(value: number | null): string {
  if (value === null) return "—";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function formatPercentChange(value: number | null): string {
  if (value === null) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(2)}%`;
}

/** 1st, 2nd, 3rd, 4th… including the 11-13 exceptions. */
export function ordinal(n: number): string {
  const rounded = Math.round(n);
  const mod100 = rounded % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${rounded}th`;
  switch (rounded % 10) {
    case 1:
      return `${rounded}st`;
    case 2:
      return `${rounded}nd`;
    case 3:
      return `${rounded}rd`;
    default:
      return `${rounded}th`;
  }
}

/**
 * Colour for a valuation percentile. Deliberately not a red/green good-bad
 * scale: a low percentile means cheap against the stock's own history, which is
 * information, not a recommendation.
 */
export function percentileTone(pct: number | null): string {
  if (pct === null) return "text-neutral-400 dark:text-neutral-500";
  if (pct <= 20) return "text-emerald-600 dark:text-emerald-400";
  if (pct <= 40) return "text-teal-600 dark:text-teal-400";
  if (pct <= 60) return "text-neutral-600 dark:text-neutral-300";
  if (pct <= 80) return "text-amber-600 dark:text-amber-400";
  return "text-rose-600 dark:text-rose-400";
}

export function changeTone(value: number | null): string {
  if (value === null || value === 0) return "text-neutral-500";
  return value > 0
    ? "text-emerald-600 dark:text-emerald-400"
    : "text-rose-600 dark:text-rose-400";
}
