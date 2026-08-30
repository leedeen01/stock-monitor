import "server-only";

import { db } from "./db";
import { MIN_YEARS_FOR_PERCENTILE, isKnownMetric, metric } from "./metrics";

const PERCENTILE_WINDOW_YEARS = 10;

export type MetricStats = {
  key: string;
  value: number | null;
  percentile: number | null; // 0-100 within the stock's own history
  median: number | null;
  low: number | null;
  high: number | null;
  years: number; // history actually available
  sufficient: boolean; // enough history for the percentile to mean anything
  groupMedian: number | null; // peer comparison across the group
};

export type GroupRef = { id: number; name: string; primaryMultiple: string };

export type WatchlistRow = {
  ticker: string;
  name: string;
  date: string;
  close: number | null;
  changePct: number | null;
  marketCap: number | null;
  primary: MetricStats | null;
  primaryLabel: string;
  revenueGrowth: number | null;
  group: GroupRef | null;
  groups: GroupRef[];
  flags: string[];
};

function shiftYears(date: string, years: number): string {
  const d = new Date(date);
  d.setFullYear(d.getFullYear() - years);
  return d.toISOString().slice(0, 10);
}

/**
 * Distribution of a metric across a ticker's own history.
 *
 * The percentile is the point of the whole tool: "P/E is 28" is not
 * information, "P/E is at the 91st percentile of its own ten years" is. When
 * there isn't enough history for that to be honest — Alphabet's EV/EBITDA only
 * spans a few years because it never tagged D&A before 2021 — `sufficient` is
 * false and the UI says so rather than implying precision it can't support.
 */
export function metricStats(
  ticker: string,
  key: string,
  groupId?: number | null,
): MetricStats {
  if (!isKnownMetric(key)) throw new Error(`unknown metric: ${key}`);
  const conn = db();

  const latest = conn
    .prepare(
      `SELECT date, ${key} AS value FROM ratios_daily
       WHERE ticker = ? AND ${key} IS NOT NULL ORDER BY date DESC LIMIT 1`,
    )
    .get(ticker) as { date: string; value: number } | undefined;

  if (!latest) {
    return {
      key, value: null, percentile: null, median: null, low: null, high: null,
      years: 0, sufficient: false, groupMedian: null,
    };
  }

  const since = shiftYears(latest.date, PERCENTILE_WINDOW_YEARS);
  const dist = conn
    .prepare(
      `SELECT COUNT(*) AS n,
              SUM(CASE WHEN ${key} <= ? THEN 1 ELSE 0 END) AS below,
              MIN(${key}) AS lo, MAX(${key}) AS hi, MIN(date) AS first
       FROM ratios_daily
       WHERE ticker = ? AND ${key} IS NOT NULL AND date >= ?`,
    )
    .get(latest.value, ticker, since) as {
    n: number; below: number; lo: number; hi: number; first: string;
  };

  const median = (
    conn
      .prepare(
        `SELECT ${key} AS v FROM ratios_daily
         WHERE ticker = ? AND ${key} IS NOT NULL AND date >= ?
         ORDER BY ${key} LIMIT 1 OFFSET ?`,
      )
      .get(ticker, since, Math.floor(dist.n / 2)) as { v: number } | undefined
  )?.v ?? null;

  const years =
    (new Date(latest.date).getTime() - new Date(dist.first).getTime()) /
    (365.25 * 24 * 3600 * 1000);

  return {
    key,
    value: latest.value,
    percentile: dist.n > 0 ? (dist.below / dist.n) * 100 : null,
    median,
    low: dist.lo,
    high: dist.hi,
    years,
    sufficient: years >= MIN_YEARS_FOR_PERCENTILE && dist.n > 100,
    groupMedian: groupId ? groupMedian(key, groupId) : null,
  };
}

/**
 * Median of the current value across the group. Group membership is the peer
 * set — you defined which stocks are comparable, so this beats a generic
 * sector average.
 */
function groupMedian(key: string, groupId: number): number | null {
  if (!isKnownMetric(key)) return null;
  const rows = db()
    .prepare(
      `SELECT r.${key} AS v
       FROM stock_groups sg
       JOIN (SELECT ticker, MAX(date) d FROM ratios_daily GROUP BY ticker) m
         ON m.ticker = sg.ticker
       JOIN ratios_daily r ON r.ticker = sg.ticker AND r.date = m.d
       WHERE sg.group_id = ? AND r.${key} IS NOT NULL
       ORDER BY r.${key}`,
    )
    .all(groupId) as { v: number }[];
  if (!rows.length) return null;
  return rows[Math.floor(rows.length / 2)].v;
}

export type PipelineStatus = {
  startedAt: string | null;
  finishedAt: string | null;
  status: string | null;
  latestSession: string | null;
  newPriceRows: number | null;
  detail: string | null;
  hoursAgo: number | null;
  stale: boolean;
};

/**
 * Freshness of the last scheduled run.
 *
 * A refresh job that quietly stops is the failure mode that matters most here:
 * the page still renders perfectly good-looking numbers, they are just wrong by
 * however long it has been broken. So staleness is surfaced rather than left to
 * be noticed.
 */
export function getPipelineStatus(): PipelineStatus {
  const row = db()
    .prepare(
      `SELECT started_at, finished_at, status, latest_session, new_price_rows, detail
       FROM pipeline_runs ORDER BY id DESC LIMIT 1`,
    )
    .get() as
    | {
        started_at: string; finished_at: string | null; status: string;
        latest_session: string | null; new_price_rows: number | null;
        detail: string | null;
      }
    | undefined;

  if (!row) {
    return {
      startedAt: null, finishedAt: null, status: null, latestSession: null,
      newPriceRows: null, detail: null, hoursAgo: null, stale: true,
    };
  }

  const reference = row.finished_at ?? row.started_at;
  const hoursAgo = (Date.now() - new Date(reference).getTime()) / 3_600_000;

  // Three days covers a normal weekend plus a holiday without crying wolf.
  const stale = hoursAgo > 72 || row.status === "error" || row.status === "running";

  return {
    startedAt: row.started_at,
    finishedAt: row.finished_at,
    status: row.status,
    latestSession: row.latest_session,
    newPriceRows: row.new_price_rows,
    detail: row.detail,
    hoursAgo,
    stale,
  };
}

export type AlertEvent = {
  id: number;
  ruleId: number;
  ruleName: string;
  ticker: string;
  triggerDate: string;
  createdAt: string;
  metricKey: string | null;
  value: number | null;
  percentile: number | null;
  detail: string;
};

export type AlertRule = {
  id: number;
  name: string;
  scope: string;
  scopeRef: string | null;
  scopeLabel: string;
  metricKey: string;
  condition: string;
  threshold: number | null;
  enabled: boolean;
  openCount: number;
};

/** Unacknowledged crossings, newest first. */
export function getOpenAlerts(userId: number, limit = 50): AlertEvent[] {
  return (
    db()
      .prepare(
        `SELECT e.id, e.rule_id, r.name AS rule_name, e.ticker, e.trigger_date,
                e.created_at, e.metric_key, e.value, e.percentile, e.detail
         FROM alert_events e
         JOIN alert_rules r ON r.id = e.rule_id
         WHERE e.acknowledged = 0 AND e.user_id = ?
         ORDER BY e.trigger_date DESC, e.id DESC
         LIMIT ?`,
      )
      .all(userId, limit) as Record<string, never>[]
  ).map((r: Record<string, unknown>) => ({
    id: r.id as number,
    ruleId: r.rule_id as number,
    ruleName: r.rule_name as string,
    ticker: r.ticker as string,
    triggerDate: r.trigger_date as string,
    createdAt: r.created_at as string,
    metricKey: (r.metric_key as string) ?? null,
    value: (r.value as number) ?? null,
    percentile: (r.percentile as number) ?? null,
    detail: r.detail as string,
  }));
}

export function getAlertRules(userId: number): AlertRule[] {
  const rows = db()
    .prepare(
      `SELECT r.*,
              (SELECT COUNT(*) FROM alert_events e
               WHERE e.rule_id = r.id AND e.acknowledged = 0) AS open_count,
              (SELECT g.name FROM metric_groups g
               WHERE CAST(g.id AS TEXT) = r.scope_ref) AS group_name
       FROM alert_rules r WHERE r.user_id = ? ORDER BY r.id`,
    )
    .all(userId) as Record<string, unknown>[];

  return rows.map((r) => ({
    id: r.id as number,
    name: r.name as string,
    scope: r.scope as string,
    scopeRef: (r.scope_ref as string) ?? null,
    scopeLabel:
      r.scope === "all"
        ? "All stocks"
        : r.scope === "group"
          ? ((r.group_name as string) ?? `Group ${r.scope_ref}`)
          : (r.scope_ref as string),
    metricKey: r.metric_key as string,
    condition: r.condition as string,
    threshold: (r.threshold as number) ?? null,
    enabled: Boolean(r.enabled),
    openCount: r.open_count as number,
  }));
}

export function getGroups(userId: number): GroupRef[] {
  return (
    db()
      .prepare(
        `SELECT id, name, primary_multiple FROM metric_groups
          WHERE user_id = ? ORDER BY id`,
      )
      .all(userId) as { id: number; name: string; primary_multiple: string }[]
  ).map((g) => ({ id: g.id, name: g.name, primaryMultiple: g.primary_multiple }));
}

function groupsFor(ticker: string, userId: number): GroupRef[] {
  return (
    db()
      .prepare(
        `SELECT g.id, g.name, g.primary_multiple
         FROM stock_groups sg JOIN metric_groups g ON g.id = sg.group_id
         WHERE sg.ticker = ? AND sg.user_id = ? ORDER BY g.id`,
      )
      .all(ticker, userId) as { id: number; name: string; primary_multiple: string }[]
  ).map((g) => ({ id: g.id, name: g.name, primaryMultiple: g.primary_multiple }));
}

/** Attention-worthy signals, computed against a year ago. */
function computeFlags(ticker: string, primary: MetricStats | null): string[] {
  const conn = db();
  const flags: string[] = [];

  const latest = conn
    .prepare(
      `SELECT date, operating_margin, shares_diluted, inventory_days,
              revenue_growth_yoy
       FROM ratios_daily WHERE ticker = ? ORDER BY date DESC LIMIT 1`,
    )
    .get(ticker) as Record<string, number | null> & { date: string };
  if (!latest) return flags;

  const prior = conn
    .prepare(
      `SELECT operating_margin, shares_diluted, inventory_days
       FROM ratios_daily WHERE ticker = ? AND date <= ? ORDER BY date DESC LIMIT 1`,
    )
    .get(ticker, shiftYears(latest.date, 1)) as Record<string, number | null>;

  if (prior) {
    if (
      latest.operating_margin !== null && prior.operating_margin !== null &&
      latest.operating_margin < prior.operating_margin - 0.02
    ) {
      flags.push("margin compressing");
    }
    if (
      latest.shares_diluted !== null && prior.shares_diluted !== null &&
      latest.shares_diluted > prior.shares_diluted * 1.02
    ) {
      flags.push("dilution");
    }
    // The semiconductor cycle tell: inventory turning over more slowly while
    // revenue growth is already rolling over.
    if (
      latest.inventory_days !== null && prior.inventory_days !== null &&
      latest.inventory_days > prior.inventory_days * 1.15 &&
      (latest.revenue_growth_yoy ?? 1) < 0.1
    ) {
      flags.push("inventory building");
    }
  }

  if (primary?.sufficient && primary.percentile !== null) {
    if (primary.percentile >= 95) flags.push("multiple at 5yr high");
    if (primary.percentile <= 5) flags.push("multiple at 5yr low");
  }
  return flags;
}

export function getWatchlist(userId: number): WatchlistRow[] {
  const conn = db();
  const watchlist = conn
    .prepare(
      `SELECT w.ticker, t.name, w.default_group_id
       FROM watchlist w
       JOIN tickers t ON t.ticker = w.ticker
       WHERE w.user_id = ? AND t.supported = 1
       ORDER BY w.ticker`,
    )
    .all(userId) as {
    ticker: string; name: string; default_group_id: number | null;
  }[];

  const rows: WatchlistRow[] = [];

  for (const w of watchlist) {
    const recent = conn
      .prepare(
        `SELECT date, close, market_cap, revenue_growth_yoy
         FROM ratios_daily WHERE ticker = ? ORDER BY date DESC LIMIT 2`,
      )
      .all(w.ticker) as {
      date: string; close: number | null; market_cap: number | null;
      revenue_growth_yoy: number | null;
    }[];
    if (!recent.length) continue;

    const [latest, previous] = recent;
    const groups = groupsFor(w.ticker, userId);
    const group =
      groups.find((g) => g.id === w.default_group_id) ?? groups[0] ?? null;

    // The primary multiple comes from the group, which is what lets one grid
    // hold both profitable and pre-profit names without a mode switch.
    const primaryKey = group?.primaryMultiple ?? "pe_ttm";
    const primary = isKnownMetric(primaryKey)
      ? metricStats(w.ticker, primaryKey, group?.id)
      : null;

    rows.push({
      ticker: w.ticker,
      name: w.name,
      date: latest.date,
      close: latest.close,
      changePct:
        latest.close && previous?.close
          ? latest.close / previous.close - 1
          : null,
      marketCap: latest.market_cap,
      primary,
      primaryLabel: isKnownMetric(primaryKey) ? metric(primaryKey).label : primaryKey,
      revenueGrowth: latest.revenue_growth_yoy,
      group,
      groups,
      flags: computeFlags(w.ticker, primary),
    });
  }

  // Cheapest against its own history first — an opinionated default that puts
  // the interesting rows where the eye lands.
  rows.sort((a, b) => {
    const pa = a.primary?.percentile ?? 999;
    const pb = b.primary?.percentile ?? 999;
    return pa - pb;
  });
  return rows;
}

export type SectionMetrics = { section: string; metrics: MetricStats[] };

export type StockDetail = {
  ticker: string;
  name: string;
  date: string;
  close: number | null;
  changePct: number | null;
  marketCap: number | null;
  enterpriseValue: number | null;
  netDebt: number | null;
  low52: number | null;
  high52: number | null;
  groups: GroupRef[];
  activeGroup: GroupRef | null;
  sections: SectionMetrics[];
  series: { date: string; [k: string]: number | string | null }[];
};

export function getStockDetail(
  ticker: string,
  userId: number,
  groupId?: number,
): StockDetail | null {
  const conn = db();
  const w = conn
    .prepare(
      `SELECT w.ticker, t.name, w.default_group_id
         FROM watchlist w
         JOIN tickers t ON t.ticker = w.ticker
        WHERE w.ticker = ? AND w.user_id = ? AND t.supported = 1`,
    )
    .get(ticker, userId) as
    | { ticker: string; name: string; default_group_id: number | null }
    | undefined;
  if (!w) return null;

  const recent = conn
    .prepare(
      `SELECT date, close, market_cap, enterprise_value, net_debt
       FROM ratios_daily WHERE ticker = ? ORDER BY date DESC LIMIT 2`,
    )
    .all(ticker) as {
    date: string; close: number | null; market_cap: number | null;
    enterprise_value: number | null; net_debt: number | null;
  }[];
  if (!recent.length) return null;
  const [latest, previous] = recent;

  const range = conn
    .prepare(
      `SELECT MIN(close) lo, MAX(close) hi FROM prices
       WHERE ticker = ? AND date >= ?`,
    )
    .get(ticker, shiftYears(latest.date, 1)) as { lo: number; hi: number };

  const groups = groupsFor(ticker, userId);
  const activeGroup =
    groups.find((g) => g.id === groupId) ??
    groups.find((g) => g.id === w.default_group_id) ??
    groups[0] ??
    null;

  const sections: SectionMetrics[] = [];
  if (activeGroup) {
    const assigned = conn
      .prepare(
        `SELECT metric_key, section FROM group_metrics
         WHERE group_id = ? ORDER BY sort_order`,
      )
      .all(activeGroup.id) as { metric_key: string; section: string }[];

    const bySection = new Map<string, string[]>();
    for (const a of assigned) {
      if (!bySection.has(a.section)) bySection.set(a.section, []);
      bySection.get(a.section)!.push(a.metric_key);
    }
    for (const [section, keys] of bySection) {
      sections.push({
        section,
        metrics: keys
          .filter(isKnownMetric)
          .map((k) => metricStats(ticker, k, activeGroup.id)),
      });
    }
  }

  // Weekly sampling keeps the payload small without changing what a
  // multi-year chart looks like.
  const series = conn
    .prepare(
      `SELECT date, close, pe_ttm, ev_ebitda, ev_sales, fcf_yield,
              gross_margin, operating_margin, net_margin, fcf_margin, roic,
              revenue_growth_yoy, shares_diluted, sbc_pct_revenue,
              net_debt_ebitda, inventory_days, fcf_conversion
       FROM ratios_daily
       WHERE ticker = ? AND date >= ?
       ORDER BY date`,
    )
    .all(ticker, shiftYears(latest.date, PERCENTILE_WINDOW_YEARS)) as Record<
    string,
    number | string | null
  >[];

  return {
    ticker: w.ticker,
    name: w.name,
    date: latest.date,
    close: latest.close,
    changePct:
      latest.close && previous?.close ? latest.close / previous.close - 1 : null,
    marketCap: latest.market_cap,
    enterpriseValue: latest.enterprise_value,
    netDebt: latest.net_debt,
    low52: range?.lo ?? null,
    high52: range?.hi ?? null,
    groups,
    activeGroup,
    sections,
    series: series.filter((_, i) => i % 5 === 0) as StockDetail["series"],
  };
}

export type SegmentLine = {
  label: string;
  latest: number | null;
  share: number | null; // of total revenue this period
  yoy: number | null;
  cagr: number | null; // annualised over the window below
  cagrYears: number;
  series: (number | null)[]; // aligned to `periods`
};

export type SegmentBreakdown = {
  periods: string[];
  lines: SegmentLine[];
  total: number | null;
  latestPeriod: string;
  filedAt: string; // date the 10-K carrying the latest period was filed
};

const CAGR_YEARS = 3;

/**
 * Revenue by product line, and whether each is growing or shrinking.
 *
 * Growth is matched on the label to itself a year earlier, never on row
 * position. Filers reword lines between filings — Alphabet's "Google Network"
 * became "Google Network Members' properties" for two years — and comparing by
 * position would silently report the growth of one product as another's. When
 * a label has no counterpart in the prior period the cell is left null rather
 * than fabricated.
 *
 * Only non-subtotal rows are returned. The source tables interleave hierarchy
 * levels, so including aggregates would count the same revenue twice.
 *
 * Unlike the rest of the deep dive, this panel does not move with the daily
 * refresh — it is re-parsed only when a new 10-K lands, roughly once a year.
 * `filedAt` carries that filing date to the UI so a page updated minutes ago
 * doesn't imply a breakdown that's actually eleven months old.
 */
export function getSegments(ticker: string): SegmentBreakdown | null {
  const rows = db()
    .prepare(
      `SELECT s.period_end, s.label, s.value, s.filed_at
       FROM segment_revenue s
       WHERE s.ticker = ? AND s.is_subtotal = 0
         AND s.filed_at = (SELECT MAX(filed_at) FROM segment_revenue x
                           WHERE x.ticker = s.ticker AND x.period_end = s.period_end)
       ORDER BY s.period_end`,
    )
    .all(ticker.toUpperCase()) as {
    period_end: string; label: string; value: number; filed_at: string;
  }[];

  if (rows.length === 0) return null;

  const periods = [...new Set(rows.map((r) => r.period_end))].sort();
  const byLabel = new Map<string, Map<string, number>>();
  for (const row of rows) {
    if (!byLabel.has(row.label)) byLabel.set(row.label, new Map());
    byLabel.get(row.label)!.set(row.period_end, row.value);
  }

  const latestPeriod = periods[periods.length - 1];
  const priorPeriod = periods[periods.length - 2];
  const cagrIndex = periods.length - 1 - CAGR_YEARS;
  const cagrPeriod = cagrIndex >= 0 ? periods[cagrIndex] : undefined;

  const total = rows
    .filter((r) => r.period_end === latestPeriod)
    .reduce((sum, r) => sum + r.value, 0);

  // filed_at is uniform across every row of the latest period's filing — the
  // query's own MAX(filed_at) join guarantees that — so the first match is exact.
  const filedAt = rows.find((r) => r.period_end === latestPeriod)!.filed_at;

  const lines: SegmentLine[] = [...byLabel.entries()].map(([label, values]) => {
    const latest = values.get(latestPeriod) ?? null;
    const prior = priorPeriod ? values.get(priorPeriod) : undefined;
    const base = cagrPeriod ? values.get(cagrPeriod) : undefined;

    return {
      label,
      latest,
      share: latest !== null && total ? latest / total : null,
      yoy: latest !== null && prior ? latest / prior - 1 : null,
      cagr:
        latest !== null && base && base > 0
          ? Math.pow(latest / base, 1 / CAGR_YEARS) - 1
          : null,
      cagrYears: CAGR_YEARS,
      series: periods.map((p) => values.get(p) ?? null),
    };
  });

  // Only lines present in the most recent filing are current products; older
  // wordings linger in the table and would otherwise show as dead rows.
  const current = lines.filter((l) => l.latest !== null);

  // Fastest growth first, so decliners land together at the bottom where they
  // are the easiest thing to spot.
  current.sort((a, b) => {
    if (a.yoy === null && b.yoy === null) return (b.latest ?? 0) - (a.latest ?? 0);
    if (a.yoy === null) return 1;
    if (b.yoy === null) return -1;
    return b.yoy - a.yoy;
  });

  return { periods, lines: current, total, latestPeriod, filedAt };
}
