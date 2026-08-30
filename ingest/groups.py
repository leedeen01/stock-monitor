"""Seed groups — metric profiles, not tags.

Each group answers "which numbers matter for this kind of business", and its
membership doubles as the peer set for relative comparison. The differences
between these four are the point: applying one generic metric set to all of them
is what makes most free stock screeners misleading.

The sharpest case is semiconductors. P/E is at its LOWEST near a cycle peak,
because earnings peak there — exactly when risk is highest. So the semi profile
leads on EV/EBITDA and carries inventory days, which turns over before revenue
does.
"""

import argparse
from datetime import datetime, timezone

import db
import metrics
from metrics import GETTING, INTEGRITY, LEVERAGE, PAYING

SEED_GROUPS = [
    # --- SGX ---------------------------------------------------------------
    # Separate profiles because the questions differ. A REIT is judged on
    # distribution and gearing, a bank on book value and return on equity, and
    # neither has a meaningful gross margin. Metrics with nothing behind them
    # are hidden rather than shown empty, so a sparse profile costs nothing.
    {
        "name": "REITs",
        "market": "SGX",
        "primary_multiple": "dividend_yield",
        "description": (
            "Bought for the distribution, undone by the gearing. Yield leads, but "
            "read it beside interest coverage and Net Debt/EBITDA: a payout funded "
            "by borrowing lasts exactly as long as the borrowing does. P/B matters "
            "because a REIT is a pile of buildings — earnings multiples do not "
            "describe it."
        ),
        "metrics": {
            PAYING: ["dividend_yield", "pb", "fcf_yield"],
            GETTING: ["revenue_growth_yoy", "net_margin", "roe"],
            LEVERAGE: ["net_debt_ebitda", "interest_coverage", "net_debt"],
        },
    },
    {
        "name": "Banks",
        "market": "SGX",
        "primary_multiple": "pb",
        "description": (
            "P/B leads, because for a bank book value approximates what it owns and "
            "below 1.0 means the market doubts the stated asset values. ROE is the "
            "industry's own yardstick and the one place this app's usual preference "
            "for ROIC does not apply. Gross and operating margin are absent by "
            "nature, not by omission."
        ),
        "metrics": {
            PAYING: ["pb", "pe_ttm", "dividend_yield"],
            GETTING: ["roe", "net_margin", "revenue_growth_yoy"],
        },
    },
    {
        "name": "Dividend",
        "market": "SGX",
        "primary_multiple": "dividend_yield",
        "description": (
            "Income names outside the REIT structure. The trap is the same: a yield "
            "that rose because the price fell is not the same as one that rose "
            "because the payout grew, and only cash conversion tells you which."
        ),
        "metrics": {
            PAYING: ["dividend_yield", "fcf_yield", "pe_ttm"],
            GETTING: ["revenue_growth_yoy", "fcf_margin", "fcf_conversion", "roe"],
            LEVERAGE: ["net_debt_ebitda", "interest_coverage"],
        },
    },
    {
        "name": "Others",
        "market": "SGX",
        "primary_multiple": "pe_ttm",
        "description": (
            "Everything else on SGX. A general profile, deliberately broad — "
            "fundamentals here come from yfinance with roughly five annual periods, "
            "so percentile bands are shorter than their US equivalents and say so."
        ),
        "metrics": {
            PAYING: ["pe_ttm", "ev_ebitda", "pb", "dividend_yield"],
            GETTING: ["revenue_growth_yoy", "operating_margin", "net_margin", "roe"],
            LEVERAGE: ["net_debt_ebitda", "interest_coverage"],
        },
    },
    {
        "name": "ETFs & Funds",
        "market": "US",
        "primary_multiple": "pe_ttm",
        "description": (
            "Priced, not valued. An index fund has no income statement, so P/E, "
            "margins and every percentile in this app are not missing for it — "
            "they do not exist. What is left is real: price, and what you paid. "
            "Metrics with no data behind them are hidden rather than shown as "
            "dashes."
        ),
        "metrics": {
            PAYING: ["pe_ttm"],
            GETTING: ["revenue_growth_yoy"],
        },
    },
    {
        "name": "Big Tech",
        "market": "US",
        "primary_multiple": "pe_ttm",
        "description": (
            "Mature, profitable, net cash, buyback-heavy. Earnings are reliable so P/E "
            "leads. The thing to actually watch is dilution — stock comp quietly erodes "
            "per-share value even while headline growth looks fine."
        ),
        "metrics": {
            PAYING: ["pe_ttm", "ev_ebitda", "ev_fcf", "fcf_yield"],
            GETTING: ["revenue_growth_yoy", "gross_margin", "operating_margin",
                      "fcf_margin", "roic", "fcf_conversion"],
            INTEGRITY: ["shares_diluted", "sbc_pct_revenue"],
        },
    },
    {
        "name": "AI / High Growth",
        "market": "US",
        "primary_multiple": "ev_sales",
        "description": (
            "Often unprofitable, so P/E and PEG are dropped entirely — they would be "
            "null or nonsense. EV/Sales leads. Gross margin is the tell that separates "
            "real software economics from reselling someone else's hardware."
        ),
        "metrics": {
            PAYING: ["ev_sales", "ps_ttm", "fcf_yield"],
            GETTING: ["revenue_growth_yoy", "gross_margin", "operating_margin", "fcf_margin"],
            INTEGRITY: ["shares_diluted", "sbc_pct_revenue"],
        },
    },
    {
        "name": "Semiconductors",
        "market": "US",
        "primary_multiple": "ev_ebitda",
        "description": (
            "Cyclical and capital intensive — the group where standard metrics mislead "
            "most. P/E bottoms at the cycle peak because earnings peak there, so it reads "
            "'cheap' at the point of maximum risk. EV/EBITDA leads instead, and inventory "
            "days is the early warning: inventory building faster than revenue turns "
            "before the downturn shows up in earnings."
        ),
        "metrics": {
            PAYING: ["ev_ebitda", "pe_ttm", "ev_sales", "fcf_yield"],
            GETTING: ["revenue_growth_yoy", "gross_margin", "operating_margin",
                      "roic", "inventory_days"],
            INTEGRITY: ["shares_diluted", "sbc_pct_revenue"],
            LEVERAGE: ["net_debt_ebitda", "capex_pct_revenue", "interest_coverage"],
        },
    },
    {
        "name": "Energy",
        "market": "US",
        "primary_multiple": "ev_ebitda",
        "description": (
            "Commodity-driven, so the thesis is cash return rather than growth. Revenue "
            "growth is dropped — it mostly just tracks the oil price and tells you nothing "
            "about the business. FCF yield and balance-sheet strength carry the weight, "
            "because surviving the trough is what separates these names."
        ),
        "metrics": {
            PAYING: ["ev_ebitda", "ev_fcf", "fcf_yield", "pe_ttm"],
            GETTING: ["operating_margin", "roic", "revenue_growth_yoy"],
            INTEGRITY: ["shares_diluted"],
            LEVERAGE: ["net_debt_ebitda", "capex_pct_revenue", "interest_coverage"],
        },
    },
]

# Which seed group each backfilled ticker belongs to. A ticker may appear in
# more than one — AVGO is both a semiconductor and, increasingly, an AI name —
# and the deep dive lets you toggle between the two readings.
SEED_MEMBERSHIP = {
    "AAPL": ["Big Tech"],
    "MSFT": ["Big Tech", "AI / High Growth"],
    "GOOGL": ["Big Tech", "AI / High Growth"],
    "META": ["Big Tech"],
    "NVDA": ["Semiconductors", "AI / High Growth"],
    "AMD": ["Semiconductors"],
    "AVGO": ["Semiconductors", "AI / High Growth"],
    "PLTR": ["AI / High Growth"],
    "XOM": ["Energy"],
    "CVX": ["Energy"],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def seed(conn, user_id: int | None = None, reset: bool = False,
         verbose: bool = True) -> None:
    """Create one user's metric profiles.

    Groups are per-user because people add their own; the defaults here are
    a starting point, not a fixed taxonomy. Passing user_id=None seeds the
    ownerless rows an older single-user install left behind."""
    if reset:
        conn.execute(
            "DELETE FROM group_metrics WHERE group_id IN "
            "(SELECT id FROM metric_groups WHERE user_id IS ?)", (user_id,))
        conn.execute("DELETE FROM stock_groups WHERE user_id IS ?", (user_id,))
        conn.execute("DELETE FROM metric_groups WHERE user_id IS ?", (user_id,))
        conn.commit()

    for spec in SEED_GROUPS:
        conn.execute(
            """
            INSERT INTO metric_groups (user_id, name, market, primary_multiple, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, name) DO UPDATE SET
                primary_multiple = excluded.primary_multiple,
                description = excluded.description
            """,
            (user_id, spec["name"], spec.get("market", "US"),
             spec["primary_multiple"], spec["description"], _now()),
        )
        group_id = conn.execute(
            "SELECT id FROM metric_groups WHERE name = ? AND user_id IS ?",
            (spec["name"], user_id)
        ).fetchone()["id"]

        conn.execute("DELETE FROM group_metrics WHERE group_id = ?", (group_id,))
        order = 0
        for section, keys in spec["metrics"].items():
            for key in keys:
                if key not in metrics.BY_KEY:
                    raise KeyError(f"{spec['name']}: unknown metric '{key}'")
                conn.execute(
                    "INSERT INTO group_metrics (group_id, metric_key, section, sort_order) "
                    "VALUES (?, ?, ?, ?)",
                    (group_id, key, section, order),
                )
                order += 1

    # Membership, and a default group for tickers in more than one.
    for ticker, group_names in SEED_MEMBERSHIP.items():
        exists = conn.execute(
            "SELECT 1 FROM watchlist WHERE ticker = ? AND user_id IS ?",
            (ticker, user_id)).fetchone()
        if not exists:
            continue
        for name in group_names:
            row = conn.execute(
                "SELECT id FROM metric_groups WHERE name = ? AND user_id IS ?",
                (name, user_id)).fetchone()
            conn.execute(
                "INSERT OR IGNORE INTO stock_groups (ticker, group_id, user_id) "
                "VALUES (?, ?, ?)",
                (ticker, row["id"], user_id),
            )
        default_id = conn.execute(
            "SELECT id FROM metric_groups WHERE name = ? AND user_id IS ?",
            (group_names[0], user_id)).fetchone()["id"]
        conn.execute(
            "UPDATE watchlist SET default_group_id = ? WHERE ticker = ? AND user_id IS ?",
            (default_id, ticker, user_id))

    # Seeded tickers were added today, so the price on the latest derived day is
    # genuinely their add price. Only fills where unset — never overwrites a real
    # add price, which would silently rewrite the since-added return.
    conn.execute(
        """
        UPDATE watchlist SET added_price = (
            SELECT close FROM ratios_daily r
            WHERE r.ticker = watchlist.ticker ORDER BY r.date DESC LIMIT 1
        )
        WHERE added_price IS NULL AND user_id IS ?
          AND EXISTS (SELECT 1 FROM ratios_daily r WHERE r.ticker = watchlist.ticker)
        """, (user_id,)
    )
    conn.commit()

    if verbose:
        for row in conn.execute("""
            SELECT g.name, g.primary_multiple,
                   (SELECT COUNT(*) FROM group_metrics gm WHERE gm.group_id = g.id) AS n_metrics,
                   (SELECT GROUP_CONCAT(ticker, ' ') FROM stock_groups sg
                    WHERE sg.group_id = g.id) AS members
            FROM metric_groups g ORDER BY g.id
        """):
            print(f"{row['name']:<18} primary={row['primary_multiple']:<10} "
                  f"{row['n_metrics']:>2} metrics  [{row['members'] or 'no members'}]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed metric groups")
    parser.add_argument("--reset", action="store_true", help="Wipe groups before seeding")
    parser.add_argument("--user-id", type=int, default=None,
                        help="Seed for this user; omit for ownerless rows")
    args = parser.parse_args()

    conn = db.connect()
    db.init_schema(conn)
    seed(conn, user_id=args.user_id, reset=args.reset)

    from pathlib import Path
    out = Path(__file__).resolve().parent.parent / "web" / "lib" / "metrics.json"
    metrics.export_json(out)
    print(f"\nexported metric registry -> {out}")
    conn.close()


if __name__ == "__main__":
    main()
