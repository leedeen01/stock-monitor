"""What differs between one market and another, in one place.

Adding a market should be an entry in this file and nothing else. Every part of
the pipeline that behaves differently for a London or Singapore listing asks
here rather than branching on the ticker, so a change to SGX cannot reach a US
stock — there is no shared code path to break.

Three things vary, and only three:

  fundamentals  which filings source can value it, if any. Only the SEC is
                wired up, so everything else is priced rather than valued.
  price_suffix  what yfinance calls it. `D05` is `D05.SI`; US listings need
                nothing appended.
  currency      what its prices are in. Never converted — see below.

**Nothing is converted anywhere.** Currency is carried so it can be shown next
to the number. A price in SGD rendered as though it were dollars is worse than
no price at all, and converting properly needs historical rates applied to
every derived figure — the code path where this project has had the most
silent corruption. Aggregates refuse to mix currencies instead.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Market:
    code: str
    label: str
    currency: str
    #: Filings provider, or None when the market has no source we can value from.
    fundamentals: str | None
    #: Appended to the ticker to get the yfinance symbol.
    price_suffix: str
    note: str = ""

    @property
    def valuable(self) -> bool:
        """Whether metrics are possible here at all."""
        return self.fundamentals is not None


MARKETS: dict[str, Market] = {
    "US": Market(
        code="US",
        label="US",
        currency="USD",
        fundamentals="edgar",
        price_suffix="",
        note="SEC filers. The only market with fundamentals behind it.",
    ),
    "LSE": Market(
        code="LSE",
        label="London",
        currency="USD",
        fundamentals=None,
        price_suffix=".L",
        note=(
            "LSE companies file with the FCA, not the SEC, and UCITS funds file "
            "nowhere we can read. Priced, not valued. Many London listings are "
            "quoted in USD, which is why the currency is checked per ticker "
            "rather than assumed from the market."
        ),
    ),
    "SGX": Market(
        code="SGX",
        label="Singapore",
        currency="SGD",
        fundamentals="yfinance",
        price_suffix=".SI",
        note=(
            "SGX sells prices and connectivity, not company financials, and "
            "structured filings go to ACRA rather than a public API. "
            "Fundamentals come from yfinance instead: around five annual "
            "periods, with results assumed public 75 days after period end "
            "since no filing dates are available. Priced in SGD, never "
            "converted."
        ),
    ),
}

DEFAULT_MARKET = "US"

# yfinance reports an exchange code rather than a market name.
EXCHANGE_TO_MARKET = {
    "NMS": "US", "NYQ": "US", "NGM": "US", "NCM": "US",
    "ASE": "US", "PCX": "US", "BTS": "US", "PNK": "US",
    "LSE": "LSE",
    "SES": "SGX",
}


def get(code: str | None) -> Market:
    """The profile for a market code, falling back to US.

    Unknown codes fall back rather than raising: a ticker with a market we do
    not recognise should behave like the common case, not break the run.
    """
    return MARKETS.get((code or DEFAULT_MARKET).upper(), MARKETS[DEFAULT_MARKET])


def from_exchange(exchange: str | None) -> str | None:
    """Map a yfinance exchange code to one of ours, or pass it through."""
    if not exchange:
        return None
    return EXCHANGE_TO_MARKET.get(exchange.upper(), exchange.upper())


def price_symbol(ticker: str, market: str | None) -> str:
    """What yfinance should be asked for.

    Only applied when the ticker does not already carry a suffix — a symbol
    given as `SPYL.L` is already qualified and must not become `SPYL.L.L`.
    """
    ticker = ticker.strip().upper()
    if "." in ticker:
        return ticker
    return f"{ticker}{get(market).price_suffix}"


def codes() -> list[str]:
    """Market codes, US first, for anything offering a choice."""
    rest = sorted(c for c in MARKETS if c != DEFAULT_MARKET)
    return [DEFAULT_MARKET, *rest]
