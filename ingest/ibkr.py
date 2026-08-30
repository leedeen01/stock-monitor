"""IBKR Flex Web Service: fetch a statement, parse it, store it.

Two-step protocol. `SendRequest` queues the report and returns a reference
code; `GetStatement` retrieves it, answering "still generating" until it is
ready. Both can fail with a numeric code rather than an HTTP error, so the
status has to be read out of the XML rather than inferred from a 200.

The token arrives in the environment, not in argv: it is a bearer credential
that reads brokerage statements, and argv is visible in the process list. The
web layer decrypts it and passes it through, so the AES implementation stays in
one place rather than being reimplemented here.

Rate limit is one request per second and ten per minute per token, which is why
the poll below backs off rather than spinning.
"""

import argparse
import os
import sqlite3
import sys
import time
import urllib.request
from xml.etree import ElementTree

import db
import jobs
import credentials as credential_store
import run_log

BASE = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
SEND_REQUEST = f"{BASE}/SendRequest"
GET_STATEMENT = f"{BASE}/GetStatement"
VERSION = "3"

USER_AGENT = "stock-monitor/0.1"
HTTP_TIMEOUT = 60

# The report is built on demand, so "not ready yet" is the normal first answer.
RETRYABLE = {1001, 1004, 1005, 1006, 1007, 1008, 1009, 1019}

# Anything here will not fix itself, so failing immediately beats ten polls.
FATAL = {
    1010: "Legacy Flex queries are no longer supported.",
    1011: "The Flex Web Service account is inactive.",
    1012: "The token has expired. Generate a new one in Client Portal.",
    1013: "Blocked by an IP restriction on the token.",
    1014: "The query id is invalid.",
    1015: "The token is invalid. Check it was copied in full.",
    1016: "The account is invalid for this query.",
    1017: "The reference code is invalid.",
    1018: "Too many requests for this token — one per second, ten per minute.",
    1020: "IBKR rejected the request as invalid.",
    1021: "IBKR could not retrieve the statement.",
}

POLL_DELAYS = (3, 5, 8, 12, 20, 30, 45)   # ~2 minutes total, well under the limit


class FlexError(RuntimeError):
    """A refusal from IBKR, already phrased for a human."""


# --- transport --------------------------------------------------------------


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
        return response.read()


def _status_of(root: ElementTree.Element) -> tuple[str | None, int | None, str | None]:
    """A Flex response reports failure in the body with HTTP 200, so the status
    has to be read rather than assumed."""
    def text(tag: str) -> str | None:
        node = root.find(f".//{tag}")
        return node.text.strip() if node is not None and node.text else None

    code = text("ErrorCode")
    return text("Status"), int(code) if code and code.isdigit() else None, text("ErrorMessage")


def _raise_for(code: int | None, message: str | None) -> None:
    if code in FATAL:
        raise FlexError(FATAL[code])
    raise FlexError(message or f"IBKR returned error code {code}.")


def fetch_statement(token: str, query_id: str, on_step=None) -> bytes:
    """Run the two-step handshake and return the statement XML."""
    def step(text: str) -> None:
        if on_step:
            on_step(text)

    step("Requesting the report from IBKR")
    root = ElementTree.fromstring(
        _get(f"{SEND_REQUEST}?t={token}&q={query_id}&v={VERSION}")
    )
    status, code, message = _status_of(root)
    if status != "Success":
        _raise_for(code, message)

    reference = root.findtext(".//ReferenceCode")
    if not reference:
        raise FlexError("IBKR accepted the request but returned no reference code.")

    # IBKR hands back the URL to collect from; prefer it over our constant so a
    # server move does not break this.
    collect = root.findtext(".//Url") or GET_STATEMENT

    for attempt, delay in enumerate(POLL_DELAYS, start=1):
        time.sleep(delay)
        step(f"Waiting for IBKR to build the report ({attempt})")
        body = _get(f"{collect}?t={token}&q={reference}&v={VERSION}")

        # A ready statement is a FlexQueryResponse; anything else is a status.
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as exc:
            raise FlexError(f"IBKR returned unparseable XML: {exc}") from exc

        if root.tag == "FlexQueryResponse":
            return body

        status, code, message = _status_of(root)
        if code in RETRYABLE:
            continue
        _raise_for(code, message)

    raise FlexError(
        "IBKR did not finish generating the report in time. It may be a large "
        "query — try again in a few minutes."
    )


# --- parsing ----------------------------------------------------------------
#
# Elements are located by tag anywhere in the tree rather than by a fixed path.
# Flex nests sections differently depending on which are enabled, and a rigid
# path would break the day a section is added.


def normalize_symbol(raw: str | None) -> str | None:
    """IBKR's symbol convention into the registry's.

    Class shares are the whole reason this exists: IBKR writes `BRK B` where
    SEC and yfinance write `BRK-B`. Without this a share class silently fails
    to join and the position shows up with no valuation history, looking like
    a data gap rather than a naming difference.
    """
    if not raw:
        return None
    return raw.strip().upper().replace(" ", "-") or None


def _num(value: str | None) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _date(value: str | None) -> str | None:
    """yyyyMMdd to ISO, which is what every other table here stores."""
    if not value:
        return None
    raw = str(value).split(";")[0].strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw or None


def parse(xml: bytes) -> dict:
    root = ElementTree.fromstring(xml)

    positions = []
    for el in root.iter("OpenPosition"):
        # SUMMARY and LOT rows can both appear; taking both would double-count.
        if (el.get("levelOfDetail") or "SUMMARY").upper() != "SUMMARY":
            continue
        symbol = normalize_symbol(el.get("symbol"))
        if not symbol:
            continue
        positions.append({
            "report_date": _date(el.get("reportDate")),
            "ticker": symbol,
            "conid": el.get("conid"),
            "asset_class": el.get("assetCategory"),
            "currency": el.get("currency"),
            "quantity": _num(el.get("position") or el.get("quantity")),
            "cost_basis_price": _num(el.get("costBasisPrice")),
            "cost_basis_money": _num(el.get("costBasisMoney")),
            "mark_price": _num(el.get("markPrice")),
            "position_value": _num(el.get("positionValue")),
            "unrealized_pnl": _num(el.get("fifoPnlUnrealized")),
            "percent_of_nav": _num(el.get("percentOfNAV")),
        })

    trades = []
    for el in root.iter("Trade"):
        trade_id = (el.get("tradeID") or "").strip()
        if not trade_id:
            continue
        trades.append({
            "trade_id": trade_id,
            "ticker": normalize_symbol(el.get("symbol")),
            "conid": el.get("conid"),
            "asset_class": el.get("assetCategory"),
            "currency": el.get("currency"),
            "trade_date": _date(el.get("tradeDate")),
            "buy_sell": el.get("buySell"),
            "quantity": _num(el.get("quantity")),
            "price": _num(el.get("tradePrice")),
            "commission": _num(el.get("ibCommission")),
            "net_cash": _num(el.get("netCash")),
            "open_close": el.get("openCloseIndicator"),
            "cost_basis": _num(el.get("cost")),
            "realized_pnl": _num(el.get("fifoPnlRealized")),
        })

    cash = []
    for el in root.iter("CashReportCurrency"):
        cash.append({
            "report_date": _date(el.get("toDate")),
            "currency": el.get("currency"),
            "starting_cash": _num(el.get("startingCash")),
            "ending_cash": _num(el.get("endingCash")),
            "dividends": _num(el.get("dividends")),
            "withholding_tax": _num(el.get("withholdingTax")),
            "deposits_withdrawals": _num(el.get("depositWithdrawals")),
            "interest": _num(el.get("brokerInterest")),
        })

    nav = []
    for el in root.iter("EquitySummaryByReportDateInBase"):
        nav.append({
            "report_date": _date(el.get("reportDate")),
            "cash": _num(el.get("cash")),
            "stock": _num(el.get("stock")),
            "total": _num(el.get("total")),
        })

    return {"positions": positions, "trades": trades, "cash": cash, "nav": nav}


# --- storage ----------------------------------------------------------------


def store(conn: sqlite3.Connection, user_id: int, parsed: dict) -> dict[str, int]:
    """Upsert everything for one user. Re-running the same statement is a no-op
    rather than a duplication, which is what makes a daily job safe."""
    counts = {}

    # conid and asset class describe the instrument, not the holding, so they
    # live on `tickers` rather than being repeated once per position per day.
    # A held symbol we have never ingested still earns a registry row here —
    # that is what lets the daily job discover it and fetch its filings.
    conn.executemany(
        """
        INSERT INTO tickers (ticker, ibkr_conid, asset_class, first_seen_at)
        VALUES (:ticker, :conid, :asset_class, datetime('now'))
        ON CONFLICT(ticker) DO UPDATE SET
            ibkr_conid = COALESCE(excluded.ibkr_conid, tickers.ibkr_conid),
            asset_class = COALESCE(excluded.asset_class, tickers.asset_class)
        """,
        [p for p in parsed["positions"] if p["ticker"]],
    )

    conn.executemany(
        """
        INSERT INTO holdings (user_id, report_date, ticker,
            currency, quantity, cost_basis_price, cost_basis_money, mark_price,
            position_value, unrealized_pnl, percent_of_nav)
        VALUES (:user_id, :report_date, :ticker,
            :currency, :quantity, :cost_basis_price, :cost_basis_money,
            :mark_price, :position_value, :unrealized_pnl, :percent_of_nav)
        ON CONFLICT(user_id, report_date, ticker) DO UPDATE SET
            quantity = excluded.quantity,
            cost_basis_price = excluded.cost_basis_price,
            cost_basis_money = excluded.cost_basis_money,
            mark_price = excluded.mark_price,
            position_value = excluded.position_value,
            unrealized_pnl = excluded.unrealized_pnl,
            percent_of_nav = excluded.percent_of_nav
        """,
        [{**p, "user_id": user_id} for p in parsed["positions"] if p["report_date"]],
    )
    counts["holdings"] = len([p for p in parsed["positions"] if p["report_date"]])

    conn.executemany(
        """
        INSERT INTO ibkr_trades (user_id, trade_id, ticker,
            currency, trade_date, buy_sell, quantity, price, commission,
            net_cash, open_close, cost_basis, realized_pnl)
        VALUES (:user_id, :trade_id, :ticker, :currency,
            :trade_date, :buy_sell, :quantity, :price, :commission, :net_cash,
            :open_close, :cost_basis, :realized_pnl)
        ON CONFLICT(user_id, trade_id) DO NOTHING
        """,
        [{**t, "user_id": user_id} for t in parsed["trades"]],
    )
    counts["trades"] = len(parsed["trades"])

    conn.executemany(
        """
        INSERT INTO ibkr_cash (user_id, report_date, currency, starting_cash,
            ending_cash, dividends, withholding_tax, deposits_withdrawals, interest)
        VALUES (:user_id, :report_date, :currency, :starting_cash, :ending_cash,
            :dividends, :withholding_tax, :deposits_withdrawals, :interest)
        ON CONFLICT(user_id, report_date, currency) DO UPDATE SET
            starting_cash = excluded.starting_cash,
            ending_cash = excluded.ending_cash,
            dividends = excluded.dividends,
            withholding_tax = excluded.withholding_tax,
            deposits_withdrawals = excluded.deposits_withdrawals,
            interest = excluded.interest
        """,
        [{**c, "user_id": user_id} for c in parsed["cash"]
         if c["report_date"] and c["currency"]],
    )
    counts["cash"] = len([c for c in parsed["cash"] if c["report_date"] and c["currency"]])

    conn.executemany(
        """
        INSERT INTO ibkr_nav (user_id, report_date, cash, stock, total)
        VALUES (:user_id, :report_date, :cash, :stock, :total)
        ON CONFLICT(user_id, report_date) DO UPDATE SET
            cash = excluded.cash, stock = excluded.stock, total = excluded.total
        """,
        [{**n, "user_id": user_id} for n in parsed["nav"] if n["report_date"]],
    )
    counts["nav"] = len([n for n in parsed["nav"] if n["report_date"]])

    conn.commit()
    return counts


def record_sync(conn, user_id: int, status: str, detail: str) -> None:
    conn.execute(
        "UPDATE ibkr_links SET last_sync_at = datetime('now'), "
        "last_sync_status = ?, last_sync_detail = ? WHERE user_id = ?",
        (status, detail[:500], user_id),
    )
    conn.commit()


def unmatched_tickers(conn, user_id: int) -> list[str]:
    """Holdings whose symbol is not in the shared registry.

    Not an error — you can hold something you never added to a watchlist — but
    worth surfacing, since those positions have no valuation history behind
    them and would otherwise appear as blanks with no explanation.
    """
    return [
        r["ticker"]
        for r in conn.execute(
            """
            SELECT DISTINCT h.ticker FROM holdings h
            LEFT JOIN tickers t ON t.ticker = h.ticker
            WHERE h.user_id = ? AND t.cik IS NULL
              AND h.report_date = (SELECT MAX(report_date) FROM holdings WHERE user_id = ?)
            ORDER BY h.ticker
            """,
            (user_id, user_id),
        )
    ]


def credentials(conn, user_id: int) -> tuple[str, str] | None:
    """Query id and decrypted token for one user, or None if unusable.

    The scheduled job has no Node process to ask, which is why ingest/secrets.py
    exists. A token that will not decrypt is treated as absent rather than
    raising: the link needs re-entering, and that is a message not a crash.
    """
    row = conn.execute(
        "SELECT flex_query_id, token_cipher FROM ibkr_links WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return None
    token = credential_store.decrypt(row["token_cipher"])
    if not token:
        return None
    return row["flex_query_id"], token


def linked_users(conn) -> list[int]:
    return [r["user_id"] for r in conn.execute(
        "SELECT user_id FROM ibkr_links ORDER BY user_id")]


def sync_all(conn, verbose: bool = True) -> dict:
    """Sync every linked account. One failure does not stop the others.

    Returns the union of symbols held but absent from the registry, so the
    caller can decide to ingest them — a ticker you own is a strong signal you
    want its filings, even if it never reached a watchlist.
    """
    unmatched: set[str] = set()
    ok = failed = 0

    for user_id in linked_users(conn):
        creds = credentials(conn, user_id)
        if not creds:
            record_sync(conn, user_id, "error",
                        "Stored token could not be read — relink the account.")
            failed += 1
            continue
        query_id, token = creds
        try:
            result = sync(conn, user_id, token, query_id, verbose=verbose)
            unmatched.update(result["unmatched"])
            ok += 1
        except Exception as exc:  # noqa: BLE001 - one account must not stop the rest
            record_sync(conn, user_id, "error", f"{type(exc).__name__}: {exc}")
            if verbose:
                print(f"  user {user_id}: FAILED - {exc}")
            failed += 1

    return {"ok": ok, "failed": failed, "unmatched": sorted(unmatched)}


# --- entry point ------------------------------------------------------------


def sync(conn, user_id: int, token: str, query_id: str,
         job_id: int | None = None, verbose: bool = True) -> dict:
    def step(text: str) -> None:
        jobs.set_step(conn, job_id, text)
        if verbose:
            print(f"  {text}")

    xml = fetch_statement(token, query_id, on_step=step)

    step("Parsing the statement")
    parsed = parse(xml)

    step("Storing holdings")
    counts = store(conn, user_id, parsed)

    unmatched = unmatched_tickers(conn, user_id)
    detail = (
        f"{counts['holdings']} position(s), {counts['trades']} trade(s), "
        f"{counts['nav']} NAV row(s)."
    )
    if unmatched:
        detail += f" Not on any watchlist: {', '.join(unmatched[:8])}."

    record_sync(conn, user_id, "ok", detail)
    if verbose:
        print(detail)
    return {**counts, "unmatched": unmatched, "detail": detail}


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync IBKR holdings via Flex")
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--job-id", type=int, default=None)
    args = parser.parse_args()

    conn = db.connect()
    db.init_schema(conn)

    # The web layer passes credentials through the environment, since it has
    # already decrypted them. Falling back to the database is what lets the
    # scheduled job run with nobody logged in.
    token = os.environ.get("IBKR_FLEX_TOKEN", "").strip()
    query_id = os.environ.get("IBKR_QUERY_ID", "").strip()
    if not token or not query_id:
        creds = credentials(conn, args.user_id)
        if creds:
            query_id, token = creds

    if not token or not query_id:
        message = "No IBKR credentials supplied — relink the account."
        record_sync(conn, args.user_id, "error", message)
        jobs.finish(conn, args.job_id, "error", message)
        print(message, file=sys.stderr)
        return 1

    # Every exit reports before the connection closes. Closing first and
    # reporting after is a mistake this project has already made once.
    try:
        result = sync(conn, args.user_id, token, query_id, args.job_id)
        jobs.finish(conn, args.job_id, "ok", result["detail"])
        return 0
    except FlexError as exc:
        record_sync(conn, args.user_id, "error", str(exc))
        jobs.finish(conn, args.job_id, "error", str(exc))
        print(f"IBKR sync failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - the UI needs something to show
        message = f"{type(exc).__name__}: {exc}"
        record_sync(conn, args.user_id, "error", message)
        jobs.finish(conn, args.job_id, "error", message)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run_log.tee_stdio("ibkr")
    raise SystemExit(main())
