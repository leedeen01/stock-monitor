"""Split normalization correctness.

The load-bearing test is `test_normalized_shares_match_companys_own_restatement`:
Apple restated FY2019 diluted shares from 4.649bn to 18.596bn in its FY2020
10-K, after the August 2020 4:1 split. If our forward-carry of as-filed share
counts is right, it must land on Apple's own restated number independently.
Two different routes to the same figure is the strongest available check that
market cap won't be silently wrong by a split ratio.
"""

import pytest

import db
import prices


@pytest.fixture(scope="module")
def conn():
    c = db.connect()
    yield c
    c.close()


def test_split_factor_ignores_splits_before_filing():
    splits = [("2020-08-31", 4.0)]
    assert prices.split_factor(splits, "2021-10-29") == 1.0


def test_split_factor_applies_splits_after_filing():
    splits = [("2020-08-31", 4.0)]
    assert prices.split_factor(splits, "2019-10-31") == 4.0


def test_split_factor_compounds():
    splits = [("2021-07-20", 4.0), ("2024-06-10", 10.0)]
    assert prices.split_factor(splits, "2020-01-01") == 40.0
    assert prices.split_factor(splits, "2022-01-01") == 10.0
    assert prices.split_factor(splits, "2025-01-01") == 1.0


def test_normalized_shares_match_companys_own_restatement(conn):
    """Apple FY2019 diluted shares, as-filed then carried through the 4:1 split,
    must equal the figure Apple itself restated to a year later."""
    rows = conn.execute(
        """
        SELECT value, filed_at FROM fundamentals
        WHERE ticker = 'AAPL' AND concept = 'shares_diluted'
          AND period_end = '2019-09-28' AND duration_days BETWEEN 340 AND 380
        ORDER BY filed_at
        """
    ).fetchall()
    assert len(rows) >= 2, "expected an original filing and at least one restatement"

    as_filed, filed_at = rows[0]["value"], rows[0]["filed_at"]
    restated = rows[-1]["value"]

    normalized = prices.normalize_shares(conn, "AAPL", as_filed, filed_at)

    # Within 0.01% — the two figures are independently rounded by Apple.
    assert normalized == pytest.approx(restated, rel=1e-4), (
        f"as-filed {as_filed:,.0f} (filed {filed_at}) normalized to {normalized:,.0f}, "
        f"but Apple restated to {restated:,.0f}"
    )


def test_normalizing_a_restated_value_is_a_noop(conn):
    """The already-restated figure has no splits after its filing date, so
    normalization must leave it alone. Both routes converge."""
    row = conn.execute(
        """
        SELECT value, filed_at FROM fundamentals
        WHERE ticker = 'AAPL' AND concept = 'shares_diluted'
          AND period_end = '2019-09-28' AND duration_days BETWEEN 340 AND 380
        ORDER BY filed_at DESC LIMIT 1
        """
    ).fetchone()
    assert prices.normalize_shares(conn, "AAPL", row["value"], row["filed_at"]) == row["value"]


def test_yfinance_close_is_split_adjusted(conn):
    """Confirms the assumption behind pairing prices with normalized shares.

    Apple closed near $499 the last session before the 4:1 split and near $127
    the first session after. If stored closes are split-adjusted, the pre-split
    day reads about a quarter of its nominal price, so the series is continuous
    across the split rather than showing a 4x cliff.
    """
    before = conn.execute(
        "SELECT close FROM prices WHERE ticker='AAPL' AND date < '2020-08-31' ORDER BY date DESC LIMIT 1"
    ).fetchone()["close"]
    after = conn.execute(
        "SELECT close FROM prices WHERE ticker='AAPL' AND date >= '2020-08-31' ORDER BY date LIMIT 1"
    ).fetchone()["close"]

    assert before < 200, f"pre-split close {before:.2f} looks unadjusted (expected ~125, not ~499)"
    assert 0.8 < after / before < 1.25, (
        f"discontinuity across the split: {before:.2f} -> {after:.2f}"
    )
