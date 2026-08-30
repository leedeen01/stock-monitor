"""IBKR Flex parsing and storage.

Built against synthetic statements rather than a live account, so these run
without credentials and cover the shapes a real one only produces occasionally:
a LOT row alongside its SUMMARY, a missing section, a symbol nobody watches,
and the same statement arriving twice.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import ibkr
from conftest import seed_user


def _statement(positions="", trades="", cash="", nav="") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="Stock Watch" type="AF">
  <FlexStatements count="1">
    <FlexStatement accountId="U1234567" fromDate="20250830" toDate="20260830">
      <OpenPositions>{positions}</OpenPositions>
      <Trades>{trades}</Trades>
      <CashReport>{cash}</CashReport>
      <EquitySummaryInBase>{nav}</EquitySummaryInBase>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>""".encode()


POSITION = (
    '<OpenPosition accountId="U1234567" currency="USD" assetCategory="STK" '
    'symbol="AAPL" conid="265598" reportDate="20260828" position="100" '
    'markPrice="310.65" positionValue="31065" costBasisPrice="150.25" '
    'costBasisMoney="15025" percentOfNAV="12.5" fifoPnlUnrealized="16040" '
    'levelOfDetail="SUMMARY" />'
)


def test_parses_a_position():
    parsed = ibkr.parse(_statement(positions=POSITION))
    assert len(parsed["positions"]) == 1
    p = parsed["positions"][0]
    assert p["ticker"] == "AAPL"
    assert p["quantity"] == 100
    assert p["cost_basis_price"] == 150.25
    # yyyyMMdd is converted, because every other table here stores ISO dates.
    assert p["report_date"] == "2026-08-28"


def test_lot_rows_are_ignored():
    """A query with lot detail emits both; counting both doubles the position."""
    lot = POSITION.replace('levelOfDetail="SUMMARY"', 'levelOfDetail="LOT"')
    parsed = ibkr.parse(_statement(positions=POSITION + lot))
    assert len(parsed["positions"]) == 1, "LOT rows must not be counted alongside SUMMARY"


def test_missing_sections_are_not_an_error():
    """Someone will untick a section. That should cost the feature, not the sync."""
    parsed = ibkr.parse(_statement(positions=POSITION))
    assert parsed["trades"] == []
    assert parsed["cash"] == []
    assert parsed["nav"] == []


def test_numbers_with_thousands_separators():
    p = POSITION.replace('costBasisMoney="15025"', 'costBasisMoney="1,502,500.75"')
    parsed = ibkr.parse(_statement(positions=p))
    assert parsed["positions"][0]["cost_basis_money"] == 1502500.75


def test_blank_numeric_attributes_become_null():
    p = POSITION.replace('percentOfNAV="12.5"', 'percentOfNAV=""')
    parsed = ibkr.parse(_statement(positions=p))
    assert parsed["positions"][0]["percent_of_nav"] is None


def test_storing_twice_does_not_duplicate(tmp_path):
    """The daily job re-fetches the same statement whenever nothing has changed."""
    conn = db.connect(tmp_path / "ibkr.db")
    db.init_schema(conn)
    seed_user(conn)

    trade = (
        '<Trade tradeID="99887766" symbol="AAPL" conid="265598" currency="USD" '
        'assetCategory="STK" tradeDate="20260801" buySell="BUY" quantity="10" '
        'tradePrice="300" ibCommission="-1.02" netCash="-3001.02" '
        'openCloseIndicator="O" />'
    )
    parsed = ibkr.parse(_statement(positions=POSITION, trades=trade))

    ibkr.store(conn, 1, parsed)
    ibkr.store(conn, 1, parsed)

    assert conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM ibkr_trades").fetchone()[0] == 1
    conn.close()


def test_a_later_snapshot_is_a_new_row_not_an_overwrite(tmp_path):
    """Holdings are a daily series — yesterday's weights stay available."""
    conn = db.connect(tmp_path / "series.db")
    db.init_schema(conn)
    seed_user(conn)

    ibkr.store(conn, 1, ibkr.parse(_statement(positions=POSITION)))
    later = POSITION.replace('reportDate="20260828"', 'reportDate="20260829"')
    ibkr.store(conn, 1, ibkr.parse(_statement(positions=later)))

    assert conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0] == 2
    conn.close()


def test_holdings_are_scoped_to_their_user(tmp_path):
    conn = db.connect(tmp_path / "tenant.db")
    db.init_schema(conn)
    seed_user(conn, 1)
    seed_user(conn, 2)

    ibkr.store(conn, 1, ibkr.parse(_statement(positions=POSITION)))

    assert conn.execute(
        "SELECT COUNT(*) FROM holdings WHERE user_id = 2").fetchone()[0] == 0
    conn.close()


def test_holding_something_unresearched_registers_it_and_says_so(tmp_path):
    """You can hold what you never researched.

    Every held symbol earns a registry row, which is what lets the daily job
    discover it and fetch its filings. Until that happens it has no CIK and so
    no valuation history, and the sync reports it rather than leaving a row of
    blanks with no explanation.
    """
    conn = db.connect(tmp_path / "unmatched.db")
    db.init_schema(conn)
    seed_user(conn)
    # AAPL is already ingested; a CIK is what says so.
    conn.execute(
        "INSERT INTO tickers (ticker, name, cik, supported) VALUES ('AAPL','Apple',320193,1)")
    conn.commit()

    other = POSITION.replace('symbol="AAPL"', 'symbol="TSLA"')
    ibkr.store(conn, 1, ibkr.parse(_statement(positions=POSITION + other)))

    assert conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0] == 2
    # TSLA is now in the registry, so tomorrow's run will ingest it...
    assert conn.execute(
        "SELECT COUNT(*) FROM tickers WHERE ticker = 'TSLA'").fetchone()[0] == 1
    # ...but has no filings yet, so it is reported as lacking history.
    assert ibkr.unmatched_tickers(conn, 1) == ["TSLA"]
    conn.close()


def test_conid_is_stored_once_on_the_instrument(tmp_path):
    """Not once per position per day — it describes the instrument."""
    conn = db.connect(tmp_path / "conid.db")
    db.init_schema(conn)
    seed_user(conn)

    ibkr.store(conn, 1, ibkr.parse(_statement(positions=POSITION)))
    later = POSITION.replace('reportDate="20260828"', 'reportDate="20260829"')
    ibkr.store(conn, 1, ibkr.parse(_statement(positions=later)))

    assert conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0] == 2
    assert conn.execute(
        "SELECT ibkr_conid FROM tickers WHERE ticker='AAPL'").fetchone()[0] == "265598"
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(holdings)")]
    assert "conid" not in cols, "instrument attributes must not repeat per row"
    conn.close()


# --- protocol -------------------------------------------------------------


def _response(body: str) -> bytes:
    return f'<?xml version="1.0"?>{body}'.encode()


def test_a_fatal_error_code_is_explained_not_retried(monkeypatch):
    """An expired token will not fix itself, so polling seven times is waste and
    the message should say what to do."""
    monkeypatch.setattr(
        ibkr, "_get",
        lambda url: _response(
            "<FlexStatementResponse><Status>Fail</Status>"
            "<ErrorCode>1012</ErrorCode>"
            "<ErrorMessage>Token has expired.</ErrorMessage>"
            "</FlexStatementResponse>"
        ),
    )
    with pytest.raises(ibkr.FlexError, match="expired"):
        ibkr.fetch_statement("tok", "123")


def test_an_unknown_error_still_surfaces_its_message(monkeypatch):
    monkeypatch.setattr(
        ibkr, "_get",
        lambda url: _response(
            "<FlexStatementResponse><Status>Fail</Status>"
            "<ErrorCode>9999</ErrorCode>"
            "<ErrorMessage>Something new went wrong.</ErrorMessage>"
            "</FlexStatementResponse>"
        ),
    )
    with pytest.raises(ibkr.FlexError, match="Something new went wrong"):
        ibkr.fetch_statement("tok", "123")


def test_polls_until_the_statement_is_ready(monkeypatch):
    """'Generation in progress' is the normal first answer, not a failure."""
    calls = {"n": 0}

    def fake_get(url: str) -> bytes:
        calls["n"] += 1
        if "SendRequest" in url:
            return _response(
                "<FlexStatementResponse><Status>Success</Status>"
                "<ReferenceCode>REF1</ReferenceCode>"
                "<Url>https://example.invalid/GetStatement</Url>"
                "</FlexStatementResponse>"
            )
        if calls["n"] < 3:
            return _response(
                "<FlexStatementResponse><Status>Warn</Status>"
                "<ErrorCode>1019</ErrorCode></FlexStatementResponse>"
            )
        return _statement(positions=POSITION)

    monkeypatch.setattr(ibkr, "_get", fake_get)
    monkeypatch.setattr(ibkr.time, "sleep", lambda _: None)

    xml = ibkr.fetch_statement("tok", "123")
    assert ibkr.parse(xml)["positions"][0]["ticker"] == "AAPL"


# --- how IBKR and the existing pipeline fit together ----------------------


def test_class_shares_normalise_to_the_registry_convention():
    """IBKR writes 'BRK B'; SEC and yfinance write 'BRK-B'. Without this the
    position never joins and looks like missing data rather than a naming
    difference."""
    assert ibkr.normalize_symbol("BRK B") == "BRK-B"
    assert ibkr.normalize_symbol("  aapl ") == "AAPL"
    assert ibkr.normalize_symbol("") is None
    assert ibkr.normalize_symbol(None) is None


def test_a_held_class_share_parses_under_the_registry_symbol():
    p = POSITION.replace('symbol="AAPL"', 'symbol="BRK B"')
    parsed = ibkr.parse(_statement(positions=p))
    assert parsed["positions"][0]["ticker"] == "BRK-B"


def test_credentials_are_unreadable_without_the_key(tmp_path, monkeypatch):
    """A rotated ENCRYPTION_KEY must read as 'relink', never as a crash or as
    a token that silently fails against IBKR."""
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    conn = db.connect(tmp_path / "creds.db")
    db.init_schema(conn)
    seed_user(conn)
    conn.execute(
        "INSERT INTO ibkr_links (user_id, flex_query_id, token_cipher, linked_at) "
        "VALUES (1, '1234567', 'v1.aa.bb.cc', datetime('now'))"
    )
    conn.commit()

    assert ibkr.credentials(conn, 1) is None
    conn.close()


def test_secrets_round_trip_matches_the_web_format(monkeypatch):
    """ingest/secrets.py and web/lib/secrets.ts must stay byte-compatible.
    If they drift, every stored token stops decrypting and the only symptom is
    a portfolio that quietly stops updating."""
    monkeypatch.setenv("ENCRYPTION_KEY", "k" * 40)
    import importlib

    import credentials as store
    importlib.reload(store)

    sealed = store.encrypt("flex-token-abc123")
    assert sealed.startswith("v1.")
    assert len(sealed.split(".")) == 4
    assert store.decrypt(sealed) == "flex-token-abc123"


def test_a_non_sec_symbol_is_ruled_out_not_retried(tmp_path):
    """SPYL and friends — European UCITS ETFs — are real holdings with no SEC
    filings. Retrying them at EDGAR every morning would leave the daily run
    permanently 'partial' and the freshness banner permanently warning."""
    import backfill

    conn = db.connect(tmp_path / "etf.db")
    db.init_schema(conn)
    seed_user(conn)

    etf = POSITION.replace('symbol="AAPL"', 'symbol="SPYL"')
    ibkr.store(conn, 1, ibkr.parse(_statement(positions=etf)))

    # Discovered and reported the first time...
    assert ibkr.unmatched_tickers(conn, 1) == ["SPYL"]

    backfill.mark_unsupported(conn, "SPYL", "not an SEC filer")

    # ...then ruled out, so it stops being chased.
    assert ibkr.unmatched_tickers(conn, 1) == []
    row = conn.execute(
        "SELECT supported, unsupported_reason FROM tickers WHERE ticker='SPYL'"
    ).fetchone()
    assert row["supported"] == 0
    assert "SEC filer" in row["unsupported_reason"]

    # The holding itself is untouched: quantity and cost basis still work.
    held = conn.execute(
        "SELECT quantity, cost_basis_money FROM holdings WHERE ticker='SPYL'"
    ).fetchone()
    assert held["quantity"] == 100
    conn.close()


# --- funds: priced, not valued --------------------------------------------


def test_a_fund_gets_a_price_only_series(tmp_path):
    """No filings, so every metric is null and only close is populated. That is
    the truth for an index tracker rather than a gap in the data."""
    import derive

    conn = db.connect(tmp_path / "fund.db")
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO tickers (ticker, name, kind, price_symbol, supported) "
        "VALUES ('SPYL','S&P 500 UCITS','fund','SPYL.L',1)"
    )
    conn.executemany(
        "INSERT INTO prices (ticker, date, close) VALUES ('SPYL', ?, ?)",
        [("2026-08-26", 18.98), ("2026-08-27", 19.10)],
    )
    conn.commit()

    derive.derive_fund(conn, "SPYL", verbose=False)

    row = conn.execute(
        "SELECT * FROM ratios_daily WHERE ticker='SPYL' ORDER BY date DESC LIMIT 1"
    ).fetchone()
    assert row["close"] == 19.10
    assert row["pe_ttm"] is None and row["revenue_growth_yoy"] is None
    conn.close()


def test_a_qualified_symbol_is_trusted_rather_than_probed(monkeypatch):
    """Short codes are reused across exchanges for unrelated funds — bare SPYL
    resolves to an EM Latin America tracker. Passing SPYL.L must not go
    hunting for alternatives."""
    import funds

    seen = []

    class FakeTicker:
        def __init__(self, sym):
            seen.append(sym)
            self.sym = sym

        @property
        def info(self):
            return {"currency": "USD", "longName": "Fake", "regularMarketPrice": 1.0}

        def history(self, **_):
            import pandas as pd
            return pd.DataFrame({"Close": [1.0] * 10})

    monkeypatch.setattr(funds.yf, "Ticker", FakeTicker)
    assert funds.resolve("SPYL.L")["price_symbol"] == "SPYL.L"
    assert seen == ["SPYL.L"], f"should not probe alternatives, tried {seen}"


# --- corporate actions: an independent check on our splits -----------------


def _action(symbol="NVDA", ratio="10 FOR 1", date="20240610", action_id="CA1"):
    return (
        f'<CorporateAction actionID="{action_id}" symbol="{symbol}" '
        f'reportDate="{date}" type="FS" quantity="900" value="0" '
        f'actionDescription="{symbol}(US67066G1040) SPLIT {ratio} '
        f'({symbol}, NVIDIA CORP, US67066G1040)" />'
    )


def _with_actions(actions: str) -> bytes:
    return (
        '<?xml version="1.0"?><FlexQueryResponse><FlexStatements><FlexStatement>'
        f"<CorporateActions>{actions}</CorporateActions>"
        "</FlexStatement></FlexStatements></FlexQueryResponse>"
    ).encode()


def test_the_split_ratio_is_read_out_of_the_description():
    """IBKR writes it as prose, not as a field."""
    assert ibkr.split_ratio_from("NVDA(US) SPLIT 10 FOR 1 (NVDA)") == 10
    assert ibkr.split_ratio_from("AAPL SPLIT 4 FOR 1") == 4
    assert ibkr.split_ratio_from("A reverse SPLIT 1 FOR 8 happened") == 0.125
    assert ibkr.split_ratio_from("CASH DIVIDEND USD 0.24") is None
    assert ibkr.split_ratio_from(None) is None


def test_agreeing_splits_report_nothing(tmp_path):
    conn = db.connect(tmp_path / "ok.db")
    db.init_schema(conn)
    seed_user(conn)
    conn.execute("INSERT INTO share_splits (ticker,date,ratio) VALUES ('NVDA','2024-06-10',10.0)")
    conn.commit()

    ibkr.store(conn, 1, ibkr.parse(_with_actions(_action())))
    assert ibkr.check_splits(conn, 1) == []
    conn.close()


def test_a_split_we_never_recorded_is_reported(tmp_path):
    """The dangerous case. Prices are split-adjusted and share counts are
    normalised with share_splits; a missing entry makes every multiple before
    that date wrong by the ratio, and nothing else would say so."""
    conn = db.connect(tmp_path / "missing.db")
    db.init_schema(conn)
    seed_user(conn)

    ibkr.store(conn, 1, ibkr.parse(_with_actions(_action())))
    problems = ibkr.check_splits(conn, 1)

    assert len(problems) == 1
    assert problems[0]["issue"] == "missing"
    assert problems[0]["broker_ratio"] == 10
    assert "10x" in problems[0]["detail"]
    conn.close()


def test_a_disagreeing_ratio_is_reported(tmp_path):
    conn = db.connect(tmp_path / "wrong.db")
    db.init_schema(conn)
    seed_user(conn)
    conn.execute("INSERT INTO share_splits (ticker,date,ratio) VALUES ('NVDA','2024-06-11',4.0)")
    conn.commit()

    ibkr.store(conn, 1, ibkr.parse(_with_actions(_action())))
    problems = ibkr.check_splits(conn, 1)

    assert len(problems) == 1 and problems[0]["issue"] == "mismatch"
    assert problems[0]["broker_ratio"] == 10 and problems[0]["our_ratio"] == 4.0
    conn.close()


def test_a_split_booked_a_day_apart_still_matches(tmp_path):
    """Brokers book on settlement, which can differ from the market date."""
    conn = db.connect(tmp_path / "near.db")
    db.init_schema(conn)
    seed_user(conn)
    conn.execute("INSERT INTO share_splits (ticker,date,ratio) VALUES ('NVDA','2024-06-08',10.0)")
    conn.commit()

    ibkr.store(conn, 1, ibkr.parse(_with_actions(_action())))
    assert ibkr.check_splits(conn, 1) == []
    conn.close()


def test_non_split_actions_are_stored_but_not_compared(tmp_path):
    conn = db.connect(tmp_path / "div.db")
    db.init_schema(conn)
    seed_user(conn)

    dividend = (
        '<CorporateAction actionID="CA9" symbol="KO" reportDate="20260601" '
        'type="DI" actionDescription="KO(US1912161007) CASH DIVIDEND USD 0.51" />'
    )
    ibkr.store(conn, 1, ibkr.parse(_with_actions(dividend)))

    assert conn.execute(
        "SELECT COUNT(*) FROM ibkr_corporate_actions").fetchone()[0] == 1
    assert ibkr.check_splits(conn, 1) == []
    conn.close()
