"""Eyeball check: latest derived ratios against reality."""

import db


def latest_snapshot(conn) -> None:
    print("=== latest derived ratios ===")
    print(f"{'TICK':<6}{'DATE':<12}{'CLOSE':>9}{'MKTCAP':>10}{'P/E':>8}{'EV/EBITDA':>11}"
          f"{'EV/S':>7}{'FCF YLD':>9}{'GM':>7}{'OPM':>7}{'REV YoY':>9}")
    print("-" * 95)
    for r in conn.execute("""
        SELECT r.* FROM ratios_daily r
        JOIN (SELECT ticker, MAX(date) d FROM ratios_daily GROUP BY ticker) m
          ON r.ticker = m.ticker AND r.date = m.d
        ORDER BY r.ticker
    """):
        def f(v, spec=".1f", scale=1.0):
            return format(v * scale, spec) if v is not None else "-"
        print(f"{r['ticker']:<6}{r['date']:<12}{f(r['close'],'.2f'):>9}"
              f"{f(r['market_cap'],'.2f',1e-12) + 'T' if r['market_cap'] else '-':>10}"
              f"{f(r['pe_ttm']):>8}{f(r['ev_ebitda']):>11}{f(r['ev_sales']):>7}"
              f"{f(r['fcf_yield'],'.2f',100):>9}{f(r['gross_margin'],'.1f',100):>7}"
              f"{f(r['operating_margin'],'.1f',100):>7}{f(r['revenue_growth_yoy'],'.1f',100):>9}")


def coverage_gaps(conn) -> None:
    print("\n=== metric coverage (% of rows non-null) ===")
    metrics = ["pe_ttm", "ev_ebitda", "ev_sales", "fcf_yield", "gross_margin",
               "operating_margin", "roic", "net_debt_ebitda", "revenue_growth_yoy"]
    print(f"{'TICK':<6}" + "".join(f"{m[:9]:>11}" for m in metrics))
    print("-" * (6 + 11 * len(metrics)))
    for t in [r["ticker"] for r in conn.execute(
            "SELECT DISTINCT ticker FROM ratios_daily ORDER BY ticker")]:
        total = conn.execute("SELECT COUNT(*) c FROM ratios_daily WHERE ticker=?", (t,)).fetchone()["c"]
        cells = []
        for m in metrics:
            n = conn.execute(
                f"SELECT COUNT({m}) c FROM ratios_daily WHERE ticker=?", (t,)).fetchone()["c"]
            cells.append(f"{100*n/total:>10.0f}%")
        print(f"{t:<6}" + "".join(cells))


def main() -> None:
    conn = db.connect()
    latest_snapshot(conn)
    coverage_gaps(conn)
    conn.close()


if __name__ == "__main__":
    main()
