"""Pipeline status: what's loaded, what's usable, what's stale."""

import db


def main() -> None:
    conn = db.connect()

    print(f"{'TICKER':<7} {'CCY':<5} {'OK':<3} {'FACTS':>7} {'PRICES':>7} {'RATIOS':>7}  COVERAGE")
    print("-" * 78)

    rows = conn.execute(
        """
        SELECT w.ticker, w.reporting_currency, w.supported, w.unsupported_reason,
               (SELECT COUNT(*) FROM fundamentals f WHERE f.ticker = w.ticker) AS facts,
               (SELECT COUNT(*) FROM prices p WHERE p.ticker = w.ticker) AS prices,
               (SELECT COUNT(*) FROM ratios_daily r WHERE r.ticker = w.ticker) AS ratios,
               (SELECT MIN(period_end) FROM fundamentals f WHERE f.ticker = w.ticker) AS first,
               (SELECT MAX(period_end) FROM fundamentals f WHERE f.ticker = w.ticker) AS last
        FROM watchlist w
        ORDER BY w.supported DESC, w.ticker
        """
    ).fetchall()

    for r in rows:
        flag = "yes" if r["supported"] else "NO"
        span = f"{r['first']} -> {r['last']}" if r["first"] else "-"
        print(
            f"{r['ticker']:<7} {str(r['reporting_currency'] or '?'):<5} {flag:<3} "
            f"{r['facts']:>7,} {r['prices']:>7,} {r['ratios']:>7,}  {span}"
        )

    blocked = [r for r in rows if not r["supported"]]
    if blocked:
        print("\nExcluded from ratio derivation:")
        for r in blocked:
            print(f"  {r['ticker']}: {r['unsupported_reason']}")

    conn.close()


if __name__ == "__main__":
    main()
