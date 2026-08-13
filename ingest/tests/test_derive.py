"""Correctness of the derived ratio series.

The tests that matter most here are the lookahead ones. Every other bug in this
project announces itself — a broken tag mapping shows up as a missing column, a
split error shows up as a market cap that is obviously 4x wrong. Lookahead bias
is different: it produces a series that looks entirely reasonable and is
flattering in a consistent direction. It has to be tested, because it cannot be
noticed.
"""

import pytest

import db


@pytest.fixture(scope="module")
def conn():
    c = db.connect()
    yield c
    c.close()


@pytest.fixture(scope="module")
def tickers(conn):
    return [r["ticker"] for r in conn.execute(
        "SELECT DISTINCT ticker FROM ratios_daily ORDER BY ticker")]


# --- the no-lookahead invariant ------------------------------------------

def test_no_row_uses_a_filing_from_its_own_future(conn, tickers):
    """The core invariant: nothing in a row may postdate the row."""
    assert tickers, "no derived data to test"
    violations = conn.execute("""
        SELECT ticker, date, fundamentals_filed_at
        FROM ratios_daily
        WHERE fundamentals_filed_at IS NOT NULL
          AND fundamentals_filed_at > date
        ORDER BY ticker, date
        LIMIT 10
    """).fetchall()
    assert not violations, (
        "rows built from filings that had not happened yet:\n"
        + "\n".join(f"  {v['ticker']} {v['date']} used a filing from {v['fundamentals_filed_at']}"
                    for v in violations)
    )


def test_fundamentals_only_change_on_filing_dates(conn):
    """TTM revenue is a step function. If it drifts between filings, something
    is interpolating data that did not exist yet."""
    rows = conn.execute("""
        SELECT date, revenue_ttm, fundamentals_filed_at
        FROM ratios_daily WHERE ticker = 'AAPL' AND revenue_ttm IS NOT NULL
        ORDER BY date
    """).fetchall()
    assert len(rows) > 500

    changes = 0
    for prev, curr in zip(rows, rows[1:]):
        if prev["revenue_ttm"] != curr["revenue_ttm"]:
            changes += 1
            assert prev["fundamentals_filed_at"] != curr["fundamentals_filed_at"], (
                f"revenue_ttm changed on {curr['date']} without a new filing"
            )
    # Roughly four filings a year over ~17 years.
    assert 40 < changes < 120, f"expected ~70 revenue changes, saw {changes}"


def test_ttm_revenue_appears_only_after_the_10k_is_filed(conn):
    """Apple's FY2024 revenue was $391.035bn, filed 2024-11-01. It must be
    absent the day before and present the day after — this is the lookahead
    boundary made concrete."""
    fy2024 = 391_035_000_000

    before = conn.execute("""
        SELECT revenue_ttm FROM ratios_daily
        WHERE ticker='AAPL' AND date < '2024-11-01' ORDER BY date DESC LIMIT 1
    """).fetchone()["revenue_ttm"]
    after = conn.execute("""
        SELECT revenue_ttm FROM ratios_daily
        WHERE ticker='AAPL' AND date >= '2024-11-01' ORDER BY date LIMIT 1
    """).fetchone()["revenue_ttm"]

    assert after == pytest.approx(fy2024, rel=1e-6), (
        f"TTM revenue just after the FY2024 10-K should be {fy2024:,}, got {after:,.0f}"
    )
    assert before != pytest.approx(fy2024, rel=1e-6), (
        "FY2024 revenue was visible before Apple filed it — lookahead bias"
    )


# --- external anchors ----------------------------------------------------

@pytest.mark.parametrize("date_str,expected_trillions,label", [
    ("2018-08-02", 1.0, "Apple first closed above $1T"),
    ("2020-08-19", 2.0, "Apple first closed above $2T"),
])
def test_market_cap_matches_known_milestones(conn, date_str, expected_trillions, label):
    """Independently verifiable checkpoints. If share normalization or price
    adjustment were wrong, these would miss by a wide margin rather than a few
    percent."""
    row = conn.execute("""
        SELECT date, market_cap FROM ratios_daily
        WHERE ticker='AAPL' AND date >= ? AND market_cap IS NOT NULL
        ORDER BY date LIMIT 1
    """, (date_str,)).fetchone()
    actual = row["market_cap"] / 1e12
    assert actual == pytest.approx(expected_trillions, rel=0.05), (
        f"{label}: expected ~${expected_trillions}T on {date_str}, got ${actual:.2f}T"
    )


def test_ratios_are_internally_consistent(conn, tickers):
    """P/E should equal market cap over TTM net income, reachable a second way
    via the earnings yield. Guards against a column being written to the wrong
    slot."""
    rows = conn.execute("""
        SELECT ticker, date, pe_ttm, earnings_yield FROM ratios_daily
        WHERE pe_ttm IS NOT NULL AND earnings_yield IS NOT NULL AND earnings_yield > 0
        ORDER BY RANDOM() LIMIT 200
    """).fetchall()
    assert rows
    for r in rows:
        assert r["pe_ttm"] == pytest.approx(1 / r["earnings_yield"], rel=1e-6), (
            f"{r['ticker']} {r['date']}: P/E {r['pe_ttm']:.2f} inconsistent with "
            f"earnings yield {r['earnings_yield']:.4f}"
        )


def test_enterprise_value_equals_market_cap_plus_net_debt(conn):
    rows = conn.execute("""
        SELECT ticker, date, enterprise_value, market_cap, net_debt FROM ratios_daily
        WHERE enterprise_value IS NOT NULL AND market_cap IS NOT NULL AND net_debt IS NOT NULL
        ORDER BY RANDOM() LIMIT 200
    """).fetchall()
    assert rows
    for r in rows:
        assert r["enterprise_value"] == pytest.approx(r["market_cap"] + r["net_debt"], rel=1e-9)


def test_no_negative_multiples_leak_through(conn):
    """A negative P/E sorts as 'cheapest' and is meaningless. Loss-making
    periods must be null, not negative."""
    for column in ("pe_ttm", "ev_ebitda", "ev_sales", "ps_ttm", "ev_fcf"):
        bad = conn.execute(
            f"SELECT COUNT(*) c FROM ratios_daily WHERE {column} < 0").fetchone()["c"]
        assert bad == 0, f"{bad} negative values in {column}"


def test_margins_are_plausible(conn, tickers):
    """Catches unit errors — a margin above 100% or below -500% means revenue
    and the numerator are on different scales."""
    bad = conn.execute("""
        SELECT ticker, date, gross_margin, operating_margin FROM ratios_daily
        WHERE gross_margin > 1.0 OR gross_margin < -1.0
           OR operating_margin > 1.0 OR operating_margin < -5.0
        LIMIT 5
    """).fetchall()
    assert not bad, "implausible margins: " + ", ".join(
        f"{r['ticker']} {r['date']} gm={r['gross_margin']} om={r['operating_margin']}" for r in bad)


def test_reconcile_shares_discards_thousands_scaled_values():
    """NVIDIA's FY2011 10-Q reported 590,997 diluted shares where neighbouring
    filings said ~574,381,000 — the same figure in thousands, tagged with the
    same unit. The odd one out must be discarded, not averaged in."""
    from derive import reconcile_shares

    assert reconcile_shares({
        "shares_diluted": 23_639_880,       # the thousands-scaled value
        "shares_outstanding": 22_886_735_080,
        "shares_outstanding_gaap": 22_458_634_040,
    }) == pytest.approx(22_886_735_080)


def test_reconcile_shares_prefers_diluted_when_sources_agree():
    from derive import reconcile_shares

    assert reconcile_shares({
        "shares_diluted": 15_400_000_000,
        "shares_outstanding": 15_100_000_000,
        "shares_outstanding_gaap": None,
    }) == 15_400_000_000


def test_reconcile_shares_handles_a_single_source():
    from derive import reconcile_shares

    assert reconcile_shares({"shares_diluted": 1_000_000, "shares_outstanding": None}) == 1_000_000
    assert reconcile_shares({"shares_diluted": None}) is None


def test_reconcile_shares_rejects_a_million_fold_overstatement():
    """AMD filed 707,555,106,000,000 shares on 2012-08-02 where the previous
    quarter said ~701 million — six extra digits, no indication of error."""
    from derive import reconcile_shares

    assert reconcile_shares(
        {"shares_outstanding": 707_555_106_000_000, "shares_outstanding_gaap": 707_000_000},
        reference=701_348_804,
    ) == 707_000_000


def test_reconcile_shares_holds_last_good_when_every_source_is_implausible():
    """A slightly stale share count is wrong by a percent. An unfiltered one is
    wrong by a factor of a thousand."""
    from derive import reconcile_shares

    assert reconcile_shares({"shares_diluted": 590_997}, reference=574_381_000) == 574_381_000


def test_reconcile_shares_allows_genuine_growth():
    """A real secondary offering must not be mistaken for a scale error."""
    from derive import reconcile_shares

    assert reconcile_shares({"shares_diluted": 1_400_000_000}, reference=1_000_000_000) \
        == 1_400_000_000


def test_stale_combined_da_loses_to_current_components():
    """Intel's combined D&A tag stops in 2019 but still resolves to a stale
    $200m; its current depreciation and amortization lines sum to $11.7bn.
    Preferring the stale tag turned EBITDA negative and suppressed EV/EBITDA
    for the whole ticker."""
    from derive import _add_derived

    state = {
        "operating_income": -2_214_000_000,
        "depreciation_amortization": 200_000_000,
        "depreciation_expense": 10_757_000_000,
        "amortisation_expense": 949_000_000,
    }
    _add_derived(state, {
        "depreciation_amortization": "2020-01-24",
        "depreciation_expense": "2026-07-24",
        "amortisation_expense": "2026-07-24",
    })
    assert state["depreciation_amortization"] == 11_706_000_000
    assert state["ebitda"] == pytest.approx(9_492_000_000)


def test_current_combined_da_beats_components():
    """The reverse case must still hold — a filer reporting a current combined
    line should keep it rather than have components substituted in."""
    from derive import _add_derived

    state = {
        "operating_income": 1_000_000_000,
        "depreciation_amortization": 500_000_000,
        "depreciation_expense": 120_000_000,
        "amortisation_expense": None,
    }
    _add_derived(state, {
        "depreciation_amortization": "2026-07-24",
        "depreciation_expense": "2026-07-24",
    })
    assert state["depreciation_amortization"] == 500_000_000


def test_da_falls_back_when_combined_absent():
    from derive import _add_derived

    state = {
        "operating_income": 1_000_000_000,
        "depreciation_amortization": None,
        "depreciation_expense": 300_000_000,
        "amortisation_expense": 200_000_000,
    }
    _add_derived(state, {})
    assert state["depreciation_amortization"] == 500_000_000
    assert state["ebitda"] == 1_500_000_000


def test_market_cap_has_no_scale_cliffs(conn, tickers):
    """Market cap moves with price, so day-to-day it changes by percent, not
    orders of magnitude. A cliff means a share count changed scale — the exact
    failure that made NVIDIA look like a $10M company in 2010."""
    for ticker in tickers:
        rows = conn.execute("""
            SELECT date, market_cap FROM ratios_daily
            WHERE ticker = ? AND market_cap IS NOT NULL ORDER BY date
        """, (ticker,)).fetchall()
        for prev, curr in zip(rows, rows[1:]):
            ratio = curr["market_cap"] / prev["market_cap"]
            assert 0.5 < ratio < 2.0, (
                f"{ticker}: market cap jumped {ratio:.1f}x from {prev['date']} "
                f"(${prev['market_cap']/1e9:.2f}B) to {curr['date']} "
                f"(${curr['market_cap']/1e9:.2f}B)"
            )


def test_every_ticker_has_a_recent_row(conn, tickers):
    """A silently stale ticker is worse than a missing one."""
    for ticker in tickers:
        last = conn.execute(
            "SELECT MAX(date) d FROM ratios_daily WHERE ticker = ?", (ticker,)).fetchone()["d"]
        assert last >= "2026-08-01", f"{ticker} has no data since {last}"
