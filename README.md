# Stock Monitor

A valuation monitoring tool built on two ideas:

**1. Groups are metric profiles, not tags.** A stock is assigned to one or more
groups, and the group decides which metrics its deep dive shows and which
multiple leads. A stock in two groups can be read either way. Group membership
also doubles as the peer set for relative comparison — you define which
companies are comparable, which beats a generic sector average.

**2. No multiple is ever shown bare.** "EV/EBITDA 39.4x" is not information.
"39.4x, the 29th percentile of its own ten years, against a group median of
52.8x" is. Every multiple renders as current value, own-history percentile, and
peer median.

Every metric label in the deep dive carries an **ⓘ** button opening a short
panel: what the metric is, then *how to use it* — the thresholds that matter,
what to read it against, and the specific way it misleads. The P/E entry warns
that it bottoms at cycle peaks; ROE warns that leverage and buybacks inflate it;
inventory days explains why it turns before earnings do. The guidance lives in
`ingest/metrics.py` as a `usage` field on each metric and is exported to the
frontend with everything else, so there is one source of truth.

## Running it

```bash
cd web && npm run dev
```

Then open http://localhost:3000. Add tickers from the UI — it resolves the CIK,
backfills EDGAR and prices, derives the ratio history, and assigns groups.

To drive the pipeline directly:

```bash
.venv/Scripts/python ingest/backfill.py NVDA AMD    # EDGAR fundamentals
.venv/Scripts/python ingest/prices.py               # prices, splits, dividends
.venv/Scripts/python ingest/derive.py               # daily ratio series
.venv/Scripts/python ingest/status.py               # what's loaded
.venv/Scripts/python ingest/verify.py               # eyeball the numbers
```

`backfill.py` and `prices.py` are incremental and safe to re-run. `derive.py`
rebuilds `ratios_daily` from scratch each time — it holds no information of its
own.

## Scheduled refresh

A Windows scheduled task, **"StockMonitor Daily"**, runs `run_daily.cmd` at
**06:30 local (SGT)** — the US close at 4pm ET lands at 4-5am here, so this
clears it in both DST regimes.

```powershell
Get-ScheduledTaskInfo -TaskName "StockMonitor Daily"          # last/next run
Start-ScheduledTask   -TaskName "StockMonitor Daily"          # run now
Disable-ScheduledTask -TaskName "StockMonitor Daily"          # pause
Set-ScheduledTask -TaskName "StockMonitor Daily" `
  -Trigger (New-ScheduledTaskTrigger -Daily -At 7:00am)       # change the time
```

It runs as your user, so it only fires while you're logged on. Missing a day
costs nothing: `prices.py` pulls full history every time, so the next run
backfills any gap automatically.

**The ↻ Refresh button on the grid runs the same job on demand.** It invokes
`daily_update.py` directly rather than poking the Windows task, so it takes the
identical code path — same skip logic, same logging, same `pipeline_runs`
record — while staying synchronous enough to report the outcome ("Updated — 12
new price rows" / "Already current"). Starting the Windows task instead would be
fire-and-forget with nothing to show. It refuses to start if a run is already in
flight, since two concurrent derivations would both rewrite `ratios_daily` for
the same tickers; a run still marked `running` after 30 minutes is treated as
crashed and no longer blocks.

The job decides what to do from the data rather than a calendar:

- **New trading session** or **new filing** → re-derive. Both matter. An
  earnings release changes every multiple without adding a single price row.
- **Neither** → record `skipped` and exit. That covers weekends, US holidays,
  the local/ET date offset, and running twice in one day, with no holiday table
  to maintain and nothing to adjust twice a year for DST.

Every run writes a `pipeline_runs` row and a log under `data/logs/`
(60 days retained). The grid reads the latest run and shows "Updated 3m ago ·
session 2026-08-11", turning amber when tickers failed and red when the refresh
looks stopped — a job that quietly dies is the failure that matters, because the
page keeps rendering confident numbers that are wrong by however long it's been
broken.

Exit codes: `0` clean or nothing to do, `1` some tickers failed, `2` crashed.

Maintenance:

```bash
.venv/Scripts/python ingest/manage.py remove INTC --purge
.venv/Scripts/python ingest/manage.py assign INTC Semiconductors --default
.venv/Scripts/python ingest/groups.py --reset       # reseed metric profiles
```

## Tests

```bash
.venv/Scripts/python -m pytest ingest/tests -v
```

The tests that matter most are the lookahead ones. Most bugs here announce
themselves — a broken tag mapping shows up as a missing column, a split error as
a market cap that is obviously 4x wrong. Lookahead bias is different: it
produces a series that looks entirely reasonable and is flattering in a
consistent direction. It has to be tested because it cannot be noticed.

## How it works

```
EDGAR companyfacts ─┐
                    ├─> fundamentals (as-filed, append-only)
                    │        │
yfinance prices ────┴─> prices, splits, dividends
                             │
                             v
                     ratios_daily (derived nightly)
                             │
                             v
                     Next.js grid + deep dive
```

**Fundamentals are as-filed and append-only.** Companies restate prior periods,
so the same (concept, period) legitimately appears more than once with different
values and filing dates. Every version is kept, keyed by the accession that
reported it.

**Derivation is event-driven.** Fundamentals change only when a company files —
4-8 times a year — while prices change daily. So the financial state is computed
once per filing date (~80 states over twenty years) and attached to each trading
day, rather than recomputed for each of ~11,000 days.

**The no-lookahead rule is structural.** A state built for filing date F is
assembled only from rows with `filed_at <= F`, and attached only to days `>= F`.
`ratios_daily.fundamentals_filed_at` records which filing fed each row, so the
invariant is testable rather than merely intended.

## Traps handled

These are real defects in the source data, each of which silently produces
plausible-looking wrong numbers:

**Split restatement.** EDGAR share counts are as-filed; yfinance prices are
retroactively split-adjusted. Apple's FY2019 10-K says 4.649bn diluted shares;
after the 2020 4:1 split, Apple restated the same period to 18.596bn. Pairing
as-filed shares with adjusted prices makes market cap wrong by exactly the split
ratio. Every as-filed count is carried forward through subsequent splits, which
independently reproduces Apple's own restated figure — that agreement is the
test.

**Share counts at the wrong scale.** Filers report share counts in thousands
while tagging the unit as `shares`, in both directions, with nothing in the XBRL
marking it:

| Filer | Reported | Neighbouring filings |
|---|---|---|
| NVIDIA FY2011 10-Q | 590,997 | 574,381,000 |
| AMD 2012-08-02 | 707,555,106,000,000 | ~707,555,106 |
| Apple 2013-14 | 899,213 | ~899,213,000 |

Cross-checking sources against each other is insufficient — with two candidates
the median sits between them. The working anchor is temporal: a share count
never moves 10x in a quarter, so the previously accepted value validates each
new one.

**Predecessor CIKs.** When a company reincorporates, the SEC ticker registry
points at the new registrant, which holds only post-reorganization filings.
XOM resolved to "ExxonMobil Holdings Corp" with 55 facts from 2024; the real
history — 3,434 facts back to 2006 — sits under legacy CIK 34088. Nothing links
them, and the failure is silent. See `CIK_OVERRIDES` in `ingest/config.py`, plus
a short-history warning that catches the next occurrence at ingest time.

**Tag drift.** Companies change tags over time, most visibly at the ASC 606
transition around 2018 when revenue moved from `SalesRevenueNet` to
`RevenueFromContractWithCustomerExcludingAssessedTax`. The normalizer collects
from every tag in a concept's list and resolves collisions by priority;
first-match-wins would truncate history at the switchover.

**D&A is not one tag, and the combined tag goes stale.** Alphabet never tags a
combined D&A line at all. Intel does, but it stops in 2019 while still resolving
to $200m — against current components summing to $11.7bn. Presence alone can't
arbitrate, so whichever source was *filed most recently* wins. Getting this
wrong is expensive: it suppressed Intel's EV/EBITDA entirely, and overstated
AMD's at 196x against a true 118x and Broadcom's at 81x against 61x. Both carry
heavy acquisition amortization, which is exactly what was being dropped.

**Stale rows from an old concept mapping.** `store_fundamentals` replaces a
ticker's rows rather than upserting. An insert-only store leaves facts behind
under their previous concept name, and those keep winning — the D&A fix above
silently did nothing until the stale rows were cleared. Safe to replace
wholesale because companyfacts returns complete history on every call.

**Debt-free is not unknown.** Apple filed no debt tags before 2013 because it
carried no debt. Treating that absence as unknown nulls out enterprise value and
discards exactly the early history the percentile bands need.

## Revenue by product

The deep dive shows which product lines are growing and which are shrinking —
Alphabet's Cloud at +36% against Network at −2%, Apple's Services at +14% against
Wearables at −4% — ranked by growth so decliners group at the bottom.

Segment revenue is tagged with XBRL *dimensions*, and `companyfacts` returns only
undimensioned facts, so it comes from the rendered report tables each filing
publishes (`FilingSummary.xml` → `R*.htm`). Those are kilobytes rather than the
megabytes of inline XBRL, and their labels are already human-readable.

`ingest/segments.py`, annual filings only, ~10 years. Run
`python verify_segments.py` for coverage and a per-product growth summary.

**Waymo is not obtainable.** Alphabet reports it inside "Other Bets" as *a
combination of multiple operating segments that are not individually material*.
No source can supply a figure the company never published.

Scraping a presentation layer is only safe because of one check: **the leaf lines
must sum to the revenue already ingested from XBRL, within 1%.** A filing that
fails is dropped rather than stored. That single test catches the wrong table,
the wrong scale, a subtotal counted as a leaf, and a misread column — each of
which otherwise yields numbers that look perfectly reasonable.

Traps these tables set, all found the hard way:

- **Mixed hierarchy levels.** "Google advertising" is the sum of Search, YouTube
  and Network; "Data Center" is exactly Compute + Networking. Summing every row
  gives Alphabet 637,412 against actual revenue of 402,836. Subtotals are found
  by value — a row matching a contiguous run of its siblings — because no label
  identifies them.
- **Pipe notation reverses between filings.** The 2026 10-K writes
  "child | parent", the 2023 one "parent | child". Orientation is read from the
  data: the parent side has fewer distinct values.
- **`(Detail)` vs `(Details)`.** Microsoft uses the singular throughout.
- **Section prefixes.** "Operating Segments and Geographic Data — Segment Sales
  and Operating Revenues" is the right table; matching "geographic" against the
  full name threw Chevron away. Exclusions test the subject after the last dash.
- **Geography is a fallback tier, not an exclusion.** Netflix reports
  UCAN/EMEA/LATAM/APAC as its segments and publishes no product split.
- **Percentages wearing revenue's clothes.** Concentration-risk tables reuse the
  member names; Palantir's overwrote its dollar rows on insert while
  reconciliation had counted both.

Not every company publishes one: AMD, Chevron and ExxonMobil have no product
breakdown that reconciles, and the section is simply absent for them.

## Known limitations

- **No forward-looking metrics.** EDGAR carries no analyst consensus, so forward
  P/E, PEG and estimate revisions are out. All multiples are trailing. The
  `estimates` table is in the schema for a paid source (FMP/Polygon) later.
- **No next-earnings date** on the grid — it needs a source that isn't ingested.
- **Alphabet EV/EBITDA only spans ~2022 onward**, since Alphabet tagged no D&A
  flow line before Dec 2021. The UI marks percentiles as insufficient below 3
  years rather than implying precision it can't support.
- **No gross margin for energy majors** — Exxon and Chevron don't report cost of
  revenue in a standard tag. It isn't in the Energy profile anyway.
- **US-listed USD filers only.** Foreign issuers reporting in another currency
  (TSM in TWD, ASML in EUR) need an FX series and, for ADRs, an ordinary-shares
  ratio. They're detected and refused rather than silently mispriced. SGX-listed
  companies aren't in EDGAR at all.
- **Prices are previous close**, not live. Irrelevant for valuation.
- **On the grid each stock appears once**, under its default group. The deep dive
  is where you switch readings.
