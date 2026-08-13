"""Ad-hoc inspection helper for validating the tag mapping by eye."""

import argparse

import db


def annual_series(conn, ticker: str, concept: str, limit: int = 8) -> None:
    print(f"\n--- {ticker} {concept} (FY, latest filing per period) ---")
    rows = conn.execute(
        """
        SELECT period_end, value, filed_at, form, accession
        FROM fundamentals
        WHERE ticker = ? AND concept = ? AND duration_days BETWEEN 340 AND 380
        GROUP BY period_end
        HAVING filed_at = MIN(filed_at)
        ORDER BY period_end DESC
        LIMIT ?
        """,
        (ticker.upper(), concept, limit),
    ).fetchall()
    for r in rows:
        print(f"  {r['period_end']}  {r['value']:>20,.0f}  filed {r['filed_at']} ({r['form']})")


def restatement_check(conn, ticker: str, concept: str, period_end: str) -> None:
    """Show every version of one fact. Split restatements show up here."""
    print(f"\n--- {ticker} {concept} for period {period_end}, ALL filed versions ---")
    rows = conn.execute(
        """
        SELECT value, filed_at, form, accession
        FROM fundamentals
        WHERE ticker = ? AND concept = ? AND period_end = ?
          AND duration_days BETWEEN 340 AND 380
        ORDER BY filed_at
        """,
        (ticker.upper(), concept, period_end),
    ).fetchall()
    for r in rows:
        print(f"  {r['value']:>20,.0f}  filed {r['filed_at']} ({r['form']}) {r['accession']}")
    if len(rows) > 1:
        lo, hi = rows[0]["value"], rows[-1]["value"]
        if lo and hi and abs(hi / lo - 1) > 0.05:
            print(f"  >> RESTATED: ratio {hi / lo:.4f} between first and last filing")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    args = parser.parse_args()
    conn = db.connect()

    for concept in ("revenue", "net_income", "operating_cash_flow", "capex", "shares_diluted"):
        annual_series(conn, args.ticker, concept)

    restatement_check(conn, args.ticker, "shares_diluted", "2019-09-28")
    restatement_check(conn, args.ticker, "eps_diluted", "2019-09-28")
    conn.close()


if __name__ == "__main__":
    main()
