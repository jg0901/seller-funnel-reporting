-- SQLite schema for the seller reporting MVP.
-- Natural keys enforce idempotency: re-loading the same file is a no-op via INSERT OR REPLACE.

CREATE TABLE IF NOT EXISTS sourcing (
    seller_id     TEXT PRIMARY KEY,
    sourced_date  TEXT NOT NULL,          -- ISO date
    channel       TEXT,
    region        TEXT,
    source_file   TEXT NOT NULL,
    run_id        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS onboarding (
    seller_id       TEXT PRIMARY KEY,
    onboarded_date  TEXT NOT NULL,
    kyc_status      TEXT,
    source_file     TEXT NOT NULL,
    run_id          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activation (
    seller_id            TEXT PRIMARY KEY,
    activated_date       TEXT NOT NULL,
    first_listing_count  INTEGER,
    source_file          TEXT NOT NULL,
    run_id               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    order_id     TEXT PRIMARY KEY,
    seller_id    TEXT NOT NULL,
    order_date   TEXT NOT NULL,
    gmv          REAL NOT NULL,
    source_file  TEXT NOT NULL,
    run_id       TEXT NOT NULL
);

-- Rejected rows are kept with the reason so the quality report has evidence.
CREATE TABLE IF NOT EXISTS quarantine (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL,
    source_file    TEXT NOT NULL,
    table_name     TEXT NOT NULL,
    row_number     INTEGER,
    reject_reason  TEXT NOT NULL,
    raw_row        TEXT NOT NULL            -- JSON of the original row
);

-- One row per file processed. file_hash lets us skip files already loaded.
CREATE TABLE IF NOT EXISTS run_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    run_ts        TEXT NOT NULL,
    source_file   TEXT NOT NULL,
    file_hash     TEXT NOT NULL,
    table_name    TEXT NOT NULL,
    rows_read     INTEGER NOT NULL,
    rows_loaded   INTEGER NOT NULL,
    rows_rejected INTEGER NOT NULL,
    status        TEXT NOT NULL,            -- loaded | skipped | failed
    flags         TEXT                      -- JSON list of warnings (e.g. volume drop)
);

CREATE INDEX IF NOT EXISTS ix_txn_seller ON transactions(seller_id);
CREATE INDEX IF NOT EXISTS ix_runlog_file ON run_log(source_file, file_hash);
