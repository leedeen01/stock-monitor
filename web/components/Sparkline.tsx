/**
 * Minimal inline SVG series. No charting library — these are non-interactive
 * and a dependency would cost more than it returns.
 */
export function Sparkline({
  points,
  height = 140,
  className = "",
  zeroLine = false,
  gridPositions = [],
}: {
  points: (number | null)[];
  height?: number;
  className?: string;
  zeroLine?: boolean;
  /** X positions (0-100) for year gridlines, from `yearTicks`. */
  gridPositions?: number[];
}) {
  const values = points.filter((p): p is number => p !== null);
  if (values.length < 2) {
    return (
      <div
        className="flex items-center justify-center text-xs text-neutral-400"
        style={{ height }}
      >
        insufficient history
      </div>
    );
  }

  const min = Math.min(...values, zeroLine ? 0 : Infinity);
  const max = Math.max(...values, zeroLine ? 0 : -Infinity);
  const span = max - min || 1;
  const width = 100;
  // Breathing room so the line never sits flush against the frame.
  const pad = height * 0.08;
  const plot = height - pad * 2;

  const y = (v: number) => height - pad - ((v - min) / span) * plot;

  const coords = points
    .map((p, i) => {
      if (p === null) return null;
      const x = (i / (points.length - 1)) * width;
      return `${x.toFixed(2)},${y(p).toFixed(2)}`;
    })
    .filter(Boolean)
    .join(" ");

  const zeroY = y(0);

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className={`w-full ${className}`}
      style={{ height }}
      aria-hidden="true"
    >
      {gridPositions.map((x) => (
        <line
          key={x}
          x1={x}
          x2={x}
          y1="0"
          y2={height}
          className="stroke-neutral-200 dark:stroke-neutral-800"
          strokeWidth="1"
          vectorEffect="non-scaling-stroke"
        />
      ))}
      {zeroLine && zeroY >= 0 && zeroY <= height && (
        <line
          x1="0"
          x2={width}
          y1={zeroY}
          y2={zeroY}
          className="stroke-neutral-400 dark:stroke-neutral-600"
          strokeWidth="1"
          strokeDasharray="3 3"
          vectorEffect="non-scaling-stroke"
        />
      )}
      <polyline
        points={coords}
        fill="none"
        className="stroke-neutral-800 dark:stroke-neutral-200"
        strokeWidth="1.75"
        vectorEffect="non-scaling-stroke"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

export type YearTick = { year: number; pct: number };

/**
 * Year positions along the series, thinned so labels don't collide.
 *
 * Positions come from where each year first appears in the array rather than
 * from elapsed time, so they line up exactly with the plotted points even
 * though the series is sampled weekly and has gaps at market holidays.
 */
export function yearTicks(dates: string[], maxTicks = 6): YearTick[] {
  if (dates.length < 2) return [];

  const firstIndex = new Map<number, number>();
  dates.forEach((d, i) => {
    const year = Number(d.slice(0, 4));
    if (!firstIndex.has(year)) firstIndex.set(year, i);
  });

  const years = [...firstIndex.keys()].sort((a, b) => a - b);
  // Drop a leading partial year — its label would sit on the left edge.
  const usable = firstIndex.get(years[0]) === 0 && years.length > 1 ? years.slice(1) : years;
  const step = Math.max(1, Math.ceil(usable.length / maxTicks));

  return usable
    .filter((_, i) => i % step === 0)
    .map((year) => ({
      year,
      pct: (firstIndex.get(year)! / (dates.length - 1)) * 100,
    }));
}
