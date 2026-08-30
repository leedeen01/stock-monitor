"""Fundamentals for markets with no filings source we can read.

Singapore, principally. SGX sells prices and connectivity, not company
financials; structured XBRL goes to ACRA rather than to a public API. yfinance
carries income statement, balance sheet and cash flow for SGX names, so that is
what this reads.

Two honest limitations, both visible in the UI rather than hidden:

**Around five annual periods, not ten years.** Percentile bands will say "5y
only" rather than pretending to a decade. The app already refuses to compute a
percentile from too little history.

**No filing dates.** This is the real cost. The EDGAR path knows exactly when
each number became public, which is what makes a ratio for a past date honest.
yfinance gives period ends and nothing else, so results are assumed known
PUBLICATION_LAG_DAYS after the period closes. That is an approximation, and it
is deliberately generous: assuming you knew FY2024 earnings on 31 December
2024 would be lookahead, and would quietly flatter every historical percentile.

Singapore companies also report half-yearly rather than quarterly, so the
series is coarser than a US equivalent regardless of source.
"""

import sqlite3
import warnings

warnings.filterwarnings("ignore")

import yfinance as yf  # noqa: E402

import markets  # noqa: E402

#: How long after a period ends before its results are treated as public.
#: SGX requires announcement within 60 days of a half or full year; 75 leaves
#: room and errs towards knowing things later rather than earlier.
PUBLICATION_LAG_DAYS = 75

ANNUAL_DAYS = 365

# yfinance line item -> this project's concept. First match wins, so the
# preferred source for a concept comes first.
#
# Absences here are usually correct rather than gaps: a bank has no gross
# profit or inventory, and those metrics are hidden rather than shown empty.
INCOME = {
    "revenue": ("Total Revenue", "Operating Revenue"),
    "net_income": ("Net Income", "Net Income Common Stockholders"),
    "operating_income": ("Operating Income", "EBIT"),
    "gross_profit": ("Gross Profit",),
    "cost_of_revenue": ("Cost Of Revenue", "Reconciled Cost Of Revenue"),
    "pretax_income": ("Pretax Income",),
    "income_tax": ("Tax Provision",),
    "interest_expense": ("Interest Expense", "Interest Expense Non Operating"),
    "eps_diluted": ("Diluted EPS",),
    "shares_diluted": ("Diluted Average Shares", "Basic Average Shares"),
}

BALANCE = {
    "total_assets": ("Total Assets",),
    "total_equity": ("Stockholders Equity", "Common Stock Equity"),
    "cash": ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"),
    "short_term_investments": ("Other Short Term Investments",),
    "long_term_debt": ("Long Term Debt", "Long Term Debt And Capital Lease Obligation"),
    "short_term_debt": ("Current Debt", "Current Debt And Capital Lease Obligation"),
    "current_assets": ("Current Assets",),
    "current_liabilities": ("Current Liabilities",),
    "inventory": ("Inventory",),
    "shares_outstanding": ("Ordinary Shares Number", "Share Issued"),
}

CASHFLOW = {
    "operating_cash_flow": ("Operating Cash Flow",),
    "capex": ("Capital Expenditure",),
    "depreciation_amortization": ("Depreciation And Amortization", "Reconciled Depreciation"),
    "buybacks": ("Repurchase Of Capital Stock",),
    "dividends_paid": ("Cash Dividends Paid", "Common Stock Dividend Paid"),
    "stock_based_compensation": ("Stock Based Compensation",),
}

#: Balance sheet figures are a snapshot; the rest cover a period.
INSTANT = set(BALANCE)


def _filed_at(period_end) -> str:
    from datetime import timedelta

    return (period_end + timedelta(days=PUBLICATION_LAG_DAYS)).date().isoformat()


def _extract(frame, mapping, period_end, ticker, unit) -> list[dict]:
    if frame is None or frame.empty or period_end not in frame.columns:
        return []

    rows = []
    column = frame[period_end]
    for concept, candidates in mapping.items():
        for label in candidates:
            if label not in frame.index:
                continue
            value = column.get(label)
            if value is None or value != value:      # NaN
                continue
            instant = concept in INSTANT
            rows.append({
                "ticker": ticker,
                "concept": concept,
                "period_start": None,
                "period_end": period_end.date().isoformat(),
                "duration_days": None if instant else ANNUAL_DAYS,
                "fiscal_year": period_end.year,
                "fiscal_period": "FY",
                "form": "yfinance",
                "filed_at": _filed_at(period_end),
                # Synthetic, but it is what makes the primary key work and a
                # re-run idempotent — the same role SEC accession plays.
                "accession": f"yf-{ticker}-{period_end.date().isoformat()}",
                "unit": unit,
                "value": float(value),
            })
            break                                    # first match wins
    return rows


def backfill(conn: sqlite3.Connection, ticker: str, verbose: bool = True) -> dict:
    """Pull annual fundamentals for one ticker and store them as-filed."""
    ticker = ticker.upper()
    row = conn.execute(
        "SELECT market, price_symbol, quote_currency FROM tickers WHERE ticker = ?",
        (ticker,),
    ).fetchone()
    market_code = row["market"] if row else None
    symbol = (row["price_symbol"] if row and row["price_symbol"]
              else markets.price_symbol(ticker, market_code))
    unit = (row["quote_currency"] if row and row["quote_currency"]
            else markets.get(market_code).currency)

    handle = yf.Ticker(symbol)
    income, balance, cash = handle.income_stmt, handle.balance_sheet, handle.cashflow

    periods = sorted(
        {c for frame in (income, balance, cash)
         if frame is not None and not frame.empty
         for c in frame.columns},
        reverse=True,
    )
    if not periods:
        raise ValueError(f"{ticker}: yfinance returned no financial statements")

    rows: list[dict] = []
    for period_end in periods:
        rows += _extract(income, INCOME, period_end, ticker, unit)
        rows += _extract(balance, BALANCE, period_end, ticker, unit)
        rows += _extract(cash, CASHFLOW, period_end, ticker, unit)

    conn.executemany(
        """
        INSERT OR REPLACE INTO fundamentals
            (ticker, concept, period_start, period_end, duration_days, fiscal_year,
             fiscal_period, form, filed_at, accession, unit, value)
        VALUES (:ticker, :concept, :period_start, :period_end, :duration_days,
                :fiscal_year, :fiscal_period, :form, :filed_at, :accession,
                :unit, :value)
        """,
        rows,
    )
    conn.execute(
        "UPDATE tickers SET supported = 1, unsupported_reason = NULL WHERE ticker = ?",
        (ticker,),
    )
    conn.commit()

    concepts = {r["concept"] for r in rows}
    if verbose:
        span = f"{periods[-1].date()} -> {periods[0].date()}"
        print(f"{ticker}: {len(rows)} facts, {len(concepts)} concepts, "
              f"{len(periods)} annual periods ({span}), unit {unit}")
    return {"facts": len(rows), "concepts": sorted(concepts), "periods": len(periods)}
