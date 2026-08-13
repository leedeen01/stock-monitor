import registry from "./metrics.json";

export type MetricFormat =
  | "multiple"
  | "percent"
  | "currency"
  | "number"
  | "days"
  | "ratio";

export type Metric = {
  key: string;
  label: string;
  fmt: MetricFormat;
  section: string;
  description: string;
  usage: string;
  higher_is_better: boolean | null;
  show_percentile: boolean;
  invert_percentile: boolean;
  peer_comparable: boolean;
};

export const METRICS: Metric[] = registry.metrics as Metric[];
export const SECTIONS: Record<string, string> = registry.sections;
export const MIN_YEARS_FOR_PERCENTILE: number = registry.minYearsForPercentile;

export const BY_KEY = new Map(METRICS.map((m) => [m.key, m]));

/**
 * Metric keys double as SQL column names, so anything reaching a query must be
 * checked against the registry rather than interpolated on trust.
 */
export function isKnownMetric(key: string): boolean {
  return BY_KEY.has(key);
}

export function metric(key: string): Metric {
  const m = BY_KEY.get(key);
  if (!m) throw new Error(`unknown metric: ${key}`);
  return m;
}

export const SECTION_ORDER = ["paying", "getting", "integrity", "leverage"];
