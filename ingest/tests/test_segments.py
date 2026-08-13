"""Revenue segmentation correctness.

Scraping a presentation layer is only safe because of the reconciliation
invariant: every stored period's leaf lines must add up to the revenue already
ingested from XBRL. That single check catches the wrong table, the wrong scale,
a subtotal counted as a leaf, and a misread column — each of which otherwise
produces numbers that look completely reasonable.
"""

import pytest

import db
from segments import _split_members, _to_number, mark_subtotals


@pytest.fixture(scope="module")
def conn():
    c = db.connect()
    yield c
    c.close()


@pytest.fixture(scope="module")
def tickers(conn):
    return [r["ticker"] for r in conn.execute(
        "SELECT DISTINCT ticker FROM segment_revenue ORDER BY ticker")]


# --- the invariant --------------------------------------------------------

def test_every_stored_period_reconciles_to_reported_revenue(conn, tickers):
    assert tickers, "no segment data ingested"
    failures = []

    for row in conn.execute("""
        SELECT s.ticker, s.period_end, SUM(s.value) AS parsed
        FROM segment_revenue s
        WHERE s.is_subtotal = 0
          AND s.filed_at = (SELECT MAX(filed_at) FROM segment_revenue x
                            WHERE x.ticker = s.ticker AND x.period_end = s.period_end)
        GROUP BY s.ticker, s.period_end
    """):
        reported = conn.execute("""
            SELECT value FROM fundamentals
            WHERE ticker = ? AND concept = 'revenue'
              AND duration_days BETWEEN 340 AND 380
              AND ABS(julianday(period_end) - julianday(?)) <= 5
            ORDER BY filed_at DESC LIMIT 1
        """, (row["ticker"], row["period_end"])).fetchone()
        if not reported:
            continue
        drift = abs(row["parsed"] - reported["value"]) / reported["value"]
        if drift > 0.01:
            failures.append(
                f"{row['ticker']} {row['period_end']}: segments sum to "
                f"{row['parsed']/1e9:.2f}B, revenue is {reported['value']/1e9:.2f}B "
                f"({drift*100:.1f}% off)")

    assert not failures, "segment lines do not add up:\n" + "\n".join(failures)


def test_no_leaf_row_is_a_total(conn, tickers):
    """A row named 'Total' counted as a leaf would double the whole breakdown."""
    bad = conn.execute("""
        SELECT ticker, label FROM segment_revenue
        WHERE is_subtotal = 0 AND label LIKE 'Total%' LIMIT 5
    """).fetchall()
    assert not bad, [f"{r['ticker']}: {r['label']}" for r in bad]


def test_no_taxonomy_boilerplate_leaked_through(conn, tickers):
    """Members like 'Segment Reporting Information' are XBRL scaffolding, never
    a product line a person would recognise."""
    bad = conn.execute("""
        SELECT DISTINCT ticker, label FROM segment_revenue
        WHERE label LIKE 'Segment Reporting Information%'
           OR label LIKE '%Concentration Risk%'
           OR label LIKE 'Reportable Segment,%'
        LIMIT 5
    """).fetchall()
    assert not bad, [f"{r['ticker']}: {r['label']}" for r in bad]


@pytest.mark.parametrize("ticker,expected", [
    ("GOOGL", "YouTube"),
    ("AAPL", "iPhone"),
    ("NVDA", "Gaming"),
    ("MSFT", "LinkedIn"),
])
def test_expected_product_lines_present(conn, ticker, expected):
    """Catches a wrong-but-parseable table: a geographic split would reconcile
    perfectly while containing none of these."""
    hit = conn.execute(
        "SELECT 1 FROM segment_revenue WHERE ticker = ? AND label LIKE ? LIMIT 1",
        (ticker, f"%{expected}%"),
    ).fetchone()
    assert hit, f"{ticker} has no line matching {expected!r}"


def test_values_are_plausible_revenue_scale(conn, tickers):
    """A missed '$ in Millions' header is a silent 1000x error. No line in this
    watchlist is under $1m or over $1t."""
    bad = conn.execute("""
        SELECT ticker, label, period_end, value FROM segment_revenue
        WHERE ABS(value) > 0 AND (ABS(value) < 1e6 OR ABS(value) > 1e12) LIMIT 5
    """).fetchall()
    assert not bad, [
        f"{r['ticker']} {r['label']} {r['period_end']}: {r['value']:,.0f}" for r in bad]


# --- hierarchy ------------------------------------------------------------

def test_subtotal_detected_when_it_follows_its_parts():
    """Alphabet lists 'Google advertising' after the three lines it sums."""
    lines = [
        {"label": "Google Search & other", "parent": "Google Services", "period_end": "2025-12-31", "value": 224_532e6},
        {"label": "YouTube ads", "parent": "Google Services", "period_end": "2025-12-31", "value": 40_367e6},
        {"label": "Google Network", "parent": "Google Services", "period_end": "2025-12-31", "value": 29_792e6},
        {"label": "Google advertising", "parent": "Google Services", "period_end": "2025-12-31", "value": 294_691e6},
        {"label": "Google subscriptions", "parent": "Google Services", "period_end": "2025-12-31", "value": 48_030e6},
    ]
    mark_subtotals(lines)
    flagged = {l["label"] for l in lines if l["is_subtotal"]}
    assert flagged == {"Google advertising"}


def test_subtotal_detected_when_it_precedes_its_parts():
    """NVIDIA lists 'Data Center' before Compute and Networking, with no pipe
    notation to hint that it aggregates them."""
    lines = [
        {"label": "Data Center", "parent": None, "period_end": "2026-01-25", "value": 193_737e6},
        {"label": "Compute", "parent": None, "period_end": "2026-01-25", "value": 162_361e6},
        {"label": "Networking", "parent": None, "period_end": "2026-01-25", "value": 31_376e6},
        {"label": "Gaming", "parent": None, "period_end": "2026-01-25", "value": 16_042e6},
    ]
    mark_subtotals(lines)
    assert {l["label"] for l in lines if l["is_subtotal"]} == {"Data Center"}


def test_near_miss_is_not_treated_as_a_subtotal():
    """Netflix's UCAN lands within 0.43% of EMEA + LATAM by coincidence. A loose
    tolerance flagged it as their subtotal and collapsed the breakdown."""
    lines = [
        {"label": "UCAN", "parent": "Streaming", "period_end": "2025-12-31", "value": 19_957_152e3},
        {"label": "EMEA", "parent": "Streaming", "period_end": "2025-12-31", "value": 14_514_646e3},
        {"label": "LATAM", "parent": "Streaming", "period_end": "2025-12-31", "value": 5_357_521e3},
        {"label": "APAC", "parent": "Streaming", "period_end": "2025-12-31", "value": 5_353_717e3},
    ]
    mark_subtotals(lines)
    assert not any(l["is_subtotal"] for l in lines)


def test_parent_with_children_is_a_subtotal():
    lines = [
        {"label": "Google Services", "parent": None, "period_end": "2025-12-31", "value": 342_721e6},
        {"label": "YouTube ads", "parent": "Google Services", "period_end": "2025-12-31", "value": 40_367e6},
        {"label": "Google Cloud", "parent": None, "period_end": "2025-12-31", "value": 58_705e6},
    ]
    mark_subtotals(lines)
    assert [l["is_subtotal"] for l in lines] == [True, False, False]


# --- member parsing -------------------------------------------------------

def test_pipe_orientation_is_read_from_the_data():
    """The order flips between filings — Alphabet's 2026 10-K writes
    "child | parent" and its 2023 10-K writes "parent | child". Assuming either
    mislabels every row in half the filings."""
    child_first = [
        {"member": "YouTube ads | Google Services", "period_end": "p", "value": 1},
        {"member": "Google Network | Google Services", "period_end": "p", "value": 2},
    ]
    _split_members(child_first)
    assert [l["label"] for l in child_first] == ["YouTube ads", "Google Network"]
    assert {l["parent"] for l in child_first} == {"Google Services"}

    parent_first = [
        {"member": "Google Services | YouTube ads", "period_end": "p", "value": 1},
        {"member": "Google Services | Google Network", "period_end": "p", "value": 2},
    ]
    _split_members(parent_first)
    assert [l["label"] for l in parent_first] == ["YouTube ads", "Google Network"]
    assert {l["parent"] for l in parent_first} == {"Google Services"}


def test_duplicate_label_in_a_period_is_collapsed():
    """One table can name the same member twice — Palantir's lists each region
    once in dollars and again as a concentration percentage.

    The storage key is label-based, so the second write silently replaced the
    first while reconciliation had counted both. That let a filing pass the
    check and still store numbers that don't add up. The larger value wins,
    being revenue rather than a share of it.
    """
    lines = [
        {"member": "United States", "period_end": "2024-12-31", "value": 1_900_000_000},
        {"member": "United States", "period_end": "2024-12-31", "value": 66},
        {"member": "United Kingdom", "period_end": "2024-12-31", "value": 300_000_000},
    ]
    _split_members(lines)
    assert [(l["label"], l["value"]) for l in lines] == [
        ("United States", 1_900_000_000),
        ("United Kingdom", 300_000_000),
    ]


def test_percentage_cells_are_rejected():
    """Concentration-risk tables reuse the revenue member names."""
    assert _to_number("66.00%") is None
    assert _to_number("$ 1,234") == 1234
    assert _to_number("(127)") == -127
    assert _to_number("—") is None
