"""Revenue by product/service line, parsed from SEC rendered report tables.

Why not companyfacts: segment revenue is tagged with XBRL *dimensions*
(`ProductOrServiceAxis`), and the companyfacts endpoint returns only
undimensioned facts. That is the same reason Alphabet's per-share-class diluted
count is missing before 2022.

Why R-files rather than raw inline XBRL: every filing publishes rendered report
fragments indexed by FilingSummary.xml. They are kilobytes rather than megabytes,
they parse with pandas, and their row labels are already human readable —
"YouTube ads" instead of `goog:YouTubeAdvertisingRevenueMember`. The cost is that
they are presentation, not data, so the reconciliation check below is what makes
them trustworthy.

The hard part is hierarchy. These tables interleave several levels of the same
breakdown, and summing them naively double-counts badly:

    Google advertising        294,691   <- subtotal of the next three
      Google Search & other   224,532
      YouTube ads              40,367
      Google Network           29,792
    Google Services          342,721    <- parent of every "| Google Services" row

Summing every row for Alphabet gives 637,412 against actual revenue of 402,836.
NVIDIA has the same shape without any pipe notation to hint at it: `Data Center`
is exactly `Compute + Networking`. So subtotals are found by value — a row equal
to a contiguous run of its siblings — not by label.
"""

import argparse
import re
import sqlite3
from datetime import date, datetime, timezone
from io import StringIO

import pandas as pd
import requests

import db
import edgar
from config import SEC_USER_AGENT

ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}"
SUBMISSIONS = "https://data.sec.gov/submissions/{name}"

# Report selection, in preference order. Reconciliation cannot distinguish a
# geographic split from a product split — both sum to total revenue — so
# ordering does that job instead.
#
# Geography is a fallback tier rather than an exclusion because for some filers
# it *is* the segmentation: Netflix reports UCAN/EMEA/LATAM/APAC as its
# reportable segments and publishes no product breakdown at all. Banning
# geography outright left it with nothing.
PREFERRED = (
    re.compile(r"disaggregat|by type|by market|by product|significant product", re.I),
    re.compile(r"reportable segment|by segment|segment information|"
               r"operations by segment|financial information by segment|"
               r"segment sales|segment revenue", re.I),
    re.compile(r"geograph|by region|by countr", re.I),
)

# Tables that are not a revenue breakdown at all. Tested against the report's
# own subject — the part after the last " - " — not the whole name. Filers
# prefix the section title, so "Operating Segments and Geographic Data -
# Segment Sales and Operating Revenues" is the right table with a misleading
# prefix, and matching the full string threw Chevron away.
EXCLUDE = re.compile(
    r"narrative|parenthetical|\(tables\)|long-lived|property and equipment|"
    r"performance obligation|unearned|corporate unallocated|by customer|"
    r"major customer|segment assets|reconciling item",
    re.I,
)

# Filers are inconsistent between "(Details)" and "(Detail)" — Microsoft uses
# the singular throughout, and requiring the plural rejected every one of its
# reports including the correct one.
DETAIL_SUFFIX = re.compile(r"\(detail(s)?\)", re.I)

# A row is a subtotal if it matches a run of siblings this closely. Kept tight
# because real subtotals are exact — the slack is only for display rounding.
# At 0.5% this produced false positives: Netflix's UCAN region happens to land
# within 0.43% of EMEA + LATAM, so it was flagged as their subtotal and the
# whole regional breakdown collapsed to one line.
SUBTOTAL_TOLERANCE = 0.001
# A breakdown needs at least this many lines to be a breakdown. Netflix's
# segment table reduces to a single row equal to total revenue, which
# reconciles perfectly at 0.0% while saying nothing at all.
MIN_LINES = 2
# Parsed lines must sum to reported revenue this closely, or the filing is dropped.
RECONCILE_TOLERANCE = 0.01
MAX_RUN = 8

SCALE = {"thousands": 1e3, "millions": 1e6, "billions": 1e9}

SEGMENT_COLUMNS = (
    "ticker", "axis", "label", "parent", "period_end", "fiscal_year",
    "filed_at", "accession", "value", "is_subtotal",
)


def _get(url: str) -> str:
    edgar._throttle()
    r = requests.get(
        url,
        headers={"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"},
        timeout=90,
    )
    r.raise_for_status()
    return r.text


# --- filing discovery -----------------------------------------------------

def list_annual_filings(cik: int, years: int = 10, limit: int = 10) -> list[dict]:
    """10-K filings, newest first, far enough back to cover `years`.

    Each 10-K carries three fiscal years of comparatives, so full coverage
    usually needs about four filings rather than ten. Older filings live in
    separate shards outside `filings.recent`, fetched only when the recent page
    doesn't reach back far enough.
    """
    payload = requests.get(
        SUBMISSIONS.format(name=f"CIK{cik:010d}.json"),
        headers={"User-Agent": SEC_USER_AGENT},
        timeout=60,
    ).json()

    out: list[dict] = []

    def harvest(block: dict) -> None:
        for i, form in enumerate(block.get("form", [])):
            if form != "10-K":
                continue
            out.append({
                "accession": block["accessionNumber"][i].replace("-", ""),
                "filed": block["filingDate"][i],
                "period": block.get("reportDate", [None] * len(block["form"]))[i],
            })

    harvest(payload["filings"]["recent"])
    cutoff = (date.today().year - years)

    for shard in payload["filings"].get("files", []):
        if out and min(f["filed"][:4] for f in out) <= str(cutoff):
            break
        edgar._throttle()
        extra = requests.get(
            SUBMISSIONS.format(name=shard["name"]),
            headers={"User-Agent": SEC_USER_AGENT},
            timeout=60,
        ).json()
        harvest(extra)

    out.sort(key=lambda f: f["filed"], reverse=True)
    return [f for f in out if f["filed"][:4] >= str(cutoff)][:limit]


def candidate_reports(cik: int, accession: str) -> list[tuple[str, str]]:
    """Revenue-breakdown reports in a filing, best candidate first."""
    xml = _get(f"{ARCHIVE.format(cik=cik, accession=accession)}/FilingSummary.xml")
    found: list[tuple[int, str, str]] = []

    for block in re.findall(r"<Report[^>]*>(.*?)</Report>", xml, re.S):
        name = re.search(r"<ShortName>(.*?)</ShortName>", block, re.S)
        html = re.search(r"<HtmlFileName>(.*?)</HtmlFileName>", block, re.S)
        if not name or not html:
            continue
        short, filename = name.group(1).strip(), html.group(1).strip()
        if not DETAIL_SUFFIX.search(short):
            continue
        # Match exclusions against the subject, not the section prefix.
        subject = short.rsplit(" - ", 1)[-1]
        if EXCLUDE.search(subject):
            continue
        for tier, pattern in enumerate(PREFERRED):
            if pattern.search(short):
                found.append((tier, short, filename))
                break

    found.sort(key=lambda f: f[0])
    return [(short, filename) for _tier, short, filename in found]


# --- table parsing --------------------------------------------------------

def detect_scale(html: str) -> float:
    """Tables state their units in the header. Never assume — guessing wrong is
    a silent 1000x error, the same failure class as the share counts NVIDIA and
    AMD filed at the wrong scale."""
    match = re.search(r"\$\s*in\s*(Thousands|Millions|Billions)", html, re.I)
    return SCALE[match.group(1).lower()] if match else 1.0


def _to_number(cell) -> float | None:
    if cell is None or (isinstance(cell, float) and pd.isna(cell)):
        return None
    text = str(cell).strip()
    if not text or text in {"nan", "—", "–", "-"}:
        return None
    # Percentages are not revenue. Filers put concentration-risk tables beside
    # the dollar figures using the same member names, and Palantir's ended up
    # overwriting its real revenue rows: the sum of dollars plus a trivial 100%
    # still reconciled, then identical primary keys collapsed to whichever was
    # written last.
    if "%" in text:
        return None
    negative = text.startswith("(") or text.startswith("$ (")
    cleaned = re.sub(r"[^\d.]", "", text)
    if not cleaned or cleaned == ".":
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def _period_ends(frame: pd.DataFrame) -> list[str | None]:
    """Fiscal period end per value column, from the header row."""
    ends: list[str | None] = []
    for column in frame.columns[1:]:
        text = column[-1] if isinstance(column, tuple) else str(column)
        parsed = pd.to_datetime(str(text), errors="coerce")
        ends.append(None if pd.isna(parsed) else parsed.date().isoformat())
    return ends


SKIP_LABEL = re.compile(r"\[line items\]|\[abstract\]|\[member\]|\[domain\]", re.I)
# Taxonomy boilerplate that renders as a member name but labels nothing a human
# would recognise — "Segment Reporting Information", "Reportable Segment,
# Aggregation before Other Operating Segment". Never a real product line.
JUNK_MEMBER = re.compile(
    r"^(segment reporting information|reportable segment|operating segments?|"
    r"intersegment|consolidation|elimination)|concentration risk", re.I,
)
TOTAL_LABEL = re.compile(r"^\s*(total|net sales|revenues?)\s*$", re.I)
# Which of a member's rows is the revenue line, when it reports several.
REVENUE_ROW = re.compile(r"revenue|net sales|^sales", re.I)


def parse_report(html: str) -> dict:
    """Turn one rendered report into labelled lines per period.

    Rows come in pairs: a member header carrying the product name and no values,
    then a value row whose own label is a generic concept ("Net sales",
    "Revenue from contract with customers"). Rows with values but no preceding
    member sit at the undimensioned level — the reported total, and reconciling
    items such as Alphabet's hedging gains.
    """
    frame = pd.read_html(StringIO(html))[0]
    scale = detect_scale(html)
    periods = _period_ends(frame)

    lines: list[dict] = []
    totals: dict[str, float] = {}
    current: str | None = None
    pending: list[tuple[str, list]] = []

    def flush() -> None:
        """Emit the revenue row for the member just finished.

        A member can carry several concepts — AMD's segment table lists revenue
        and operating income, Microsoft's adds cost of revenue and operating
        expenses. Taking whichever came first would silently record operating
        income as revenue, so the revenue-labelled row is chosen by name.
        """
        nonlocal current, pending
        if current and pending:
            chosen = next(
                (p for p in pending if REVENUE_ROW.search(p[0])), pending[0]
            )
            for period, value in zip(periods, chosen[1]):
                if period and value is not None:
                    lines.append({
                        "member": current,
                        "period_end": period,
                        "value": value * scale,
                    })
        current, pending = None, []

    for _, row in frame.iterrows():
        raw_label = str(row.iloc[0]).strip()
        if not raw_label or raw_label == "nan" or SKIP_LABEL.search(raw_label):
            continue

        values = [_to_number(row.iloc[i + 1]) for i in range(len(periods))]

        if all(v is None for v in values):
            flush()
            current = None if JUNK_MEMBER.match(raw_label) else raw_label
            continue

        if current is None:
            # Undimensioned: the total, or a reconciling item.
            if TOTAL_LABEL.match(raw_label):
                for period, value in zip(periods, values):
                    if period and value is not None:
                        totals[period] = value * scale
            continue

        pending.append((raw_label, values))

    flush()
    _split_members(lines)
    return {"lines": lines, "totals": totals, "scale": scale, "periods": periods}


def _split_members(lines: list[dict]) -> None:
    """Resolve "A | B" member names into label and parent.

    The order is not stable across filings. Alphabet's 2026 10-K writes
    "Google Search & other | Google Services" — child first — while its 2023
    10-K writes "Google Services | Google Search & other". Assuming either one
    silently mislabels every row in half the filings, which then breaks sibling
    grouping and lets subtotals through: the 2023 table summed to 173% of
    reported revenue.

    So orientation is read off the data. The parent repeats across its children,
    so whichever side has fewer distinct values is the parent.
    """
    pairs = [l["member"].split("|", 1) for l in lines if "|" in l["member"]]
    parent_on_left = False
    if pairs:
        left = {p[0].strip() for p in pairs}
        right = {p[1].strip() for p in pairs}
        parent_on_left = len(left) < len(right)

    for line in lines:
        member = line.pop("member")
        if "|" in member:
            first, second = (p.strip() for p in member.split("|", 1))
            label, parent = (second, first) if parent_on_left else (first, second)
        else:
            label, parent = member.strip(), None
        line["label"] = label
        line["parent"] = parent or None

    # Boilerplate can sit on either side of the pipe, so filter after the split.
    kept = [
        l for l in lines
        if not JUNK_MEMBER.search(l["label"])
        and not (l["parent"] and JUNK_MEMBER.search(l["parent"]))
    ]

    # Collapse duplicate (label, period). The storage key is label-based, so a
    # label appearing under two parents — the same region listed once per
    # segment, say — silently overwrote itself on insert. Reconciliation then
    # validated a sum that included every duplicate while the database kept
    # only one, so a filing could pass the check and still store numbers that
    # don't add up. Deduplicating here makes what is validated identical to
    # what is written. The largest value wins, being the revenue figure rather
    # than a component of it.
    best: dict[tuple[str, str], dict] = {}
    for line in kept:
        key = (line["label"], line["period_end"])
        if key not in best or abs(line["value"]) > abs(best[key]["value"]):
            best[key] = line
    lines[:] = [l for l in kept if best.get((l["label"], l["period_end"])) is l]


# --- hierarchy ------------------------------------------------------------

def mark_subtotals(lines: list[dict]) -> None:
    """Flag rows that aggregate other rows, so they are not counted twice.

    Two mechanisms, because filers use both:

    * Pipe notation names a parent explicitly — anything that appears as another
      row's parent is an aggregate ("Google Services").
    * Otherwise, a row equal to a contiguous run of its siblings is a subtotal.
      Runs are checked on each side: Alphabet lists "Google advertising" *after*
      the three lines it sums, NVIDIA lists "Data Center" *before* Compute and
      Networking.
    """
    for line in lines:
        line["is_subtotal"] = False

    by_period: dict[str, list[dict]] = {}
    for line in lines:
        by_period.setdefault(line["period_end"], []).append(line)

    for group in by_period.values():
        parents = {line["parent"] for line in group if line["parent"]}
        for line in group:
            if line["label"] in parents:
                line["is_subtotal"] = True

        # Compare only within a sibling set — a run must share a parent.
        for parent in {line["parent"] for line in group}:
            siblings = [l for l in group if l["parent"] == parent and not l["is_subtotal"]]
            for i, candidate in enumerate(siblings):
                if candidate["value"] is None or candidate["value"] <= 0:
                    continue
                if _matches_run(candidate, siblings, i):
                    candidate["is_subtotal"] = True


def _matches_run(candidate: dict, siblings: list[dict], i: int) -> bool:
    target = candidate["value"]
    for length in range(2, MAX_RUN + 1):
        after = siblings[i + 1: i + 1 + length]
        if len(after) == length and _close(sum(s["value"] for s in after), target):
            return True
        before = siblings[max(0, i - length): i]
        if len(before) == length and _close(sum(s["value"] for s in before), target):
            return True
    return False


def _close(a: float, b: float, tolerance: float = SUBTOTAL_TOLERANCE) -> bool:
    if b == 0:
        return abs(a) < 1
    return abs(a - b) / abs(b) <= tolerance


# --- reconciliation -------------------------------------------------------

def reported_revenue(conn: sqlite3.Connection, ticker: str, period_end: str) -> float | None:
    """Total revenue already ingested for the same period, matched loosely on
    date because the rendered table and the XBRL period can differ by a day."""
    row = conn.execute(
        """
        SELECT value FROM fundamentals
        WHERE ticker = ? AND concept = 'revenue'
          AND duration_days BETWEEN 340 AND 380
          AND ABS(julianday(period_end) - julianday(?)) <= 5
        ORDER BY filed_at DESC LIMIT 1
        """,
        (ticker, period_end),
    ).fetchone()
    return row["value"] if row else None


def reconcile(conn, ticker: str, lines: list[dict], totals: dict) -> dict[str, bool]:
    """Per period: do the non-subtotal lines add up to reported revenue?

    This is what makes a presentation-layer scrape safe. It catches the wrong
    report, the wrong scale, a subtotal left in, and a misread column — all at
    once, and all of which otherwise produce numbers that look entirely
    reasonable.
    """
    verdict: dict[str, bool] = {}
    by_period: dict[str, list[dict]] = {}
    for line in lines:
        by_period.setdefault(line["period_end"], []).append(line)

    for period, group in by_period.items():
        # Reported revenue outranks the table's own total row. Trusting the
        # table meant checking a bad parse against itself: when the wrong rows
        # were picked up, the total was picked up wrongly too and the two
        # agreed, so AMD stored 0.21bn against actual revenue of 6.47bn.
        total = reported_revenue(conn, ticker, period) or totals.get(period)
        if not total:
            verdict[period] = False
            continue
        leaves = [l for l in group if not l["is_subtotal"]]
        if len(leaves) < MIN_LINES:
            verdict[period] = False
            continue
        verdict[period] = _close(sum(l["value"] for l in leaves), total,
                                 RECONCILE_TOLERANCE)
    return verdict


# --- orchestration --------------------------------------------------------

def store(conn: sqlite3.Connection, ticker: str, accession: str, filed: str,
          lines: list[dict]) -> int:
    rows = [[
        ticker, "product", l["label"], l["parent"], l["period_end"],
        int(l["period_end"][:4]), filed, accession, l["value"],
        1 if l["is_subtotal"] else 0,
    ] for l in lines]
    conn.executemany(
        f"INSERT OR REPLACE INTO segment_revenue ({', '.join(SEGMENT_COLUMNS)}) "
        f"VALUES ({', '.join(['?'] * len(SEGMENT_COLUMNS))})",
        rows,
    )
    conn.commit()
    return len(rows)


def backfill_segments(conn: sqlite3.Connection, ticker: str, years: int = 10,
                      verbose: bool = True) -> dict:
    ticker = ticker.upper()
    meta = edgar.resolve_ticker(ticker)

    # A reincorporated company files under a new CIK that holds none of the
    # history — ExxonMobil's current registrant has no 10-K at all, so asking
    # only the primary CIK returned zero filings. Merge across every CIK, and
    # remember which one owns each accession since the archive path needs it.
    filings: list[dict] = []
    for cik in meta["ciks"]:
        for filing in list_annual_filings(cik, years=years):
            filings.append({**filing, "cik": cik})
    filings.sort(key=lambda f: f["filed"], reverse=True)
    filings = filings[:10]

    if verbose:
        print(f"{ticker}: {len(filings)} annual filings back to {filings[-1]['filed'] if filings else '-'}")

    # Replace rather than upsert. Parsing rules change as filers' table layouts
    # are discovered, and rows left behind under an older interpretation keep
    # winning — exactly how the D&A concept remapping silently did nothing
    # until the stale rows were cleared. Re-deriving is cheap and complete.
    conn.execute("DELETE FROM segment_revenue WHERE ticker = ?", (ticker,))
    conn.commit()

    stored = parsed_filings = 0
    failures: list[str] = []

    for filing in filings:
        accession, filed, cik = filing["accession"], filing["filed"], filing["cik"]
        try:
            candidates = candidate_reports(cik, accession)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{filed}: filing summary unavailable ({exc})")
            continue

        if not candidates:
            failures.append(f"{filed}: no revenue breakdown report")
            continue

        # Try candidates in preference order; reconciliation decides the winner,
        # since a plausible name is not proof of the right table.
        for short, filename in candidates:
            try:
                html = _get(f"{ARCHIVE.format(cik=cik, accession=accession)}/{filename}")
                report = parse_report(html)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{filed} {filename}: parse failed ({exc})")
                continue

            if not report["lines"]:
                continue

            mark_subtotals(report["lines"])
            verdict = reconcile(conn, ticker, report["lines"], report["totals"])
            good = [p for p, ok in verdict.items() if ok]
            if not good:
                continue

            keep = [l for l in report["lines"] if l["period_end"] in good]
            stored += store(conn, ticker, accession, filed, keep)
            parsed_filings += 1
            if verbose:
                labels = sorted({l["label"] for l in keep if not l["is_subtotal"]})
                print(f"  {filed} [{filename}] {short[:52]}")
                print(f"    periods {sorted(good)}  lines: {', '.join(labels[:7])}")
            break
        else:
            failures.append(f"{filed}: no candidate reconciled")

    if verbose and failures:
        for failure in failures:
            print(f"  SKIPPED {failure}")

    return {"ticker": ticker, "filings": len(filings), "parsed": parsed_filings,
            "rows": stored, "failures": failures}


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill revenue by product line")
    parser.add_argument("tickers", nargs="*", help="Defaults to supported watchlist")
    parser.add_argument("--years", type=int, default=10)
    args = parser.parse_args()

    conn = db.connect()
    db.init_schema(conn)
    tickers = [t.upper() for t in args.tickers] or [
        r["ticker"] for r in conn.execute(
            "SELECT ticker FROM watchlist WHERE supported = 1 ORDER BY ticker")
    ]
    for ticker in tickers:
        try:
            backfill_segments(conn, ticker, years=args.years)
        except Exception as exc:  # noqa: BLE001
            print(f"{ticker}: FAILED - {type(exc).__name__}: {exc}")
    conn.close()


if __name__ == "__main__":
    main()
