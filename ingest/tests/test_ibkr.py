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
