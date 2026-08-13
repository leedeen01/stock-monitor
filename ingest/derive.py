"""Derive the daily ratio series that the UI reads.

Design note — why this is event-driven rather than day-driven:

Fundamentals only change when a company files, roughly 4-8 times a year. Prices
change daily. So instead of recomputing a company's financial state for each of
~11,000 trading days, we compute it once per filing date (~80 states over twenty
years) and then attach the most recent state to each day. Same answer, three
orders of magnitude less work.

The no-lookahead rule is enforced structurally: a state built for filing date F
is assembled only from rows with filed_at <= F, and is attached only to trading
days >= F. There is no path by which a later filing can influence an earlier
row. `fundamentals_filed_at` records the newest filing that fed each row so the
invariant is testable rather than merely intended.

Restatements are handled by taking, among rows visible at F, the one with the
latest filed_at for each (concept, period). That is what an investor standing on
date F would have seen: the most recent published version of that period.
"""

import argparse
import sqlite3
from datetime import date, timedelta

import db
import prices as prices_mod

# --- TTM assembly ---------------------------------------------------------

QUARTER_MIN, QUARTER_MAX = 80, 100
ANNUAL_MIN, ANNUAL_MAX = 340, 380

# Concepts summed over a trailing twelve months.
FLOW_CONCEPTS = (
    "revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income",
    "pretax_income", "income_tax", "interest_expense", "operating_cash_flow",
    "capex", "depreciation_amortization", "depreciation_expense",
    "amortisation_expense", "stock_based_compensation", "dividends_paid", "buybacks",
)

# Concepts taken as the latest reported balance.
STOCK_CONCEPTS = (
    "cash", "short_term_investments", "long_term_debt", "short_term_debt",
    "inventory", "total_assets", "total_equity", "current_assets", "current_liabilities",
)

# Share counts need split normalization before meeting a price. Ordered by
# preference — see the fallback chain in compute_ratios.
SHARE_CONCEPTS = ("shares_diluted", "shares_outstanding", "shares_outstanding_gaap")


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _visible(rows: list[dict], as_of: str) -> dict:
    """Latest published version of each (concept, period) as of a date."""
    best: dict[tuple, dict] = {}
    for row in rows:
        if row["filed_at"] > as_of:
            continue
        key = (row["concept"], row["period_start"], row["period_end"], row["duration_days"])
        current = best.get(key)
        if current is None or row["filed_at"] > current["filed_at"]:
            best[key] = row
    return best


def _quarters(visible: dict, concept: str) -> list[dict]:
    out = [
        r for (c, _s, _e, d), r in visible.items()
        if c == concept and d and QUARTER_MIN <= d <= QUARTER_MAX
    ]
    out.sort(key=lambda r: r["period_end"])
    return out


def _annuals(visible: dict, concept: str) -> list[dict]:
    out = [
        r for (c, _s, _e, d), r in visible.items()
        if c == concept and d and ANNUAL_MIN <= d <= ANNUAL_MAX
    ]
    out.sort(key=lambda r: r["period_end"])
    return out


def ttm(visible: dict, concept: str) -> tuple[float | None, str | None]:
    """Trailing twelve months for a flow concept.

    Prefers four contiguous quarters. Falls back to the latest annual figure,
    which is what a company reporting only annually leaves us — correct, just
    staler. Returns (value, filed_at of the newest input).
    """
    quarters = _quarters(visible, concept)
    if len(quarters) >= 4:
        last4 = quarters[-4:]
        span = _days(last4[0]["period_start"], last4[-1]["period_end"])
        if ANNUAL_MIN <= span <= ANNUAL_MAX:
            return (
                sum(q["value"] for q in last4),
                max(q["filed_at"] for q in last4),
            )

    annuals = _annuals(visible, concept)
    if annuals:
        return annuals[-1]["value"], annuals[-1]["filed_at"]

    # Four quarters that don't line up cleanly still beat nothing, provided
    # they cover roughly a year.
    if len(quarters) >= 4:
        last4 = quarters[-4:]
        span = _days(last4[0]["period_start"], last4[-1]["period_end"])
        if 300 <= span <= 420:
            return sum(q["value"] for q in last4), max(q["filed_at"] for q in last4)

    return None, None


def latest_instant(visible: dict, concept: str) -> tuple[float | None, str | None]:
    candidates = [r for (c, _s, _e, d), r in visible.items() if c == concept and not d]
    if not candidates:
        return None, None
    newest = max(candidates, key=lambda r: (r["period_end"], r["filed_at"]))
    return newest["value"], newest["filed_at"]


# --- state assembly -------------------------------------------------------

def build_state(rows: list[dict], as_of: str, splits: list[tuple[str, float]]) -> dict:
    """Everything knowable about a company's financials on a given date."""
    visible = _visible(rows, as_of)
    state: dict = {"as_of": as_of, "filed_at": None}
    # When a concept can be sourced two ways, the per-concept filing date is
    # what decides between them — see _add_derived.
    filed_per_concept: dict[str, str] = {}
    newest_filing = None

    for concept in FLOW_CONCEPTS:
        value, filed = ttm(visible, concept)
        state[concept] = value
        if filed:
            filed_per_concept[concept] = filed
            if newest_filing is None or filed > newest_filing:
                newest_filing = filed

    for concept in STOCK_CONCEPTS:
        value, filed = latest_instant(visible, concept)
        state[concept] = value
        if filed:
            filed_per_concept[concept] = filed
            if newest_filing is None or filed > newest_filing:
                newest_filing = filed

    # Share counts are as-filed; carry them through any later split so they
    # are expressed in the same units as the split-adjusted price series.
    for concept in SHARE_CONCEPTS:
        if concept == "shares_diluted":
            quarters = _quarters(visible, concept)
            annuals = _annuals(visible, concept)
            source = quarters[-1] if quarters else (annuals[-1] if annuals else None)
        else:
            candidates = [r for (c, _s, _e, d), r in visible.items() if c == concept and not d]
            source = max(candidates, key=lambda r: (r["period_end"], r["filed_at"])) if candidates else None

        if source is None:
            state[concept] = None
            continue
        factor = prices_mod.split_factor(splits, source["filed_at"])
        state[concept] = source["value"] * factor
        if newest_filing is None or source["filed_at"] > newest_filing:
            newest_filing = source["filed_at"]

    state["filed_at"] = newest_filing
    _add_derived(state, filed_per_concept)
    return state


def _add_derived(s: dict, filed: dict[str, str] | None = None) -> None:
    """Concepts with no direct tag, plus the fallbacks that keep whole sectors
    from dropping out."""
    filed = filed or {}

    # Alphabet, Meta and Chevron never tag GrossProfit.
    if s.get("gross_profit") is None and s.get("revenue") is not None and s.get("cost_of_revenue") is not None:
        s["gross_profit"] = s["revenue"] - s["cost_of_revenue"]

    # D&A comes from either a combined tag or separate depreciation and
    # amortization lines, and filers switch between them mid-history. Presence
    # alone can't decide: Intel's combined tag stops in 2019 but still resolves
    # to a stale $200m, while its current components sum to $11.7bn. Taking the
    # stale one turned EBITDA negative and suppressed EV/EBITDA entirely.
    #
    # So prefer whichever source was reported most recently.
    combined = s.get("depreciation_amortization")
    dep, amo = s.get("depreciation_expense"), s.get("amortisation_expense")
    components = (dep or 0) + (amo or 0) if (dep is not None or amo is not None) else None

    if combined is None:
        s["depreciation_amortization"] = components
    elif components is not None:
        combined_filed = filed.get("depreciation_amortization", "")
        component_filed = max(
            filed.get("depreciation_expense", ""), filed.get("amortisation_expense", "")
        )
        if component_filed > combined_filed:
            s["depreciation_amortization"] = components

    # EBITDA has no tag anywhere. Energy majors omit OperatingIncomeLoss, so
    # fall back to building EBIT from the bottom of the income statement.
    da = s.get("depreciation_amortization")
    if s.get("operating_income") is not None:
        s["ebit"] = s["operating_income"]
    elif s.get("pretax_income") is not None and s.get("interest_expense") is not None:
        s["ebit"] = s["pretax_income"] + s["interest_expense"]
    elif s.get("pretax_income") is not None:
        s["ebit"] = s["pretax_income"]
    else:
        s["ebit"] = None

    s["ebitda"] = (s["ebit"] + da) if (s["ebit"] is not None and da is not None) else None

    ocf, capex = s.get("operating_cash_flow"), s.get("capex")
    s["fcf"] = (ocf - capex) if (ocf is not None and capex is not None) else None

    # A debt-free company files no debt tags at all. Apple carried zero debt
    # until it began issuing bonds in 2013; NVIDIA likewise for years. Treating
    # that absence as "unknown" nulls out enterprise value and silently discards
    # precisely the early history the percentile bands depend on. So: if we have
    # a balance sheet for the period but no debt line, debt is zero.
    lt, st = s.get("long_term_debt"), s.get("short_term_debt")
    has_balance_sheet = s.get("total_assets") is not None or s.get("cash") is not None
    if lt is None and st is None:
        s["total_debt"] = 0.0 if has_balance_sheet else None
    else:
        s["total_debt"] = (lt or 0) + (st or 0)

    liquid = (s.get("cash") or 0) + (s.get("short_term_investments") or 0)
    s["net_debt"] = (s["total_debt"] - liquid) if s["total_debt"] is not None else None


# --- ratio computation ----------------------------------------------------

def _safe_div(a, b, guard_positive: bool = False):
    if a is None or b is None or b == 0:
        return None
    if guard_positive and b <= 0:
        return None
    return a / b


# Filed share counts carry scale errors in both directions, and the XBRL gives
# no hint that anything is wrong:
#
#   NVIDIA  FY2011 10-Q   590,997          where neighbours said 574,381,000
#   AMD     2012-08-02    707,555,106,000,000  where neighbours said ~707,555,106
#   Apple   2013-14       899,213          where neighbours said ~899,213,000
#
# Cross-checking sources against each other is not enough on its own: with only
# two candidates the median sits between them and cannot say which is wrong.
# What does work is temporal continuity — a company's share count moves by a few
# percent a quarter, never by 1000x. So the previously accepted value anchors
# each new one.
SHARE_MIN_PLAUSIBLE = 1e5
SHARE_MAX_PLAUSIBLE = 1e12
SHARE_DRIFT_TOLERANCE = 10  # quarter-on-quarter; generous enough for a big raise

SHARE_PREFERENCE = ("shares_diluted", "shares_outstanding", "shares_outstanding_gaap")


def reconcile_shares(
    candidates: dict[str, float | None],
    reference: float | None = None,
) -> float | None:
    """Pick a trustworthy share count, given the previous accepted value.

    `reference` is the last share count we believed. When every candidate is
    implausible against it, we hold the previous value rather than accept a
    scale error — a slightly stale count is wrong by a percent or two, whereas
    an unfiltered one is wrong by six orders of magnitude and poisons every
    multiple derived from market cap.
    """
    values = {
        k: v for k, v in candidates.items()
        if v and SHARE_MIN_PLAUSIBLE <= v <= SHARE_MAX_PLAUSIBLE
    }
    if not values:
        return reference

    if reference:
        anchored = {
            k: v for k, v in values.items()
            if 1 / SHARE_DRIFT_TOLERANCE <= v / reference <= SHARE_DRIFT_TOLERANCE
        }
        if not anchored:
            return reference
        values = anchored
    elif len(values) >= 3:
        # No temporal anchor yet, but three sources let the median arbitrate.
        ordered = sorted(values.values())
        median = ordered[len(ordered) // 2]
        values = {k: v for k, v in values.items() if median / 100 <= v <= median * 100} or values

    for source in SHARE_PREFERENCE:
        if source in values:
            return values[source]
    return max(values.values())


def compute_ratios(state: dict, close: float) -> dict:
    """Turn a financial state plus a price into the row the UI reads.

    `shares_final` is set by the chronological pass in derive_ticker, which is
    where the temporal anchor for share reconciliation lives.
    """
    shares = state.get("shares_final")
    market_cap = shares * close if shares else None
    net_debt = state.get("net_debt")
    ev = (market_cap + net_debt) if (market_cap is not None and net_debt is not None) else None
    # A company holding more net cash than its market cap has a negative EV.
    # That is economically real but useless as a multiple denominator — it would
    # sort as the cheapest stock on the page — so EV multiples are withheld.
    ev_multiple_base = ev if (ev is not None and ev > 0) else None

    revenue = state.get("revenue")
    net_income = state.get("net_income")
    fcf = state.get("fcf")
    ebitda = state.get("ebitda")

    return {
        "close": close,
        "market_cap": market_cap,
        "enterprise_value": ev,
        "shares_diluted": shares,
        # P/E is meaningless on negative earnings, so guard rather than emit a
        # negative multiple that would sort as "cheap".
        "pe_ttm": _safe_div(market_cap, net_income, guard_positive=True),
        "ps_ttm": _safe_div(market_cap, revenue, guard_positive=True),
        "pb": _safe_div(market_cap, state.get("total_equity"), guard_positive=True),
        "ev_ebitda": _safe_div(ev_multiple_base, ebitda, guard_positive=True),
        "ev_sales": _safe_div(ev_multiple_base, revenue, guard_positive=True),
        "ev_fcf": _safe_div(ev_multiple_base, fcf, guard_positive=True),
        "fcf_yield": _safe_div(fcf, market_cap),
        "earnings_yield": _safe_div(net_income, market_cap),
        "gross_margin": _safe_div(state.get("gross_profit"), revenue),
        "operating_margin": _safe_div(state.get("ebit"), revenue),
        "net_margin": _safe_div(net_income, revenue),
        "fcf_margin": _safe_div(fcf, revenue),
        "fcf_conversion": _safe_div(fcf, net_income, guard_positive=True),
        "roic": _roic(state),
        "roe": _safe_div(net_income, state.get("total_equity"), guard_positive=True),
        "net_debt": net_debt,
        "net_debt_ebitda": _safe_div(net_debt, ebitda, guard_positive=True),
        "interest_coverage": _safe_div(state.get("ebit"), state.get("interest_expense"), guard_positive=True),
        "revenue_ttm": revenue,
        "revenue_growth_yoy": None,  # filled in a second pass, needs history
        "inventory_days": _safe_div(
            state.get("inventory"),
            _safe_div(state.get("cost_of_revenue"), 365, guard_positive=True),
        ),
        "sbc_pct_revenue": _safe_div(state.get("stock_based_compensation"), revenue, guard_positive=True),
        "capex_pct_revenue": _safe_div(state.get("capex"), revenue, guard_positive=True),
        "fundamentals_filed_at": state.get("filed_at"),
    }


def _roic(s: dict):
    """NOPAT / invested capital. Capital-structure neutral, unlike ROE."""
    ebit, pretax, tax = s.get("ebit"), s.get("pretax_income"), s.get("income_tax")
    equity, debt, cash = s.get("total_equity"), s.get("total_debt"), s.get("cash")
    if ebit is None or equity is None or debt is None:
        return None
    tax_rate = 0.21
    if pretax and pretax > 0 and tax is not None:
        tax_rate = min(max(tax / pretax, 0.0), 0.5)
    invested = equity + debt - (cash or 0)
    return _safe_div(ebit * (1 - tax_rate), invested, guard_positive=True)


# --- orchestration --------------------------------------------------------

RATIO_COLUMNS = (
    "ticker", "date", "close", "market_cap", "enterprise_value", "shares_diluted",
    "pe_ttm", "ps_ttm", "pb", "ev_ebitda", "ev_sales", "ev_fcf", "fcf_yield",
    "earnings_yield", "gross_margin", "operating_margin", "net_margin", "fcf_margin",
    "fcf_conversion", "roic", "roe", "net_debt", "net_debt_ebitda", "interest_coverage",
    "revenue_ttm", "revenue_growth_yoy", "inventory_days", "sbc_pct_revenue",
    "capex_pct_revenue", "fundamentals_filed_at",
)


def derive_ticker(conn: sqlite3.Connection, ticker: str, verbose: bool = True) -> dict:
    ticker = ticker.upper()

    rows = [dict(r) for r in conn.execute(
        "SELECT concept, period_start, period_end, duration_days, filed_at, value "
        "FROM fundamentals WHERE ticker = ?", (ticker,)
    )]
    price_rows = [(r["date"], r["close"]) for r in conn.execute(
        "SELECT date, close FROM prices WHERE ticker = ? ORDER BY date", (ticker,)
    )]
    if not rows or not price_rows:
        raise ValueError(f"{ticker}: need both fundamentals and prices (have {len(rows)}/{len(price_rows)})")

    splits = prices_mod.load_splits(conn, ticker)

    # One state per filing date — the only dates on which the picture changes.
    filing_dates = sorted({r["filed_at"] for r in rows})
    states = [(f, build_state(rows, f, splits)) for f in filing_dates]

    # Share reconciliation needs chronological context, so it runs as a pass
    # over the state sequence rather than per-state.
    last_good = None
    for _, state in states:
        shares = reconcile_shares(
            {c: state.get(c) for c in SHARE_CONCEPTS}, reference=last_good
        )
        state["shares_final"] = shares
        if shares:
            last_good = shares

    earliest = states[0][0]
    out = []
    state_idx = 0
    for day, close in price_rows:
        if day < earliest:
            continue  # nothing was knowable yet
        while state_idx + 1 < len(states) and states[state_idx + 1][0] <= day:
            state_idx += 1
        ratios = compute_ratios(states[state_idx][1], close)
        out.append({"ticker": ticker, "date": day, **ratios})

    _fill_revenue_growth(out)

    conn.execute("DELETE FROM ratios_daily WHERE ticker = ?", (ticker,))
    conn.executemany(
        f"INSERT INTO ratios_daily ({', '.join(RATIO_COLUMNS)}) "
        f"VALUES ({', '.join(['?'] * len(RATIO_COLUMNS))})",
        [[row.get(c) for c in RATIO_COLUMNS] for row in out],
    )
    conn.commit()

    if verbose:
        with_pe = sum(1 for r in out if r["pe_ttm"] is not None)
        print(f"{ticker}: {len(out):,} daily rows ({out[0]['date']} -> {out[-1]['date']}), "
              f"{len(states)} filing states, {with_pe:,} with P/E")

    return {"ticker": ticker, "rows": len(out), "states": len(states)}


def _fill_revenue_growth(rows: list[dict]) -> None:
    """YoY growth of TTM revenue, comparing against ~365 days earlier.

    Offsets by timedelta rather than replacing the year, which blows up on
    Feb 29 — the prior year has no such date.
    """
    dates = [r["date"] for r in rows]
    for row in rows:
        target = (date.fromisoformat(row["date"]) - timedelta(days=365)).isoformat()
        idx = _bisect_left(dates, target)
        if idx >= len(dates):
            continue
        # Exact match if the market was open that day, else the session before.
        if dates[idx] != target:
            if idx == 0:
                continue
            idx -= 1
        prior = rows[idx]
        if prior["revenue_ttm"] and row["revenue_ttm"] and prior["revenue_ttm"] > 0:
            row["revenue_growth_yoy"] = row["revenue_ttm"] / prior["revenue_ttm"] - 1


def _bisect_left(seq: list[str], target: str) -> int:
    lo, hi = 0, len(seq)
    while lo < hi:
        mid = (lo + hi) // 2
        if seq[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def main() -> None:
    parser = argparse.ArgumentParser(description="Derive daily ratio series")
    parser.add_argument("tickers", nargs="*", help="Defaults to all supported watchlist tickers")
    args = parser.parse_args()

    conn = db.connect()
    db.init_schema(conn)
    tickers = [t.upper() for t in args.tickers] or [
        r["ticker"] for r in conn.execute(
            "SELECT ticker FROM watchlist WHERE supported = 1 ORDER BY ticker"
        )
    ]
    for ticker in tickers:
        try:
            derive_ticker(conn, ticker)
        except Exception as exc:  # noqa: BLE001 - report and continue the batch
            print(f"{ticker}: FAILED - {type(exc).__name__}: {exc}")
    conn.close()


if __name__ == "__main__":
    main()
