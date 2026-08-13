"""SEC EDGAR client.

Two endpoints carry the whole fundamentals side of this project:

  * company_tickers.json  - ticker -> CIK mapping
  * companyfacts          - every XBRL fact a company has ever filed, each one
                            carrying the date it was filed

That second point is what makes EDGAR the right source here. Commercial APIs
hand you current ratios; only EDGAR tells you *when each number became public*,
which is the difference between an honest percentile band and one biased by
hindsight.
"""

import json
import time
from pathlib import Path

import requests

from config import (
    CIK_OVERRIDES,
    DATA_DIR,
    SEC_COMPANYFACTS_URL,
    SEC_MIN_INTERVAL_SECONDS,
    SEC_TICKERS_URL,
    SEC_USER_AGENT,
)

CACHE_DIR = DATA_DIR / "cache"
_last_request_at = 0.0


def _throttle() -> None:
    """Keep well under the SEC's 10 req/sec ceiling."""
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < SEC_MIN_INTERVAL_SECONDS:
        time.sleep(SEC_MIN_INTERVAL_SECONDS - elapsed)
    _last_request_at = time.monotonic()


def _get(url: str) -> dict:
    _throttle()
    response = requests.get(
        url,
        headers={"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def get_ticker_cik_map(refresh: bool = False) -> dict[str, dict]:
    """Map ticker -> {cik, name}. Cached; the file changes rarely."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / "company_tickers.json"

    if cache_file.exists() and not refresh:
        raw = json.loads(cache_file.read_text(encoding="utf-8"))
    else:
        raw = _get(SEC_TICKERS_URL)
        cache_file.write_text(json.dumps(raw), encoding="utf-8")

    return {
        entry["ticker"].upper(): {"cik": int(entry["cik_str"]), "name": entry["title"]}
        for entry in raw.values()
    }


def resolve_ticker(ticker: str) -> dict:
    """Look up a ticker's CIK and registered company name.

    Returns `cik` (the current registrant) and `ciks` (every CIK whose filings
    belong to this ticker, current first). They differ only for companies that
    reincorporated — see CIK_OVERRIDES.
    """
    ticker = ticker.upper()
    mapping = get_ticker_cik_map()
    if ticker not in mapping:
        # A stale cache is the likely cause for a recent listing.
        mapping = get_ticker_cik_map(refresh=True)
    if ticker not in mapping:
        raise KeyError(f"{ticker} not found in SEC ticker registry")

    meta = dict(mapping[ticker])
    override = CIK_OVERRIDES.get(ticker)
    if override:
        meta["ciks"] = list(override)
        meta["cik"] = override[0]
    else:
        meta["ciks"] = [meta["cik"]]
    return meta


def get_companyfacts(cik: int, max_age_days: int = 1) -> dict:
    """Fetch a company's full XBRL fact set.

    Cached on disk: these payloads run 5-20MB and only change when the company
    files, so refetching during development is pure waste.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"companyfacts_{cik:010d}.json"

    if cache_file.exists():
        age_days = (time.time() - cache_file.stat().st_mtime) / 86400
        if age_days < max_age_days:
            return json.loads(cache_file.read_text(encoding="utf-8"))

    facts = _get(SEC_COMPANYFACTS_URL.format(cik=cik))
    cache_file.write_text(json.dumps(facts), encoding="utf-8")
    return facts
