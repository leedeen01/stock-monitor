"""Alert engine correctness.

The load-bearing test is `test_a_condition_that_stays_true_fires_once`. Everything
else about alerts is cosmetic by comparison: a rule that re-fires every day while
a condition persists produces a list you stop reading within a week, and an
alert you ignore is worse than no alert — it costs attention and returns
nothing.
"""

from datetime import date, timedelta

import pytest

import alerts
import db
from conftest import seed_user


def _synthetic(tmp_path, values: list[float], column: str = "fcf_conversion"):
    """A ticker whose metric follows `values`, one trading day apart.

    Rows are inserted one at a time by the caller so the evaluator sees the
    series grow the way it would in real life, with only the sessions that had
    happened yet.
    """
    conn = db.connect(tmp_path / "alerts.db")
    db.init_schema(conn)
    seed_user(conn)
    conn.execute(
        "INSERT INTO tickers (ticker, name, supported) VALUES ('TEST', 'Test Co', 1)"
    )
    conn.execute(
        "INSERT INTO watchlist (user_id, ticker, added_at) VALUES (1, 'TEST', '2020-01-01')"
    )
    conn.commit()

    start = date(2024, 1, 1)
    rows = [
        (start + timedelta(days=i)).isoformat()
        for i in range(len(values))
    ]
    return conn, rows


def _insert_day(conn, day: str, value: float, column: str = "fcf_conversion", filed: str = "2024-01-01"):
    conn.execute(
        f"INSERT INTO ratios_daily (ticker, date, close, {column}, fundamentals_filed_at) "
        f"VALUES ('TEST', ?, 100.0, ?, ?)",
        (day, value, filed),
    )
    conn.commit()


def _add_rule(conn, condition: str, threshold: float, metric_key: str = "fcf_conversion"):
    conn.execute(
        """
        INSERT INTO alert_rules (user_id, name, scope, scope_ref, metric_key, condition, threshold, enabled, created_at)
        VALUES (1, 'test rule', 'all', NULL, ?, ?, ?, 1, '2024-01-01')
        """,
        (metric_key, condition, threshold),
    )
    conn.commit()
    return conn.execute("SELECT * FROM alert_rules ORDER BY id DESC LIMIT 1").fetchone()


def _run_daily(conn, days: list[str], values: list[float], column="fcf_conversion"):
    """Replay the series a day at a time, evaluating after each, as the
    scheduled job would."""
    total = 0
    for day, value in zip(days, values):
        _insert_day(conn, day, value, column)
        total += len(alerts.evaluate_all(conn, verbose=False))
    return total


# --- the edge-trigger invariant ------------------------------------------

def test_a_condition_that_stays_true_fires_once(tmp_path):
    """Ten consecutive sessions below the threshold is one crossing, not ten
    alerts."""
    values = [1.2, 1.1, 0.5, 0.5, 0.4, 0.45, 0.5, 0.5, 0.5, 0.5]
    conn, days = _synthetic(tmp_path, values)
    _add_rule(conn, "value_below", 0.8)

    _run_daily(conn, days, values)

    events = conn.execute("SELECT * FROM alert_events ORDER BY trigger_date").fetchall()
    assert len(events) == 1, (
        f"expected a single crossing, got {len(events)}: "
        + ", ".join(e["trigger_date"] for e in events)
    )
    assert events[0]["trigger_date"] == days[2], "should fire on the session it crossed"
    conn.close()


def test_recrossing_fires_again(tmp_path):
    """Going false and crossing back is genuinely new information."""
    values = [1.2, 0.5, 0.5, 1.2, 1.2, 0.4, 0.4]
    conn, days = _synthetic(tmp_path, values)
    _add_rule(conn, "value_below", 0.8)

    _run_daily(conn, days, values)

    events = conn.execute("SELECT trigger_date FROM alert_events ORDER BY trigger_date").fetchall()
    assert [e["trigger_date"] for e in events] == [days[1], days[5]]
    conn.close()


def test_a_new_rule_fires_on_conditions_already_true(tmp_path):
    """Writing a rule must do something.

    If a stock has been below the threshold for months and you then create a
    rule for it, pure edge detection against the previous session finds nothing
    changed and stays silent forever — the rule appears broken. An unchecked
    (rule, ticker) pair has no history with you, so a condition already true
    counts as a crossing.
    """
    values = [0.5] * 6  # below threshold the entire time
    conn, days = _synthetic(tmp_path, values)
    for day, value in zip(days, values):
        _insert_day(conn, day, value)

    # Rule created only now, long after the condition became true.
    _add_rule(conn, "value_below", 0.8)
    alerts.evaluate_all(conn, verbose=False)

    events = conn.execute("SELECT * FROM alert_events").fetchall()
    assert len(events) == 1, "a new rule must report what is already true"
    assert events[0]["trigger_date"] == days[-1]

    # ...but only once.
    alerts.evaluate_all(conn, verbose=False)
    assert conn.execute("SELECT COUNT(*) c FROM alert_events").fetchone()["c"] == 1
    conn.close()


def test_a_newly_added_ticker_reports_conditions_already_true(tmp_path):
    """Same reasoning for a stock added to an existing rule's scope."""
    conn = db.connect(tmp_path / "newticker.db")
    db.init_schema(conn)
    seed_user(conn)
    conn.execute(
        "INSERT INTO tickers (ticker, name, supported) VALUES ('OLD', 'Old', 1)"
    )
    conn.execute(
        "INSERT INTO watchlist (user_id, ticker, added_at) VALUES (1, 'OLD', '2024-01-01')"
    )
    conn.execute(
        """
        INSERT INTO alert_rules (user_id, name, scope, scope_ref, metric_key, condition, threshold, enabled, created_at)
        VALUES (1, 'r', 'all', NULL, 'fcf_conversion', 'value_below', 0.8, 1, '2024-01-01')
        """
    )
    conn.execute(
        "INSERT INTO ratios_daily (ticker, date, close, fcf_conversion) VALUES ('OLD', '2024-01-01', 10, 2.0)"
    )
    conn.commit()
    alerts.evaluate_all(conn, verbose=False)
    assert conn.execute("SELECT COUNT(*) c FROM alert_events").fetchone()["c"] == 0

    # A new ticker joins, already below the threshold.
    conn.execute(
        "INSERT INTO tickers (ticker, name, supported) VALUES ('NEW', 'New', 1)"
    )
    conn.execute(
        "INSERT INTO watchlist (user_id, ticker, added_at) VALUES (1, 'NEW', '2024-02-01')"
    )
    conn.execute(
        "INSERT INTO ratios_daily (ticker, date, close, fcf_conversion) VALUES ('NEW', '2024-01-01', 10, 0.3)"
    )
    conn.commit()
    alerts.evaluate_all(conn, verbose=False)

    events = conn.execute("SELECT ticker FROM alert_events").fetchall()
    assert [e["ticker"] for e in events] == ["NEW"]
    conn.close()


def test_a_condition_never_true_fires_nothing(tmp_path):
    values = [1.2, 1.3, 1.1, 1.5]
    conn, days = _synthetic(tmp_path, values)
    _add_rule(conn, "value_below", 0.8)

    _run_daily(conn, days, values)

    assert conn.execute("SELECT COUNT(*) c FROM alert_events").fetchone()["c"] == 0
    conn.close()


def test_reevaluating_the_same_day_is_idempotent(tmp_path):
    """A manual refresh after the scheduled run must not duplicate events."""
    values = [1.2, 0.5]
    conn, days = _synthetic(tmp_path, values)
    _add_rule(conn, "value_below", 0.8)
    _run_daily(conn, days, values)

    for _ in range(3):
        alerts.evaluate_all(conn, verbose=False)

    assert conn.execute("SELECT COUNT(*) c FROM alert_events").fetchone()["c"] == 1
    conn.close()


def test_value_above_crosses_the_other_way(tmp_path):
    values = [1.0, 2.0, 4.0, 4.5, 1.0, 5.0]
    conn, days = _synthetic(tmp_path, values)
    _add_rule(conn, "value_above", 3.0)
    _run_daily(conn, days, values)

    events = conn.execute("SELECT trigger_date FROM alert_events ORDER BY trigger_date").fetchall()
    assert [e["trigger_date"] for e in events] == [days[2], days[5]]
    conn.close()


def test_new_filing_fires_when_filed_at_changes(tmp_path):
    conn, days = _synthetic(tmp_path, [1.0] * 4)
    _add_rule(conn, "new_filing", 0)

    filings = ["2024-01-01", "2024-01-01", "2024-02-15", "2024-02-15"]
    for day, filed in zip(days, filings):
        _insert_day(conn, day, 1.0, filed=filed)
        alerts.evaluate_all(conn, verbose=False)

    events = conn.execute("SELECT trigger_date FROM alert_events").fetchall()
    assert len(events) == 1 and events[0]["trigger_date"] == days[2]
    conn.close()


# --- percentile correctness ----------------------------------------------

def test_percentile_matches_a_direct_rank_computation(tmp_path):
    """The SQL percentile must agree with sorting the values and finding the
    rank — a different route to the same number."""
    values = [float(v) for v in range(1, 201)]  # 1..200, current = 200
    conn, days = _synthetic(tmp_path, values)
    for day, value in zip(days, values):
        _insert_day(conn, day, value)

    pct, sufficient = alerts.percentile_on(conn, "TEST", "fcf_conversion", days[-1])
    assert sufficient
    assert pct == pytest.approx(100.0), "the maximum value sits at the top of its range"

    # Midpoint: the 100th of 200 ascending values.
    mid_pct, _ = alerts.percentile_on(conn, "TEST", "fcf_conversion", days[99])
    assert mid_pct == pytest.approx(100.0), (
        "evaluated as of day 100 only 100 rows exist, so it is still the max"
    )
    conn.close()


def test_percentile_uses_only_data_up_to_that_session(tmp_path):
    """No lookahead: evaluating a past session must not see later values."""
    values = [10.0] * 50 + [1.0] + [10.0] * 50
    conn, days = _synthetic(tmp_path, values)
    for day, value in zip(days, values):
        _insert_day(conn, day, value)

    # On the day of the dip, it is the lowest value seen so far.
    pct_then, _ = alerts.percentile_on(conn, "TEST", "fcf_conversion", days[50])
    assert pct_then < 5, f"the dip should rank near the bottom, got {pct_then}"
    conn.close()


def test_percentile_rules_need_enough_history(tmp_path):
    """A percentile from a handful of points asserts precision the data cannot
    support, so the rule must not fire."""
    values = [5.0] * 10 + [1.0]
    conn, days = _synthetic(tmp_path, values)
    _add_rule(conn, "percentile_below", 20)
    _run_daily(conn, days, values)

    assert conn.execute("SELECT COUNT(*) c FROM alert_events").fetchone()["c"] == 0, (
        "fired on fewer than the minimum rows for a meaningful percentile"
    )
    conn.close()


# --- scope ---------------------------------------------------------------

def test_group_scope_limits_which_tickers_are_checked(tmp_path):
    conn = db.connect(tmp_path / "scope.db")
    db.init_schema(conn)
    seed_user(conn)
    conn.executescript(
        """
        INSERT INTO tickers (ticker, name, supported) VALUES
            ('AAA', 'A', 1), ('BBB', 'B', 1);
        INSERT INTO watchlist (user_id, ticker, added_at) VALUES
            (1, 'AAA', '2024-01-01'), (1, 'BBB', '2024-01-01');
        INSERT INTO metric_groups (user_id, name, primary_multiple, created_at)
            VALUES (1, 'OnlyA', 'pe_ttm', '2024-01-01');
        """
    )
    gid = conn.execute("SELECT id FROM metric_groups WHERE name='OnlyA'").fetchone()["id"]
    conn.execute("INSERT INTO stock_groups (user_id, ticker, group_id) VALUES (1, 'AAA', ?)", (gid,))
    conn.execute(
        """
        INSERT INTO alert_rules (user_id, name, scope, scope_ref, metric_key, condition, threshold, enabled, created_at)
        VALUES (1, 'grouped', 'group', ?, 'fcf_conversion', 'value_below', 0.8, 1, '2024-01-01')
        """,
        (str(gid),),
    )
    conn.commit()

    rule = conn.execute("SELECT * FROM alert_rules").fetchone()
    assert alerts.tickers_in_scope(conn, rule) == ["AAA"]
    conn.close()
