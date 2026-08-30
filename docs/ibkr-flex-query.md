# IBKR Flex Query — configuration reference

The exact shape of the Activity Flex Query the app reads. This is the contract:
the importer is written against these fields, so changing the query without
changing the parser is how holdings quietly stop updating.

Recreate the query from this document if it is ever lost. IBKR Client Portal →
**Performance & Reports → Flex Queries → Activity Flex Query → +**

**Why Flex rather than the trading APIs.** The Client Portal and TWS APIs need a
gateway process and a daily re-login, which is unworkable on a headless NAS.
Flex is a plain HTTPS GET authenticated by a token valid up to a year. The
trade is that data refreshes once overnight — which suits an app whose prices
are already previous-close.

---

## Query-level settings

| Setting | Value | Why |
|---|---|---|
| Query name | `Stock Watch` | anything; the numeric **query id** is what the app stores |
| Format | **XML** | the parser expects XML, and `lxml` is already in the image |
| Period | **Last 365 Calendar Days** | one period governs every section — see below |
| Symbols filter | *empty* | empty means all holdings; a filter would silently hide new positions |
| Include Derivatives | No | long-only equities |
| Date Format | `yyyyMMdd` | unambiguous, sorts lexically |
| Time Format | `HHmmss` | |
| Date/Time Separator | `;` | |
| Profit and Loss | Default | |
| Include Offsetting Trade/Cancel Pairs | **No** | cancelled trades would need filtering out again |
| Include Currency Rates | No | USD-only account |
| Include Audit Trail Fields | No | order-routing detail |
| Account Alias in place of Account ID | **No** | the ID is stable, an alias is editable |
| **Breakout by Day** | **No** | Yes emits one row *per position per day* — thousands instead of ~13 |

### On the period

A Flex query has exactly one period, applied to every section. Positions are a
snapshot as of the period end regardless, so a wide period costs nothing there
and buys a year of trades and dividends. `Last Business Day` would give correct
holdings and almost no history.

For a longer trade history than 365 days, run a **separate one-off query** with
a custom date range rather than widening this one.

---

## Compulsory vs optional

The app degrades rather than breaks. Each tier below adds features; only the
first is required for the link to be useful at all.

### Tier 1 — required

Without these the importer has nothing to store.

**Section: Open Positions**, level of detail **Summary**

| Field | Used for |
|---|---|
| `Symbol` | joining to the `tickers` registry |
| `Quantity` | position size |
| `CostBasisMoney` | total cost — the basis of every return figure |
| `ReportDate` | which day the snapshot is, and idempotent re-import |
| `AssetClass` | filtering to `STK`; without it an option row tries to match a ticker |
| `CurrencyPrimary` | guarding against non-USD holdings being mixed into USD totals |

### Tier 2 — strongly recommended

| Field | Section | Unlocks |
|---|---|---|
| `CostBasisPrice` | Open Positions | per-share entry vs current multiple |
| `PositionValue` | Open Positions | position weighting |
| `MarkPrice` | Open Positions | cross-check against our own price data |
| `PercentOfNAV` | Open Positions | IBKR's own weighting, as an independent check |
| `FifoPnlUnrealized` | Open Positions | unrealised P/L without recomputing it |
| `Conid` | Open Positions | stable identity — symbols get renamed and reused, conids do not |
| `EndingCash` | Cash Report | cash %, and the denominator for true portfolio weight |

### Tier 3 — optional

Everything else. Trades, Corporate Actions, Cash Transactions, NAV and
Financial Instrument Information each enable a specific feature and can be
added later. **Editing the query does not invalidate the token.**

---

## Sections, in full

Fields marked **bold** are the ones the parser actually reads today or is
designed to. The rest are selected because they are free to carry and awkward
to add later.

### 1. Open Positions — *required*

Level of detail: **Summary** (not Lot — one row per holding, not per tax lot).

**Select:** Account ID · Currency · FX Rate To Base · **Asset Class** · Sub
Category · **Symbol** · Description · **Conid** · ISIN · Listing Exchange ·
**Report Date** · **Quantity** · **Mark Price** · **Position Value** · **Cost
Basis Price** · **Cost Basis Money** · **Percent of NAV** · **Unrealized P/L** ·
Side · Level of Detail · Open Date Time

**Skip:** Model, Security ID/Type, CUSIP, FIGI, all Underlying fields, Issuer
fields, Multiplier, Strike, Expiry, Put/Call, Principal Adjust Factor, Open
Price, Holding Period Date Time, Vesting Date, Code, Originating Order/
Transaction ID, Accrued Interest, Serial Number, Delivery Type, Commodity Type,
Fineness, Weight.

> **Open Price is not your entry price.** It is the session's opening basis.
> Cost Basis Price is the one you want, and confusing them produces plausible
> numbers that are wrong for months.

### 2. Cash Report

Options: **Exclude segments and MTD/YTD breakout** ✓ · **Base Currency
Summary** ✓ · Currency Breakout ✗ (USD-only)

**Select:** Account ID · **Currency** · From Date · To Date · Level of Detail ·
Starting Cash · **Ending Cash** · Ending Settled Cash · **Deposit/Withdrawals** ·
Net Trades Sales · Net Trades Purchases · **Dividends** · **Withholding Tax** ·
Broker Interest Paid and Received

**Skip:** everything else — CFD, Paxos, HK IPO, debit card, seven kinds of
billable sales tax, SLB.

> `Deposit/Withdrawals` is what separates "cash rose because I added money" from
> "cash rose because I sold". Without it, cash % reads as performance.

### 3. Trades

Level of detail: **Order** only. *Execution* splits one order into every partial
fill; *Symbol Summary* and *Asset Class* aggregate the trades away.

**Select:** Account ID · Currency · FX Rate To Base · **Asset Class** ·
**Symbol** · Description · **Conid** · **Trade ID** · **Trade Date** ·
**Date/Time** · **Buy/Sell** · **Quantity** · **TradePrice** · Trade Money ·
Proceeds · **IB Commission** · Net Cash · **Open/Close Indicator** ·
Transaction Type · Exchange · Cost Basis · Realized P/L · Level Of Detail ·
Notes/Codes

**Skip:** every order-plumbing id (IB Order ID, Brokerage Order ID, Exch Order
ID, External Execution ID, RTN, Clearing Firm ID, Trader ID, Is API Order,
Request ID, Position Action ID, Order Reference, Volatility Order Link), all
options fields, the `Orig Trade *` set, MTM P/L, Change In Price/Quantity,
Settle Date Target, Accrued Interest, Initial Investment, commodity block.

> **Trade ID is the dedup key.** Without it, re-running the import
> double-counts every trade and the damage is silent — cost basis just drifts.
> `fundamentals` is keyed by SEC accession for exactly the same reason.

### 4. Corporate Actions

Level of detail: **Detail**.

**Select:** Account ID · Currency · Asset Class · **Symbol** · Description ·
**Conid** · **Report Date** · Date/Time · **Action Description** · **Type** ·
**Action ID** · Transaction ID · **Quantity** · Amount · Proceeds · Value ·
Cost Basis · Realized P/L · Code · Level of Detail

**Skip:** options block, commodity block, Security ID/Type, CUSIP, FIGI, Issuer
fields, Principal Adjust Factor, MTM PNL.

> Usually empty, and worth having anyway. A 4:1 split mishandled makes
> historical P/E wrong by exactly 4x and looks entirely plausible — this project
> has already hit that class of bug three times. An independent record of every
> split from the broker is the cheapest possible check. The ratio appears in
> `Action Description` as text (`... SPLIT 10 FOR 1`); `Type` classifies it.

### 5. Cash Transactions

Types: **Dividends** · **Withholding Tax** · **Payment in Lieu of Dividends** ·
**Broker Interest Received** · Broker Interest Paid · **Deposits &
Withdrawals** · level of detail **Detail**

**Select:** Account ID · Currency · FX Rate To Base · Asset Class · **Symbol** ·
Description · **Conid** · **Date/Time** · Settle Date · **Ex Date** · Report
Date · **Amount** · **Type** · **Dividend Type** · **Transaction ID** · Action
ID · Code · Level of Detail

**Skip:** 871(m) (derivatives), bond interest, advisor fees, carbon credits,
bill pay, price/commission adjustments, Client Reference, Trade ID, Available
For Trading Date, options and commodity blocks.

> Withholding arrives as its own negative row rather than netted off the
> dividend, so both types are needed to see what actually landed. A Singapore
> resident holding US equities has 30% withheld at source.
>
> `Ex Date` is what matches a dividend to the holding that earned it. Pay date
> cannot — the position may have been sold in between.

### 6. Net Asset Value (NAV) in Base

Options: **Exclude prior report date** ✓ · **Exclude long and short breakout** ✓

**Select:** Account ID · Currency · **Report Date** · **Cash** · **Stock** ·
Dividend Accruals · Interest Accruals · **Total**

**Skip:** SLB, IPO, CFD, crypto, commodities, soft dollars, and the dozen
individual accrual components — all zero here.

### 7. Financial Instrument Information

A reference table; identity only.

**Select:** **Conid** · **Symbol** · Description · Asset Class · Sub Category ·
Currency · **ISIN** · Listing Exchange · Issuer · Issuer Country Code · Code

**Skip:** Security ID/Type, CUSIP, FIGI (ISIN already gives unambiguous
identity), all Underlying and options fields, Maturity, Issue Date, Underlying
Category, Settlement Policy Method, commodity block.

---

## Credentials

Two values, both entered in the app at **/link-ibkr** — never in the compose
file, never in a commit:

- **Query ID** — the numeric id beside the query in the Flex Queries list
- **Token** — Flex Web Service Configuration → gear → enable → generate

The token is read-only: it fetches statements and cannot place trades. The app
encrypts it with AES-256-GCM under `ENCRYPTION_KEY` before storage, never
returns it to the browser and never writes it to a log.

Tokens expire within a year. When one does, the symptom is a portfolio that
silently stops updating, which is why `ibkr_links.last_sync_at` is surfaced on
the portfolio page.
