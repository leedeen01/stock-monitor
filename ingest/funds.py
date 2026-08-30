"""Tickers that have prices but no filings.

ETFs, UCITS funds, foreign ordinaries. Real to hold, impossible to value from
SEC filings because there are none — an S&P 500 tracker has no income
statement, so P/E and every percentile in this app are not merely missing but
meaningless for it.

They still earn a place: a price series, a chart, and a since-bought return.
This module works out which yfinance symbol to use, and refuses anything not
quoted in USD.
"""

import warnings

warnings.filterwarnings("ignore")

import yfinance as yf  # noqa: E402

# IBKR reports the local symbol; yfinance wants an exchange suffix. Ordered by
# how likely a USD-quoted listing is, since that is the only kind we accept.
SUFFIXES = ("", ".L", ".AS", ".DE", ".SW", ".PA", ".MI")

BASE_CURRENCY = "USD"


class NotPriceable(RuntimeError):
    """No usable USD listing, phrased for a human."""


def resolve(symbol: str) -> dict:
    """Find a usable USD-quoted listing for `symbol`.

    Two checks, both learned the hard way.

    Currency is verified rather than assumed: SPYL is quoted in USD on the LSE
    and in EUR in Amsterdam, and taking whichever answered first would mix EUR
    prices into a USD portfolio — every derived figure wrong by the exchange
    rate while looking entirely plausible.

    Price history is required, not just a quote. The bare symbol SPYL resolves
    to an entirely different fund — SPDR MSCI EM Latin America — which reports
    a USD price and has no history at all. A currency check alone accepted it,
    which would have tracked the wrong security silently.

    Passing an exchange-qualified symbol (SPYL.L) skips the guessing entirely,
    and is the reliable way to do this: short codes are reused across
    exchanges for unrelated funds.
    """
    symbol = symbol.strip().upper()

    # Already qualified — trust it rather than probing alternatives.
    candidates = [symbol] if "." in symbol else [f"{symbol}{sfx}" for sfx in SUFFIXES]
    tried: list[str] = []

    for candidate in candidates:
        tried.append(candidate)
        try:
            handle = yf.Ticker(candidate)
            info = handle.info or {}
            currency = (info.get("currency") or "").upper()
            if currency != BASE_CURRENCY:
                continue

            # A quote without history is not something we can chart or hold a
            # cost basis against, and is the signature of a wrong match.
            history = handle.history(period="1mo", auto_adjust=False)
            if history is None or history.empty or history["Close"].notna().sum() < 5:
                continue
        except Exception:  # noqa: BLE001 - an unknown symbol is not exceptional
            continue

        return {
            "price_symbol": candidate,
            "name": info.get("longName") or info.get("shortName") or candidate,
            "currency": currency,
            "quote_type": (info.get("quoteType") or "").upper(),
            "exchange": info.get("exchange"),
        }

    raise NotPriceable(
        f"No {BASE_CURRENCY}-quoted listing with price history found for "
        f"{symbol} (tried {', '.join(tried)}). Try the exchange-qualified "
        f"symbol, such as {symbol}.L for the London listing."
    )


def register(conn, symbol: str) -> dict:
    """Record a priceable fund in the registry."""
    found = resolve(symbol)
    conn.execute(
        """
        INSERT INTO tickers
            (ticker, name, kind, price_symbol, reporting_currency,
             supported, unsupported_reason, first_seen_at)
        VALUES (?, ?, 'fund', ?, ?, 1, NULL, datetime('now'))
        ON CONFLICT(ticker) DO UPDATE SET
            name = excluded.name,
            kind = 'fund',
            price_symbol = excluded.price_symbol,
            reporting_currency = excluded.reporting_currency,
            supported = 1,
            unsupported_reason = NULL
        """,
        (symbol.strip().upper(), found["name"], found["price_symbol"], found["currency"]),
    )
    conn.commit()
    return found
