"""Coverage and correctness report for revenue segmentation."""

import db


def main() -> None:
    conn = db.connect()

    print("=== coverage: periods with a product breakdown ===")
    print(f"{'TICKER':<8}{'PERIODS':>8}{'LINES':>7}{'FROM':>13}{'TO':>13}  LATEST BREAKDOWN")
    print("-" * 96)
    for r in conn.execute("""
        SELECT ticker, COUNT(DISTINCT period_end) periods, COUNT(*) rows,
               MIN(period_end) lo, MAX(period_end) hi
        FROM segment_revenue GROUP BY ticker ORDER BY ticker
    """):
        latest = [x["label"] for x in conn.execute("""
            SELECT DISTINCT label FROM segment_revenue
            WHERE ticker = ? AND period_end = ? AND is_subtotal = 0
            ORDER BY label
        """, (r["ticker"], r["hi"]))]
        shown = ", ".join(latest)[:52]
        print(f"{r['ticker']:<8}{r['periods']:>8}{r['rows']:>7}{r['lo']:>13}{r['hi']:>13}  {shown}")

    missing = [r["ticker"] for r in conn.execute("""
        SELECT ticker FROM watchlist w WHERE supported = 1
          AND NOT EXISTS (SELECT 1 FROM segment_revenue s WHERE s.ticker = w.ticker)
        ORDER BY ticker
    """)]
    if missing:
        print(f"\nno product breakdown published: {', '.join(missing)}")

    print("\n=== reconciliation: leaf lines vs reported revenue ===")
    bad = 0
    for r in conn.execute("""
        SELECT s.ticker, s.period_end, SUM(s.value) parsed
        FROM segment_revenue s
        WHERE s.is_subtotal = 0
          AND s.filed_at = (SELECT MAX(filed_at) FROM segment_revenue x
                            WHERE x.ticker = s.ticker AND x.period_end = s.period_end)
        GROUP BY s.ticker, s.period_end ORDER BY s.ticker, s.period_end
    """):
        rev = conn.execute("""
            SELECT value FROM fundamentals
            WHERE ticker = ? AND concept = 'revenue'
              AND duration_days BETWEEN 340 AND 380
              AND ABS(julianday(period_end) - julianday(?)) <= 5
            ORDER BY filed_at DESC LIMIT 1
        """, (r["ticker"], r["period_end"])).fetchone()
        if not rev:
            continue
        diff = abs(r["parsed"] - rev["value"]) / rev["value"] * 100
        if diff > 1.0:
            bad += 1
            print(f"  MISMATCH {r['ticker']} {r['period_end']}: "
                  f"parsed {r['parsed']/1e9:.2f}B vs reported {rev['value']/1e9:.2f}B ({diff:.1f}%)")
    print(f"  {'all periods reconcile within 1%' if not bad else f'{bad} periods out of tolerance'}")

    print("\n=== growth: latest year by product ===")
    for ticker in ("GOOGL", "AAPL", "NVDA", "MSFT"):
        rows = conn.execute("""
            SELECT label, period_end, value FROM segment_revenue
            WHERE ticker = ? AND is_subtotal = 0
              AND filed_at = (SELECT MAX(filed_at) FROM segment_revenue WHERE ticker = ?)
            ORDER BY period_end
        """, (ticker, ticker)).fetchall()
        if not rows:
            continue
        periods = sorted({r["period_end"] for r in rows})
        if len(periods) < 2:
            continue
        prev, last = periods[-2], periods[-1]
        by = {}
        for r in rows:
            by.setdefault(r["label"], {})[r["period_end"]] = r["value"]
        print(f"\n  {ticker}  {prev} -> {last}")
        calc = []
        for label, vals in by.items():
            if prev in vals and last in vals and vals[prev]:
                calc.append((vals[last] / vals[prev] - 1, label, vals[last]))
        for growth, label, value in sorted(calc, reverse=True):
            arrow = "up  " if growth >= 0 else "DOWN"
            print(f"    {arrow} {label[:44]:44} {value/1e9:>9.1f}B  {growth*100:>+7.1f}%")

    conn.close()


if __name__ == "__main__":
    main()
