-- Stock monitor schema.
--
-- Two ideas shape this design:
--   1. `fundamentals` is as-filed and append-only. A company restates prior
--      periods in later filings, so the same (concept, period) legitimately
--      appears more than once with different values and different filed_at
--      dates. We keep every version, keyed by the accession that reported it,
--      and let the derivation step pick whichever was knowable at the time.
--   2. `ratios_daily` is the derived table the UI reads. It is rebuilt from
--      `fundamentals` + `prices` and holds no information of its own.

CREATE TABLE IF NOT EXISTS watchlist (
    ticker            TEXT PRIMARY KEY,
    name              TEXT,
    cik               INTEGER,
    added_at          TEXT NOT NULL,
    added_price       REAL,
    default_group_id  INTEGER REFERENCES metric_groups(id),
    notes             TEXT,
    -- Currency the company FILES in, which is not always the currency it
    -- TRADES in. TSM files in TWD, ASML in EUR, both quoted in USD. Any ratio
    -- that mixes a USD price with non-USD fundamentals is wrong by the FX rate
    -- and looks entirely plausible while being wrong. derive.py refuses to
    -- compute for tickers where this is set and not 'USD'.
    reporting_currency TEXT,
    -- Ordinary shares per ADR. NULL for ordinary US listings. TSM is 5.
    -- Share counts from EDGAR are ordinary shares; prices are per ADR.
    adr_ratio         REAL,
    -- 0 when the pipeline cannot produce trustworthy ratios for this ticker.
    supported         INTEGER NOT NULL DEFAULT 1,
    unsupported_reason TEXT
);

-- A group is a metric profile, not a tag: it decides which metrics the deep
-- dive renders and which multiple leads. Membership doubles as the peer set
-- for relative comparison.
CREATE TABLE IF NOT EXISTS metric_groups (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL UNIQUE,
    primary_multiple  TEXT NOT NULL,
    description       TEXT,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS group_metrics (
    group_id    INTEGER NOT NULL REFERENCES metric_groups(id) ON DELETE CASCADE,
    metric_key  TEXT NOT NULL,
    section     TEXT NOT NULL,
    sort_order  INTEGER NOT NULL,
    PRIMARY KEY (group_id, metric_key)
);

CREATE TABLE IF NOT EXISTS stock_groups (
    ticker    TEXT NOT NULL REFERENCES watchlist(ticker) ON DELETE CASCADE,
    group_id  INTEGER NOT NULL REFERENCES metric_groups(id) ON DELETE CASCADE,
    PRIMARY KEY (ticker, group_id)
);

-- One row per XBRL fact, normalized from raw tags to canonical concepts.
-- period_start is NULL for instant concepts (cash, debt, share count);
-- populated for duration concepts (revenue, net income, cash flow).
CREATE TABLE IF NOT EXISTS fundamentals (
    ticker         TEXT NOT NULL,
    concept        TEXT NOT NULL,
    period_start   TEXT,
    period_end     TEXT NOT NULL,
    duration_days  INTEGER,
    fiscal_year    INTEGER,
    fiscal_period  TEXT,
    form           TEXT,
    filed_at       TEXT NOT NULL,
    accession      TEXT NOT NULL,
    unit           TEXT,
    value          REAL,
    PRIMARY KEY (ticker, concept, period_end, accession, duration_days)
);

CREATE INDEX IF NOT EXISTS idx_fundamentals_lookup
    ON fundamentals (ticker, concept, filed_at);

-- Daily closes. `close` is split-adjusted (as yfinance returns it); share
-- counts are normalized to match in `share_splits`.
CREATE TABLE IF NOT EXISTS prices (
    ticker  TEXT NOT NULL,
    date    TEXT NOT NULL,
    close   REAL NOT NULL,
    volume  INTEGER,
    PRIMARY KEY (ticker, date)
);

-- Split events, used to normalize as-filed EDGAR share counts into
-- current-share terms so they pair correctly with split-adjusted prices.
-- Without this, a 4:1 split makes historical P/E wrong by exactly 4x, silently.
CREATE TABLE IF NOT EXISTS share_splits (
    ticker  TEXT NOT NULL,
    date    TEXT NOT NULL,
    ratio   REAL NOT NULL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS dividends (
    ticker  TEXT NOT NULL,
    date    TEXT NOT NULL,
    amount  REAL NOT NULL,
    PRIMARY KEY (ticker, date)
);

-- Derived. Rebuilt by derive.py; safe to drop and regenerate.
-- `fundamentals_filed_at` records which filing fed each row, so the
-- no-lookahead invariant is testable rather than assumed.
CREATE TABLE IF NOT EXISTS ratios_daily (
    ticker                TEXT NOT NULL,
    date                  TEXT NOT NULL,
    close                 REAL,
    market_cap            REAL,
    enterprise_value      REAL,
    shares_diluted        REAL,
    pe_ttm                REAL,
    ps_ttm                REAL,
    pb                    REAL,
    ev_ebitda             REAL,
    ev_sales              REAL,
    ev_fcf                REAL,
    fcf_yield             REAL,
    earnings_yield        REAL,
    gross_margin          REAL,
    operating_margin      REAL,
    net_margin            REAL,
    fcf_margin            REAL,
    fcf_conversion        REAL,
    roic                  REAL,
    roe                   REAL,
    net_debt              REAL,
    net_debt_ebitda       REAL,
    interest_coverage     REAL,
    revenue_ttm           REAL,
    revenue_growth_yoy    REAL,
    inventory_days        REAL,
    sbc_pct_revenue       REAL,
    capex_pct_revenue     REAL,
    fundamentals_filed_at TEXT,
    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_ratios_ticker_date
    ON ratios_daily (ticker, date DESC);

-- Reserved for a paid estimates source (FMP/Polygon). Empty in v1: EDGAR
-- carries no analyst consensus, so forward P/E, PEG and estimate revisions
-- are out of scope until this is populated.
CREATE TABLE IF NOT EXISTS estimates (
    ticker       TEXT NOT NULL,
    as_of        TEXT NOT NULL,
    fiscal_year  INTEGER NOT NULL,
    metric       TEXT NOT NULL,
    value        REAL,
    analysts     INTEGER,
    PRIMARY KEY (ticker, as_of, fiscal_year, metric)
);

-- Revenue by product/service line, scraped from rendered filing tables because
-- companyfacts drops dimensional facts. As-filed and keyed by accession, like
-- `fundamentals`.
--
-- `is_subtotal` marks rows that aggregate other rows — Alphabet's "Google
-- advertising" is the sum of Search, YouTube and Network; NVIDIA's "Data
-- Center" is Compute plus Networking. They are kept rather than discarded
-- because they are the labels people recognise, but anything summing lines
-- MUST exclude them or it double-counts.
CREATE TABLE IF NOT EXISTS segment_revenue (
    ticker      TEXT NOT NULL,
    axis        TEXT NOT NULL,        -- 'product' for now
    label       TEXT NOT NULL,        -- as reported: 'YouTube ads'
    parent      TEXT,                 -- from the "child | parent" notation
    period_end  TEXT NOT NULL,
    fiscal_year INTEGER,
    filed_at    TEXT NOT NULL,
    accession   TEXT NOT NULL,
    value       REAL NOT NULL,
    is_subtotal INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ticker, axis, label, period_end, accession)
);

CREATE INDEX IF NOT EXISTS idx_segment_lookup
    ON segment_revenue (ticker, axis, period_end);

-- One row per scheduled run. The UI reads the latest to show when data was
-- last refreshed — a silently failed job showing stale numbers you believe are
-- current is worse than an obviously broken one.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL,   -- running | ok | partial | skipped | error
    tickers         INTEGER,
    new_price_rows  INTEGER,
    latest_session  TEXT,            -- most recent trading day now in the data
    detail          TEXT
);

-- Alert rules. `metric_key` is a key from the metric registry, or the special
-- value '__primary__' meaning "whichever multiple leads for this ticker's
-- group" — so one rule reads P/E for Big Tech and EV/EBITDA for semis.
--
-- scope 'all'    -> scope_ref NULL
-- scope 'group'  -> scope_ref is a metric_groups.id
-- scope 'ticker' -> scope_ref is a ticker symbol
CREATE TABLE IF NOT EXISTS alert_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    scope       TEXT NOT NULL CHECK (scope IN ('all', 'group', 'ticker')),
    scope_ref   TEXT,
    metric_key  TEXT NOT NULL,
    condition   TEXT NOT NULL,
    threshold   REAL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

-- One row per crossing, not per day the condition holds. See alerts.py: rules
-- are edge-triggered, because a condition that stays true for forty sessions
-- would otherwise produce forty identical alerts and train you to ignore them.
CREATE TABLE IF NOT EXISTS alert_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id       INTEGER NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
    ticker        TEXT NOT NULL,
    trigger_date  TEXT NOT NULL,   -- market session that crossed
    created_at    TEXT NOT NULL,
    metric_key    TEXT,
    value         REAL,
    percentile    REAL,
    detail        TEXT,
    acknowledged  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (rule_id, ticker, trigger_date)
);

CREATE INDEX IF NOT EXISTS idx_alert_events_open
    ON alert_events (acknowledged, created_at DESC);

-- Previous evaluation result per (rule, ticker), which is what makes edge
-- detection possible.
--
-- Storing it beats re-deriving the previous session's answer, because "was this
-- true last time?" and "was this true yesterday?" are different questions. A
-- rule created today has no yesterday: re-deriving would find the condition
-- already true, conclude nothing changed, and stay silent forever — so writing
-- a rule would appear to do nothing. With no stored state, the first evaluation
-- of a pair is treated as a crossing, which is also the right answer for a
-- freshly added ticker.
--
-- `last_marker` holds the filing date for new_filing rules, which detect change
-- rather than threshold.
CREATE TABLE IF NOT EXISTS alert_rule_state (
    rule_id       INTEGER NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
    ticker        TEXT NOT NULL,
    last_session  TEXT,
    last_held     INTEGER NOT NULL DEFAULT 0,
    last_marker   TEXT,
    PRIMARY KEY (rule_id, ticker)
);

-- Bookkeeping so backfills can resume instead of restarting.
CREATE TABLE IF NOT EXISTS ingest_log (
    ticker      TEXT NOT NULL,
    source      TEXT NOT NULL,
    ran_at      TEXT NOT NULL,
    status      TEXT NOT NULL,
    detail      TEXT,
    PRIMARY KEY (ticker, source, ran_at)
);

-- Async jobs started from the UI.
--
-- The web app used to await these subprocesses inside a server action, which
-- held the action pending for minutes. Next queues client navigations behind an
-- in-flight server action, so clicking into a stock did nothing until a refresh
-- finished. Now the action inserts a row here, spawns the work detached, and
-- returns immediately; the browser polls /api/jobs.
--
-- The row is inserted by whoever STARTS the job, before the process is spawned.
-- That ordering matters: if Python created it, the first poll would arrive
-- before the row existed and the UI would conclude nothing was running.
--
-- Separate from `pipeline_runs`, which stays the scheduler's own record of what
-- the data reflects. A job is "did this button work"; a run is "how fresh are
-- these numbers".
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,          -- refresh | add
    target      TEXT,                   -- ticker, for add
    status      TEXT NOT NULL,          -- running | ok | error
    step        TEXT,                   -- current phase, shown in the UI
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    detail      TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_active ON jobs (status, id DESC);

-- Accounts.
--
-- Passwords are stored as scrypt hashes with a per-user salt, never plaintext
-- and never reversibly encrypted. The format is scheme$salt$key so the scheme
-- can change later without a migration guessing game.
--
-- `role` exists because the first account to register owns the instance, and
-- per-user data ownership will need to distinguish them. Nothing enforces it
-- beyond that yet.
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL,
    email_lower   TEXT NOT NULL UNIQUE,   -- lookups are case-insensitive
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'member',
    created_at    TEXT NOT NULL,
    last_seen_at  TEXT
);

-- Each user's link to their own IBKR account.
--
-- One row per user: the Flex Web Service needs a query id and a token, and the
-- token is a bearer credential that reads their brokerage statements. It is
-- stored encrypted (AES-256-GCM) rather than plaintext, is never returned to
-- the browser, and is never written to a log. Deleting the account takes the
-- link with it.
--
-- Flex data refreshes once daily overnight, which is why last_sync_at is worth
-- surfacing: a stale link should look stale rather than look like a portfolio
-- that stopped moving.
CREATE TABLE IF NOT EXISTS ibkr_links (
    user_id          INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    flex_query_id    TEXT NOT NULL,
    token_cipher     TEXT NOT NULL,
    account_label    TEXT,
    linked_at        TEXT NOT NULL,
    last_sync_at     TEXT,
    last_sync_status TEXT,
    last_sync_detail TEXT
);

-- IBKR holdings, one snapshot per user per report date.
--
-- Flex refreshes overnight, so this is a daily series rather than live. Keeping
-- every snapshot rather than overwriting means position weight over time is
-- available later without a second data source.
CREATE TABLE IF NOT EXISTS holdings (
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    report_date      TEXT NOT NULL,
    ticker           TEXT NOT NULL,
    currency         TEXT,
    quantity         REAL,
    cost_basis_price REAL,
    cost_basis_money REAL,
    mark_price       REAL,
    position_value   REAL,
    unrealized_pnl   REAL,
    percent_of_nav   REAL,
    PRIMARY KEY (user_id, report_date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_holdings_latest
    ON holdings (user_id, report_date DESC);

-- Executions, keyed by IBKR's trade id so a re-run cannot double-count. Same
-- reasoning as `fundamentals` being keyed by SEC accession.
CREATE TABLE IF NOT EXISTS ibkr_trades (
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    trade_id     TEXT NOT NULL,
    ticker       TEXT,
    currency     TEXT,
    trade_date   TEXT,
    buy_sell     TEXT,
    quantity     REAL,
    price        REAL,
    commission   REAL,
    net_cash     REAL,
    open_close   TEXT,
    cost_basis   REAL,
    realized_pnl REAL,
    PRIMARY KEY (user_id, trade_id)
);

CREATE INDEX IF NOT EXISTS idx_ibkr_trades_ticker
    ON ibkr_trades (user_id, ticker, trade_date);

-- Cash by currency, and the NAV split. Cash is what turns position values into
-- real weights: without it, "40% of my equities" reads as "40% of my money".
CREATE TABLE IF NOT EXISTS ibkr_cash (
    user_id              INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    report_date          TEXT NOT NULL,
    currency             TEXT NOT NULL,
    starting_cash        REAL,
    ending_cash          REAL,
    dividends            REAL,
    withholding_tax      REAL,
    deposits_withdrawals REAL,
    interest             REAL,
    PRIMARY KEY (user_id, report_date, currency)
);

CREATE TABLE IF NOT EXISTS ibkr_nav (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    report_date TEXT NOT NULL,
    cash        REAL,
    stock       REAL,
    total       REAL,
    PRIMARY KEY (user_id, report_date)
);
