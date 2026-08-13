"""Metric registry — one definition per metric, referenced everywhere by key.

Adding a metric is one entry here. Adding a group is a list of keys. No group
gets its own rendering code, which is what keeps "I'll add groupings on the go"
from meaning "I'll add a code path on the go".

`key` matches a column in ratios_daily, so the registry and the derived table
stay in lockstep.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Sections of the deep dive, in render order.
PAYING = "paying"        # what you pay - the multiples
GETTING = "getting"      # what you get - growth, margins, returns
INTEGRITY = "integrity"  # per-share integrity - dilution, SBC, buybacks
LEVERAGE = "leverage"    # can it survive - only for cyclical groups

SECTION_LABELS = {
    PAYING: "What you're paying",
    GETTING: "What you're getting",
    INTEGRITY: "Per-share integrity",
    LEVERAGE: "Leverage",
}

# A percentile band needs enough history to mean anything. Alphabet's EV/EBITDA
# only spans ~3 years because it never tagged D&A before 2021 — showing that as
# a "10-year percentile" would imply precision the data cannot support.
MIN_YEARS_FOR_PERCENTILE = 3


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    fmt: str                      # multiple | percent | currency | number | days | ratio
    section: str
    description: str              # what it is
    usage: str = ""               # how to act on it — shown in the info popover
    higher_is_better: bool | None = None   # None = neither, context decides
    show_percentile: bool = False
    invert_percentile: bool = False        # true when a HIGH percentile is good
    # Ratios and margins compare across companies; absolute quantities do not.
    # NVIDIA's 24bn shares against a peer median of 4.9bn says nothing about
    # either company, so those metrics suppress the group comparison.
    peer_comparable: bool = True


def _m(*args, **kwargs) -> Metric:
    return Metric(*args, **kwargs)


METRICS: tuple[Metric, ...] = (
    # --- what you're paying ------------------------------------------------
    _m("pe_ttm", "P/E (TTM)", "multiple", PAYING,
       "Price over trailing twelve-month earnings. Null when earnings are negative — "
       "a negative P/E would sort as the cheapest stock on the page.",
       usage=(
           "Read the percentile, not the number. 'Under 15 is cheap' is folklore — "
           "what matters is where this sits in the stock's own range.\n\n"
           "Beware the cyclical trap: P/E is at its LOWEST near a cycle peak, because "
           "earnings peak there. A semiconductor or energy name that looks cheap on "
           "P/E may be at the point of maximum risk. For those, lead with EV/EBITDA."
       ),
       higher_is_better=False, show_percentile=True),
    _m("ev_ebitda", "EV/EBITDA", "multiple", PAYING,
       "Enterprise value over EBITDA. Neutral to capital structure, so it compares "
       "companies with different debt loads — which P/E cannot.",
       usage=(
           "The multiple to use when comparing companies with different debt loads, "
           "or one company across time as its leverage changes.\n\n"
           "Always read it next to Net Debt/EBITDA. A low EV/EBITDA on heavy leverage "
           "is the classic value trap — cheap because the balance sheet may not "
           "survive a downturn, not because the market is wrong."
       ),
       higher_is_better=False, show_percentile=True),
    _m("ev_sales", "EV/Sales", "multiple", PAYING,
       "The fallback when earnings are negative or noisy. The only multiple that "
       "still works on a pre-profit company.",
       usage=(
           "Only meaningful alongside gross margin. 10x sales at an 80% gross margin "
           "is a completely different business from 10x at 30% — the first can grow "
           "into the multiple, the second mostly cannot.\n\n"
           "Use it when earnings are negative or too noisy to trust, and treat a high "
           "reading as a growth requirement: the company has to deliver years of it "
           "before the multiple makes sense."
       ),
       higher_is_better=False, show_percentile=True),
    _m("ev_fcf", "EV/FCF", "multiple", PAYING,
       "Enterprise value over free cash flow. Harder to manipulate than any "
       "earnings-based multiple.",
       usage=(
           "The most trustworthy multiple here, because cash is far harder to massage "
           "than earnings.\n\n"
           "Compare it to EV/EBITDA. A wide gap between the two means heavy capex or "
           "working-capital drag that EBITDA is quietly hiding."
       ),
       higher_is_better=False, show_percentile=True),
    _m("fcf_yield", "FCF Yield", "percent", PAYING,
       "Free cash flow over market cap. The one valuation metric directly "
       "comparable to something outside the stock market, like a Treasury yield.",
       usage=(
           "Set this against the 10-year Treasury. If a mature, low-growth business "
           "yields less than a risk-free bond, you are paying entirely for growth that "
           "has to actually arrive.\n\n"
           "A very high yield is rarely a free lunch — above roughly 8% the market is "
           "usually pricing in decline. The question is whether you disagree."
       ),
       higher_is_better=True, show_percentile=True, invert_percentile=True),
    _m("ps_ttm", "P/S", "multiple", PAYING,
       "Price over trailing sales.",
       usage=(
           "Same use as EV/Sales, but it ignores debt. Prefer EV/Sales whenever "
           "leverage is material; P/S is mainly useful for net-cash companies where "
           "the two nearly agree."
       ),
       higher_is_better=False, show_percentile=True),
    _m("pb", "P/B", "multiple", PAYING,
       "Price over book value. Primary for banks and asset-heavy businesses, "
       "close to meaningless for asset-light software.",
       usage=(
           "Genuinely useful for banks, insurers and heavy industry, where book value "
           "approximates what the company owns. Below 1.0 on a bank means the market "
           "doubts the stated asset values.\n\n"
           "Close to meaningless for software: book value excludes the brand, the code "
           "and the people that actually generate the returns."
       ),
       higher_is_better=False, show_percentile=True),

    # --- what you're getting -----------------------------------------------
    _m("revenue_growth_yoy", "Revenue Growth YoY", "percent", GETTING,
       "Growth in trailing-twelve-month revenue against a year earlier. The input "
       "that justifies any multiple.",
       usage=(
           "Watch the trend, not the level. Decelerating growth at a high multiple is "
           "the most reliable way to lose money in this market — the multiple "
           "compresses at the same time earnings disappoint.\n\n"
           "For cyclicals, read it backwards: peak growth is a warning, not a signal."
       ),
       higher_is_better=True),
    _m("gross_margin", "Gross Margin", "percent", GETTING,
       "Separates genuine software economics from reselling hardware.",
       usage=(
           "The clearest read on business-model quality and the hardest line to fake. "
           "Rising means pricing power or a better mix; falling means competition or "
           "input costs.\n\n"
           "For anything marketed as an 'AI' company, this is the tell: real software "
           "economics run 70%+, while reselling someone else's hardware runs far lower "
           "however the revenue growth looks."
       ),
       higher_is_better=True),
    _m("operating_margin", "Operating Margin", "percent", GETTING,
       "EBIT over revenue. The spread against gross margin is where operating "
       "leverage shows up.",
       usage=(
           "Read the gap between this and gross margin — that gap is operating "
           "expense. A widening gap on flat gross margin means the company is buying "
           "its growth.\n\n"
           "Direction matters far more than level, and levels are only comparable "
           "within an industry."
       ),
       higher_is_better=True),
    _m("net_margin", "Net Margin", "percent", GETTING, "Net income over revenue.",
       usage=(
           "Distorted by one-off charges, tax rates and interest. Use it as a sanity "
           "check on P/E, but judge the business itself on operating margin."
       ),
       higher_is_better=True),
    _m("fcf_margin", "FCF Margin", "percent", GETTING,
       "Free cash flow over revenue — how much of a sale becomes cash.",
       usage=(
           "How much of each dollar of sales becomes cash you could actually take out. "
           "The gap against net margin is capex and working capital — a persistent gap "
           "means the business needs feeding to stand still."
       ),
       higher_is_better=True),
    _m("roic", "ROIC", "percent", GETTING,
       "Return on invested capital. Neutral to capital structure, unlike ROE, which "
       "leverage and buybacks inflate. Compare against cost of capital: growth only "
       "creates value above it.",
       usage=(
           "The real test of whether growth is worth anything. Compare against cost of "
           "capital, roughly 8-10% for most companies.\n\n"
           "Above it, growth compounds value. Below it, growth destroys value no "
           "matter how fast the top line moves. A business earning 30% ROIC and "
           "reinvesting heavily is close to the best thing you can own."
       ),
       higher_is_better=True),
    _m("roe", "ROE", "percent", GETTING,
       "Return on equity. Flattered by leverage and buybacks — a company that buys "
       "back stock until book equity nears zero shows an absurd ROE. Prefer ROIC.",
       usage=(
           "Treat with suspicion. Leverage and buybacks both inflate it, so a heavily "
           "indebted company can post a spectacular ROE while earning poor returns on "
           "the capital it actually employs.\n\n"
           "Companies that have bought back stock until book equity approaches zero "
           "produce absurd or negative readings that mean nothing. Use ROIC instead, "
           "except for banks where ROE is the industry standard."
       ),
       higher_is_better=True),
    _m("fcf_conversion", "FCF Conversion", "ratio", GETTING,
       "Free cash flow over net income. The earnings-quality tripwire: sustained "
       "readings below ~0.8 mean reported profit isn't becoming cash.",
       usage=(
           "The earnings-quality tripwire. One weak quarter is noise; below ~0.8 for "
           "several consecutive quarters means reported profit isn't turning into "
           "cash.\n\n"
           "When it does, look at receivables, inventory and capitalised costs — that "
           "is usually where the difference is sitting. Sustained above 1.0 is the "
           "mark of a genuinely cash-generative model."
       ),
       higher_is_better=True),
    _m("inventory_days", "Inventory Days", "days", GETTING,
       "Inventory over daily cost of revenue. The classic semiconductor cycle tell — "
       "inventory building faster than revenue precedes a downturn.",
       usage=(
           "The semiconductor cycle's earliest warning, and the reason it sits in that "
           "group's profile. Inventory turns over before earnings do.\n\n"
           "A sharp rise while revenue growth is flattening is the classic pre-"
           "downturn signature: product has stopped moving, but the income statement "
           "hasn't admitted it yet."
       ),
       higher_is_better=False),

    # --- per-share integrity -----------------------------------------------
    _m("shares_diluted", "Diluted Shares", "number", INTEGRITY,
       "Share count over time. Dilution silently erodes per-share value: 15% revenue "
       "growth against 7% dilution is a very different investment from 15% against 0%. "
       "Watch the trend, not the level.",
       usage=(
           "Watch the trend; the level tells you nothing on its own.\n\n"
           "Rising 5-8% a year roughly halves your ownership over a decade. That is "
           "why 15% revenue growth against 7% dilution is a completely different "
           "investment from 15% against zero — the second grows your slice, the first "
           "barely does.\n\n"
           "Falling means buybacks, which only create value when done below intrinsic "
           "value. Many companies buy back hardest at the top."
       ),
       higher_is_better=False, peer_comparable=False),
    _m("sbc_pct_revenue", "SBC % of Revenue", "percent", INTEGRITY,
       "Stock-based compensation as a share of revenue. The real cost behind dilution.",
       usage=(
           "The cost behind the dilution above, and a real expense however the company "
           "presents its adjusted numbers.\n\n"
           "Above roughly 10% of revenue is heavy. Compare within the group rather "
           "than across — norms differ enormously between software and hardware."
       ),
       higher_is_better=False),

    # --- leverage ----------------------------------------------------------
    _m("net_debt_ebitda", "Net Debt/EBITDA", "multiple", LEVERAGE,
       "Leverage against cash generation. The number that decides whether a cyclical "
       "survives the trough.",
       usage=(
           "The number that decides whether a cyclical survives its trough. Under 2x "
           "is comfortable; over 4x is fragile. Negative means net cash.\n\n"
           "For a cyclical, judge it against TROUGH EBITDA rather than today's. "
           "Leverage that looks fine at the peak can be fatal when earnings halve — "
           "which is exactly when the multiple looked cheapest."
       ),
       higher_is_better=False),
    _m("interest_coverage", "Interest Coverage", "multiple", LEVERAGE,
       "EBIT over interest expense. More predictive of distress than the debt level.",
       usage=(
           "More predictive of distress than the debt level itself, because it asks "
           "whether the company can actually service what it owes.\n\n"
           "Below about 3x leaves no room if earnings fall. Above 10x means debt is "
           "simply not an issue worth thinking about."
       ),
       higher_is_better=True),
    _m("capex_pct_revenue", "Capex % of Revenue", "percent", LEVERAGE,
       "Capital intensity, and whether management is holding discipline through the cycle.",
       usage=(
           "Capital intensity, and the main lever management controls in semis and "
           "energy.\n\n"
           "A sharp rise is either a growth investment cycle or a treadmill. Check "
           "whether ROIC rises with it — if capex climbs and returns don't, the "
           "spending is maintaining the business rather than growing it."
       ),
       higher_is_better=None),
    _m("net_debt", "Net Debt", "currency", LEVERAGE,
       "Total debt less cash. Negative means net cash.",
       usage=(
           "Negative means net cash. Read it alongside Net Debt/EBITDA rather than "
           "alone — the absolute figure means little without the cash generation "
           "standing behind it."
       ),
       higher_is_better=False, peer_comparable=False),
)

BY_KEY: dict[str, Metric] = {m.key: m for m in METRICS}


def get(key: str) -> Metric:
    return BY_KEY[key]


def export_json(path: Path) -> None:
    """Emit the registry for the frontend, so labels, formats and descriptions
    have exactly one source of truth."""
    payload = {
        "metrics": [asdict(m) for m in METRICS],
        "sections": SECTION_LABELS,
        "minYearsForPercentile": MIN_YEARS_FOR_PERCENTILE,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
