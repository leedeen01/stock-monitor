"""Alert evaluation.

The design decision that decides whether this feature is useful or noise:
**rules are edge-triggered, not level-triggered.**

If Apple's P/E sits below its 25th percentile for forty sessions, a
level-triggered evaluator produces forty identical alerts. You'd start ignoring
them within a week, and an alert you ignore is worse than no alert — it costs
attention and returns nothing. So every rule is evaluated twice: on the latest
session and on the one before it. An event is recorded only on a false -> true
transition, i.e. the moment the condition was actually crossed. If it goes false
and later crosses again, that's a new event, because it is genuinely new
information.

`metric_key` may be the sentinel '__primary__', which resolves per ticker to
whichever multiple leads for its default group. One rule then reads P/E for Big
Tech and EV/EBITDA for semiconductors — the group system doing its job.
"""

import argparse
import sqlite3
from datetime import date, datetime, timedelta, timezone

import db
import metrics

# Mirrors PERCENTILE_WINDOW_YEARS / MIN_YEARS_FOR_PERCENTILE in the frontend.
# test_alerts.py asserts the two implementations agree.
PERCENTILE_WINDOW_YEARS = 10
MIN_ROWS_FOR_PERCENTILE = 100

PRIMARY = "__primary__"

CONDITIONS = (
    "percentile_below",
    "percentile_above",
    "value_below",
    "value_above",
    "new_filing",
)

CONDITION_LABELS = {
    "percentile_below": "percentile below",
    "percentile_above": "percentile above",
    "value_below": "value below",
    "value_above": "value above",
    "new_filing": "new filing",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _shift_years(day: str, years: int) -> str:
    return (date.fromisoformat(day) - timedelta(days=round(365.25 * years))).isoformat()


# --- metric access --------------------------------------------------------

def resolve_metric(conn: sqlite3.Connection, ticker: str, metric_key: str) -> str | None:
    """Turn '__primary__' into the ticker's group-specific leading multiple."""
    if metric_key != PRIMARY:
        return metric_key if metric_key in metrics.BY_KEY else None

    row = conn.execute(
        """
        SELECT g.primary_multiple AS m
        FROM watchlist w
        LEFT JOIN metric_groups g ON g.id = w.default_group_id
        WHERE w.ticker = ?
        """,
        (ticker,),
    ).fetchone()
    if row and row["m"] in metrics.BY_KEY:
        return row["m"]
    return None


def value_on(conn: sqlite3.Connection, ticker: str, column: str, session: str) -> float | None:
    """Metric value on a specific session. Column name is registry-checked by
    the caller — never interpolate an unvalidated key here."""
    row = conn.execute(
        f"SELECT {column} AS v FROM ratios_daily WHERE ticker = ? AND date = ?",
        (ticker, session),
    ).fetchone()
    return row["v"] if row and row["v"] is not None else None


def percentile_on(
    conn: sqlite3.Connection, ticker: str, column: str, session: str
) -> tuple[float | None, bool]:
    """Where the value on `session` sits in the preceding 10 years.

    Uses only data up to and including `session`, so evaluating a past session
    gives the answer that was true then — the same no-lookahead discipline the
    rest of the pipeline follows.
    """
    value = value_on(conn, ticker, column, session)
    if value is None:
        return None, False

    since = _shift_years(session, PERCENTILE_WINDOW_YEARS)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n,
               SUM(CASE WHEN {column} <= ? THEN 1 ELSE 0 END) AS below
        FROM ratios_daily
        WHERE ticker = ? AND {column} IS NOT NULL
          AND date >= ? AND date <= ?
        """,
        (value, ticker, since, session),
    ).fetchone()

    if not row or not row["n"]:
        return None, False
    return (row["below"] / row["n"]) * 100, row["n"] >= MIN_ROWS_FOR_PERCENTILE


def _filed_at_on(conn: sqlite3.Connection, ticker: str, session: str) -> str | None:
    row = conn.execute(
        "SELECT fundamentals_filed_at AS f FROM ratios_daily WHERE ticker = ? AND date = ?",
        (ticker, session),
    ).fetchone()
    return row["f"] if row else None


# --- rule evaluation ------------------------------------------------------

def _holds(
    conn: sqlite3.Connection, rule: sqlite3.Row, ticker: str, session: str,
) -> tuple[bool, float | None, float | None]:
    """Whether the rule's threshold condition is true on a session.

    Returns (holds, value, percentile) so the event can record what tripped it.
    `new_filing` is handled separately — it detects change, not a threshold.
    """
    column = resolve_metric(conn, ticker, rule["metric_key"])
    if column is None:
        return False, None, None

    value = value_on(conn, ticker, column, session)
    if value is None:
        return False, None, None

    condition = rule["condition"]
    if condition == "value_below":
        return value < rule["threshold"], value, None
    if condition == "value_above":
        return value > rule["threshold"], value, None

    pct, sufficient = percentile_on(conn, ticker, column, session)
    # Without enough history a percentile is not meaningful, and firing on one
    # would assert precision the data can't support.
    if pct is None or not sufficient:
        return False, value, pct
    if condition == "percentile_below":
        return pct < rule["threshold"], value, pct
    if condition == "percentile_above":
        return pct > rule["threshold"], value, pct
    return False, value, pct


def _load_state(conn: sqlite3.Connection, rule_id: int, ticker: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM alert_rule_state WHERE rule_id = ? AND ticker = ?",
        (rule_id, ticker),
    ).fetchone()


def _save_state(
    conn: sqlite3.Connection, rule_id: int, ticker: str,
    session: str, held: bool, marker: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO alert_rule_state (rule_id, ticker, last_session, last_held, last_marker)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(rule_id, ticker) DO UPDATE SET
            last_session = excluded.last_session,
            last_held    = excluded.last_held,
            last_marker  = excluded.last_marker
        """,
        (rule_id, ticker, session, 1 if held else 0, marker),
    )


def tickers_in_scope(conn: sqlite3.Connection, rule: sqlite3.Row) -> list[str]:
    if rule["scope"] == "ticker":
        return [rule["scope_ref"].upper()] if rule["scope_ref"] else []
    if rule["scope"] == "group":
        return [
            r["ticker"]
            for r in conn.execute(
                """
                SELECT sg.ticker FROM stock_groups sg
                JOIN watchlist w ON w.ticker = sg.ticker AND w.supported = 1
                WHERE sg.group_id = ? ORDER BY sg.ticker
                """,
                (rule["scope_ref"],),
            )
        ]
    return [
        r["ticker"]
        for r in conn.execute(
            "SELECT ticker FROM watchlist WHERE supported = 1 ORDER BY ticker"
        )
    ]


def _sessions(conn: sqlite3.Connection, ticker: str) -> tuple[str | None, str | None]:
    rows = conn.execute(
        "SELECT date FROM ratios_daily WHERE ticker = ? ORDER BY date DESC LIMIT 2",
        (ticker,),
    ).fetchall()
    latest = rows[0]["date"] if rows else None
    previous = rows[1]["date"] if len(rows) > 1 else None
    return latest, previous


def evaluate_rule(conn: sqlite3.Connection, rule: sqlite3.Row) -> list[dict]:
    """Fire where the condition crossed from false to true since last checked."""
    fired: list[dict] = []
    is_filing_rule = rule["condition"] == "new_filing"

    for ticker in tickers_in_scope(conn, rule):
        latest, _ = _sessions(conn, ticker)
        if latest is None:
            continue

        prior = _load_state(conn, rule["id"], ticker)
        marker = _filed_at_on(conn, ticker, latest) if is_filing_rule else None

        if is_filing_rule:
            # Nothing to compare against on first sight, so record the current
            # filing and wait for it to change.
            holds = bool(prior and marker and prior["last_marker"] != marker)
            value = pct = None
        else:
            holds, value, pct = _holds(conn, rule, ticker, latest)

        # The edge test. No stored state means this pair has never been checked
        # — a new rule, or a newly added ticker — and a condition already true
        # is news to you, so it counts as a crossing.
        crossed = holds and (prior is None or not prior["last_held"])

        _save_state(conn, rule["id"], ticker, latest, holds, marker)

        if crossed:
            fired.append({
                "rule_id": rule["id"],
                "ticker": ticker,
                "trigger_date": latest,
                "metric_key": resolve_metric(conn, ticker, rule["metric_key"]),
                "value": value,
                "percentile": pct,
                "detail": _describe(conn, rule, ticker, value, pct),
            })

    conn.commit()
    return fired


def _describe(conn, rule, ticker: str, value: float | None, pct: float | None) -> str:
    if rule["condition"] == "new_filing":
        return f"{ticker} filed new financials"

    column = resolve_metric(conn, ticker, rule["metric_key"])
    spec = metrics.BY_KEY.get(column)
    label = spec.label if spec else column
    shown = _format(value, spec.fmt) if spec else f"{value:.2f}"
    direction = "below" if rule["condition"].endswith("below") else "above"

    if rule["condition"].startswith("percentile"):
        # One decimal on the percentile: rounding 14.6 to "15th" against a
        # threshold of 15 reads as a contradiction.
        return (
            f"{ticker} {label} {shown} — {pct:.1f} percentile of its own history "
            f"(rule: {direction} {rule['threshold']:.0f})"
        )
    return f"{ticker} {label} {shown} (rule: {direction} {_format(rule['threshold'], spec.fmt if spec else 'ratio')})"


def _format(value: float | None, fmt: str) -> str:
    """Mirrors the frontend formatter closely enough for alert text."""
    if value is None:
        return "—"
    if fmt == "percent":
        return f"{value * 100:.1f}%"
    if fmt == "multiple":
        return f"{value:.1f}x"
    if fmt == "days":
        return f"{value:.0f}d"
    if fmt in ("currency", "number"):
        for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
            if abs(value) >= size:
                return f"{value / size:.1f}{unit}"
        return f"{value:,.0f}"
    return f"{value:.2f}"


def record(conn: sqlite3.Connection, events: list[dict]) -> int:
    """Persist events. The UNIQUE constraint makes re-running a no-op."""
    if not events:
        return 0
    before = conn.execute("SELECT COUNT(*) AS c FROM alert_events").fetchone()["c"]
    conn.executemany(
        """
        INSERT OR IGNORE INTO alert_events
            (rule_id, ticker, trigger_date, created_at, metric_key, value, percentile, detail)
        VALUES (:rule_id, :ticker, :trigger_date, :created_at, :metric_key,
                :value, :percentile, :detail)
        """,
        [{**e, "created_at": _now()} for e in events],
    )
    conn.commit()
    after = conn.execute("SELECT COUNT(*) AS c FROM alert_events").fetchone()["c"]
    return after - before


def evaluate_all(conn: sqlite3.Connection, verbose: bool = True) -> list[dict]:
    rules = conn.execute(
        "SELECT * FROM alert_rules WHERE enabled = 1 ORDER BY id"
    ).fetchall()

    all_events: list[dict] = []
    for rule in rules:
        all_events.extend(evaluate_rule(conn, rule))

    inserted = record(conn, all_events)

    if verbose:
        print(f"evaluated {len(rules)} rules -> {len(all_events)} matches, {inserted} new")
        for event in all_events:
            print(f"  {event['detail']}  [{event['trigger_date']}]")
    return all_events


# Starting rules. Deliberately few — an alert list you scroll past is the same
# as no alert list. Each of these is something you'd want to be interrupted for.
DEFAULT_RULES = [
    {
        "name": "Cheap vs its own history",
        "scope": "all", "scope_ref": None,
        "metric_key": PRIMARY, "condition": "percentile_below", "threshold": 15,
    },
    {
        "name": "Expensive vs its own history",
        "scope": "all", "scope_ref": None,
        "metric_key": PRIMARY, "condition": "percentile_above", "threshold": 90,
    },
    {
        # The earnings-quality tripwire: profit that isn't turning into cash.
        "name": "Profit not converting to cash",
        "scope": "all", "scope_ref": None,
        "metric_key": "fcf_conversion", "condition": "value_below", "threshold": 0.8,
    },
    {
        "name": "Leverage above 3x EBITDA",
        "scope": "all", "scope_ref": None,
        "metric_key": "net_debt_ebitda", "condition": "value_above", "threshold": 3,
    },
    {
        "name": "New financials filed",
        "scope": "all", "scope_ref": None,
        "metric_key": PRIMARY, "condition": "new_filing", "threshold": None,
    },
    {
        # Group-scoped: inventory turning over more slowly is the classic
        # semiconductor cycle tell, and means nothing for a software company.
        "name": "Inventory piling up (semis)",
        "scope": "group", "scope_ref": "Semiconductors",
        "metric_key": "inventory_days", "condition": "percentile_above", "threshold": 85,
    },
]


def seed_default_rules(conn: sqlite3.Connection, verbose: bool = True) -> int:
    """Insert the starter rules. Skips any whose name already exists, so it is
    safe to re-run and won't resurrect a rule you deleted on purpose."""
    added = 0
    for spec in DEFAULT_RULES:
        exists = conn.execute(
            "SELECT 1 FROM alert_rules WHERE name = ?", (spec["name"],)
        ).fetchone()
        if exists:
            continue

        scope_ref = spec["scope_ref"]
        if spec["scope"] == "group" and scope_ref:
            row = conn.execute(
                "SELECT id FROM metric_groups WHERE name = ?", (scope_ref,)
            ).fetchone()
            if not row:
                if verbose:
                    print(f"  skipped '{spec['name']}': no group named {scope_ref}")
                continue
            scope_ref = str(row["id"])

        conn.execute(
            """
            INSERT INTO alert_rules
                (name, scope, scope_ref, metric_key, condition, threshold, enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (spec["name"], spec["scope"], scope_ref, spec["metric_key"],
             spec["condition"], spec["threshold"], _now()),
        )
        added += 1
    conn.commit()
    if verbose:
        print(f"seeded {added} new rule(s)")
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate alert rules")
    parser.add_argument("--list", action="store_true", help="Show rules and exit")
    parser.add_argument("--open", action="store_true", help="Show unacknowledged events")
    parser.add_argument("--seed", action="store_true", help="Insert the default rules")
    args = parser.parse_args()

    conn = db.connect()
    db.init_schema(conn)

    if args.seed:
        seed_default_rules(conn)
        return

    if args.list:
        for r in conn.execute("SELECT * FROM alert_rules ORDER BY id"):
            scope = r["scope"] if r["scope"] == "all" else f"{r['scope']}={r['scope_ref']}"
            state = "on " if r["enabled"] else "off"
            print(f"  [{state}] #{r['id']:<3} {r['name']:<38} {scope:<16} "
                  f"{r['metric_key']} {r['condition']} {r['threshold']}")
        return

    if args.open:
        for e in conn.execute(
            """SELECT e.*, r.name FROM alert_events e
               JOIN alert_rules r ON r.id = e.rule_id
               WHERE e.acknowledged = 0 ORDER BY e.created_at DESC LIMIT 50"""
        ):
            print(f"  {e['trigger_date']}  {e['name']:<32} {e['detail']}")
        return

    evaluate_all(conn)
    conn.close()


if __name__ == "__main__":
    main()
