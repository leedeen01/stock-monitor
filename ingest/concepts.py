"""XBRL tag normalization.

Companies tag the same economic idea differently, and the same company changes
its tags over time — most notably at the ASC 606 transition around 2018, where
revenue moved from `SalesRevenueNet` to
`RevenueFromContractWithCustomerExcludingAssessedTax`.

So this layer does *not* do first-match-wins on the tag list. It collects facts
from every tag in the list and resolves collisions by priority. First-match
would silently truncate history at the tag switchover, and a percentile band
built on truncated history is wrong in a way nothing downstream would flag.

Two taxonomies are supported:

  * us-gaap   - domestic filers (10-K/10-Q), quarterly granularity
  * ifrs-full - foreign private issuers filing 20-F, e.g. TSM

IFRS support is deliberately limited. 20-F filers report ANNUALLY ONLY, so an
IFRS ticker yields roughly one data point per year rather than four. Percentile
bands built on ~10 annual points are far coarser than the ~40 quarterly points a
domestic filer gives. `is_annual_only_filer` flags these so the UI can say so
rather than implying equivalent precision. Note that not every foreign issuer is
IFRS — ASML files 20-F but tags under us-gaap, so it needs no special handling.

Conventions for the rows produced here:
  * duration concepts  -> period_start set, duration_days > 0
  * instant concepts   -> period_start None, duration_days 0 (0 not NULL, so the
                          primary key actually constrains)
  * cash outflows (capex, buybacks, dividends) stay positive, as filed
"""

from dataclasses import dataclass, field
from datetime import date

DURATION = "duration"
INSTANT = "instant"

GAAP_NS = "us-gaap"
IFRS_NS = "ifrs-full"


@dataclass(frozen=True)
class ConceptSpec:
    name: str
    tags: tuple[str, ...]  # priority ordered, highest first
    kind: str
    namespace: str = GAAP_NS
    ifrs: tuple[str, ...] = field(default=())

    def sources(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        out = ((self.namespace, self.tags),)
        if self.ifrs:
            out += ((IFRS_NS, self.ifrs),)
        return out


CONCEPTS: tuple[ConceptSpec, ...] = (
    # --- income statement -------------------------------------------------
    ConceptSpec("revenue", (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ), DURATION, ifrs=("Revenue", "RevenueFromContractsWithCustomers")),
    ConceptSpec("cost_of_revenue", (
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
        "CostOfServices",
    ), DURATION, ifrs=("CostOfSales",)),
    # Many filers (Alphabet, Meta, Chevron) never tag GrossProfit. derive.py
    # falls back to revenue - cost_of_revenue rather than dropping the metric.
    ConceptSpec("gross_profit", ("GrossProfit",), DURATION, ifrs=("GrossProfit",)),
    # Energy majors often omit OperatingIncomeLoss entirely, which is why
    # EBITDA has a pretax + interest + D&A fallback path in derive.py.
    ConceptSpec("operating_income", ("OperatingIncomeLoss",), DURATION,
                ifrs=("ProfitLossFromOperatingActivities",)),
    ConceptSpec("net_income", (
        "NetIncomeLoss",
        "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    ), DURATION, ifrs=("ProfitLossAttributableToOwnersOfParent", "ProfitLoss")),
    ConceptSpec("pretax_income", (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ), DURATION, ifrs=("ProfitLossBeforeTax",)),
    ConceptSpec("income_tax", ("IncomeTaxExpenseBenefit",), DURATION,
                ifrs=("IncomeTaxExpenseContinuingOperations",)),
    ConceptSpec("interest_expense", (
        "InterestExpense",
        "InterestExpenseDebt",
        "InterestExpenseNonoperating",
    ), DURATION, ifrs=("FinanceCosts",)),
    ConceptSpec("eps_diluted", ("EarningsPerShareDiluted",), DURATION,
                ifrs=("DilutedEarningsLossPerShare",)),
    ConceptSpec("shares_diluted", (
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
    ), DURATION, ifrs=(
        "AdjustedWeightedAverageNumberOfOrdinarySharesOutstanding",
        "WeightedAverageNumberOfOrdinarySharesOutstandingDiluted",
    )),

    # --- cash flow --------------------------------------------------------
    ConceptSpec("operating_cash_flow", (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ), DURATION, ifrs=("CashFlowsFromUsedInOperatingActivities",)),
    ConceptSpec("capex", (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsForCapitalImprovements",
    ), DURATION, ifrs=(
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    )),
    # D&A generally appears only on the cash flow statement, which is why
    # EBITDA has to be assembled rather than read off a tag.
    #
    # Deliberately NOT falling back to bare "Depreciation" here: that tag is
    # depreciation only, and using it as though it were D&A understates the
    # add-back, understates EBITDA and inflates EV/EBITDA. Alphabet is the case
    # in point — it never tags a combined D&A line. Bare depreciation is picked
    # up as its own concept below and summed with amortization instead.
    ConceptSpec("depreciation_amortization", (
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
    ), DURATION, ifrs=("DepreciationAndAmortisationExpense",)),
    # Filers that split D&A across two lines — Alphabet under us-gaap, TSM under
    # IFRS. derive.py sums these when the combined concept above is absent.
    ConceptSpec("depreciation_expense", ("Depreciation",), DURATION,
                ifrs=("DepreciationExpense",)),
    ConceptSpec("amortisation_expense", ("AmortizationOfIntangibleAssets",), DURATION,
                ifrs=("AmortisationExpense",)),
    ConceptSpec("stock_based_compensation", (
        "ShareBasedCompensation",
        "AllocatedShareBasedCompensationExpense",
    ), DURATION),
    ConceptSpec("dividends_paid", (
        "PaymentsOfDividendsCommonStock",
        "PaymentsOfDividends",
    ), DURATION, ifrs=("DividendsPaidClassifiedAsFinancingActivities", "DividendsPaid")),
    ConceptSpec("buybacks", ("PaymentsForRepurchaseOfCommonStock",), DURATION),

    # --- balance sheet ----------------------------------------------------
    ConceptSpec("cash", (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ), INSTANT, ifrs=("CashAndCashEquivalents",)),
    ConceptSpec("short_term_investments", (
        "ShortTermInvestments",
        "MarketableSecuritiesCurrent",
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
    ), INSTANT),
    ConceptSpec("long_term_debt", (
        "LongTermDebtNoncurrent",
        "LongTermDebt",
    ), INSTANT, ifrs=("LongtermBorrowings", "BorrowingsNoncurrent")),
    ConceptSpec("short_term_debt", (
        "LongTermDebtCurrent",
        "DebtCurrent",
        "ShortTermBorrowings",
    ), INSTANT, ifrs=("ShorttermBorrowings",)),
    ConceptSpec("inventory", ("InventoryNet",), INSTANT, ifrs=("Inventories",)),
    ConceptSpec("total_assets", ("Assets",), INSTANT, ifrs=("Assets",)),
    ConceptSpec("total_equity", (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ), INSTANT, ifrs=("EquityAttributableToOwnersOfParent", "Equity")),
    ConceptSpec("current_assets", ("AssetsCurrent",), INSTANT, ifrs=("CurrentAssets",)),
    ConceptSpec("current_liabilities", ("LiabilitiesCurrent",), INSTANT,
                ifrs=("CurrentLiabilities",)),
    ConceptSpec(
        "shares_outstanding",
        ("EntityCommonStockSharesOutstanding",),
        INSTANT,
        namespace="dei",
    ),
    # Multi-class companies (Alphabet, Meta) tag weighted-average diluted shares
    # per share class, and companyfacts drops dimensional facts — Alphabet's
    # undimensioned diluted count only begins in 2022. This balance-sheet tag
    # goes back much further and is the fallback that keeps market cap, and so
    # every multiple, from vanishing for the earlier years.
    ConceptSpec("shares_outstanding_gaap", ("CommonStockSharesOutstanding",), INSTANT),
)

# Duration windows we keep, in days. Anything else (odd stub periods, multi-year
# cumulative facts) is dropped. H and 9M are retained because some filers report
# only year-to-date figures, and deriving a clean Q4 needs them.
DURATION_BUCKETS = (
    (80, 100, "Q"),
    (170, 190, "H"),
    (260, 285, "9M"),
    (340, 380, "FY"),
)

# Concepts that legitimately go missing for whole classes of company. Absence
# here is information, not a mapping bug, so the coverage report shouldn't cry
# wolf: software firms hold no inventory, non-payers report no dividend.
OPTIONAL_CONCEPTS = frozenset({
    "inventory", "dividends_paid", "buybacks", "short_term_debt",
    "short_term_investments", "stock_based_compensation", "gross_profit",
    "shares_outstanding", "shares_outstanding_gaap", "depreciation_expense",
    "amortisation_expense", "interest_expense", "current_assets",
    "current_liabilities", "long_term_debt",
})


def _bucket(days: int) -> str | None:
    for low, high, label in DURATION_BUCKETS:
        if low <= days <= high:
            return label
    return None


def _parse(d: str) -> date:
    return date.fromisoformat(d)


def detect_taxonomy(facts: dict) -> str:
    """Which taxonomy a filer uses. Drives the annual-only warning."""
    namespaces = facts.get("facts", {})
    if namespaces.get(GAAP_NS):
        return GAAP_NS
    if namespaces.get(IFRS_NS):
        return IFRS_NS
    return "unknown"


def is_annual_only_filer(facts: dict) -> bool:
    """True for 20-F filers, whose fundamentals arrive once a year."""
    return detect_taxonomy(facts) == IFRS_NS


def normalize_companyfacts(ticker: str, facts: dict) -> list[dict]:
    """Turn a raw companyfacts payload into canonical fundamental rows.

    Pure function — no I/O, no database. Everything here is testable against a
    fixture.
    """
    ticker = ticker.upper()
    all_facts = facts.get("facts", {})
    # key -> (tag_priority, row). Lower priority number wins.
    resolved: dict[tuple, tuple[int, dict]] = {}

    for spec in CONCEPTS:
        priority = 0
        for namespace, tags in spec.sources():
            namespace_facts = all_facts.get(namespace, {})

            for tag in tags:
                priority += 1
                tag_data = namespace_facts.get(tag)
                if not tag_data:
                    continue

                for unit, entries in tag_data.get("units", {}).items():
                    for entry in entries:
                        row = _build_row(ticker, spec, unit, entry)
                        if row is None:
                            continue

                        key = (
                            row["concept"],
                            row["period_end"],
                            row["accession"],
                            row["duration_days"],
                        )
                        existing = resolved.get(key)
                        if existing is None or priority < existing[0]:
                            resolved[key] = (priority, row)

    rows = [row for _, row in resolved.values()]
    rows.sort(key=lambda r: (r["concept"], r["period_end"], r["filed_at"]))
    return rows


def _build_row(ticker: str, spec: ConceptSpec, unit: str, entry: dict) -> dict | None:
    end = entry.get("end")
    filed = entry.get("filed")
    accession = entry.get("accn")
    value = entry.get("val")

    if not end or not filed or accession is None or value is None:
        return None

    if spec.kind == DURATION:
        start = entry.get("start")
        if not start:
            return None
        days = (_parse(end) - _parse(start)).days
        if _bucket(days) is None:
            return None
        period_start, duration_days = start, days
    else:
        period_start, duration_days = None, 0

    return {
        "ticker": ticker,
        "concept": spec.name,
        "period_start": period_start,
        "period_end": end,
        "duration_days": duration_days,
        "fiscal_year": entry.get("fy"),
        "fiscal_period": entry.get("fp"),
        "form": entry.get("form"),
        "filed_at": filed,
        "accession": accession,
        "unit": unit,
        "value": float(value),
    }


MONETARY_CONCEPTS = frozenset({"revenue", "net_income", "total_assets", "cash", "total_equity"})


def detect_reporting_currency(rows: list[dict]) -> str | None:
    """The currency a filer reports in — not necessarily the one it trades in.

    Mixing a USD share price with EUR or TWD fundamentals produces ratios that
    are wrong by the exchange rate while looking completely reasonable, so this
    gets recorded and checked rather than assumed.
    """
    counts: dict[str, int] = {}
    for row in rows:
        if row["concept"] in MONETARY_CONCEPTS and row.get("unit"):
            counts[row["unit"]] = counts.get(row["unit"], 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def coverage_report(rows: list[dict]) -> dict[str, int]:
    """Fact count per concept — the quickest way to spot a broken tag mapping."""
    counts: dict[str, int] = {spec.name: 0 for spec in CONCEPTS}
    for row in rows:
        counts[row["concept"]] = counts.get(row["concept"], 0) + 1
    return counts


# Concepts that can be assembled from others, so their absence is only a
# problem when the components are missing too. derive.py builds each of these.
DERIVABLE_FROM = {
    "depreciation_amortization": ("depreciation_expense", "amortisation_expense"),
    "gross_profit": ("revenue", "cost_of_revenue"),
    "operating_income": ("pretax_income",),
}


def missing_required(rows: list[dict]) -> list[str]:
    """Absent concepts that actually signal a problem worth looking at."""
    counts = coverage_report(rows)
    missing = set()
    for key, count in counts.items():
        if count or key in OPTIONAL_CONCEPTS:
            continue
        components = DERIVABLE_FROM.get(key)
        if components and any(counts.get(c, 0) for c in components):
            continue  # derive.py will assemble it
        missing.add(key)
    return sorted(missing)
