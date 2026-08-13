"""Central configuration for the ingest pipeline."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.environ.get("STOCK_MONITOR_DB", DATA_DIR / "stocks.db"))

# The SEC requires a User-Agent identifying who is making the request, with a
# real contact address. Requests without one get 403'd. See
# https://www.sec.gov/os/webmaster-faq#developers
SEC_CONTACT = os.environ.get("SEC_CONTACT", "lmaodeen@gmail.com")
SEC_USER_AGENT = f"stock-monitor/0.1 ({SEC_CONTACT})"

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# The SEC's published ceiling is 10 requests/second. We stay well under it —
# companyfacts returns a company's entire filing history in one call, so even a
# full backfill is one request per ticker.
SEC_MIN_INTERVAL_SECONDS = 0.15

# How far back to pull daily prices. Ten years gives percentile bands enough
# history to span at least one full cycle for the semiconductor and energy groups.
PRICE_HISTORY_YEARS = 10

# When a company reincorporates or forms a holding company, the SEC ticker
# registry points at the NEW registrant, which carries only post-reorganization
# filings. The history lives under the predecessor CIK. Nothing in the registry
# links them, and the failure is silent — you get a plausible-looking handful of
# facts instead of twenty years.
#
# Ticker -> every CIK to merge, current registrant first.
CIK_OVERRIDES: dict[str, list[int]] = {
    # ExxonMobil Holdings Corp (2024 reorg) + legacy Exxon Mobil Corporation.
    "XOM": [2115436, 34088],
}

# Below this many years of filing history, assume a predecessor CIK exists and
# warn. Cheap heuristic, but it catches the failure mode above at ingest time
# rather than when a percentile band looks strangely flat.
MIN_EXPECTED_HISTORY_YEARS = 5
