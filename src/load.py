"""SQLite persistence: schema init, idempotent upserts, run log, quarantine, known-key lookup."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from common import ROOT

SCHEMA = ROOT / "src" / "schema.sql"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text())
    return con


def already_loaded(con: sqlite3.Connection, filename: str, file_hash: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM run_log WHERE source_file=? AND file_hash=? AND status='loaded' LIMIT 1",
        (filename, file_hash)).fetchone()
    return row is not None


def known_keys(con: sqlite3.Connection, table: str, col: str) -> set[str]:
    return {r[0] for r in con.execute(f"SELECT {col} FROM {table}")}


def upsert(con: sqlite3.Connection, table: str, df: pd.DataFrame,
           source_file: str, run_id: str) -> int:
    if df.empty:
        return 0
    df = df.copy()
    df["source_file"] = source_file
    df["run_id"] = run_id
    cols = list(df.columns)
    placeholders = ",".join("?" * len(cols))
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    con.executemany(sql, df.itertuples(index=False, name=None))
    return len(df)


def write_quarantine(con: sqlite3.Connection, table: str, rejected: list[dict],
                     source_file: str, run_id: str) -> None:
    con.executemany(
        "INSERT INTO quarantine (run_id, source_file, table_name, row_number, reject_reason, raw_row) "
        "VALUES (?,?,?,?,?,?)",
        [(run_id, source_file, table, r["row_number"], r["reject_reason"], r["raw_row"])
         for r in rejected])


def log_run(con: sqlite3.Connection, run_id: str, source_file: str, file_hash: str, table: str,
            rows_read: int, rows_loaded: int, rows_rejected: int, status: str,
            flags: list[str] | None = None) -> None:
    con.execute(
        "INSERT INTO run_log (run_id, run_ts, source_file, file_hash, table_name, rows_read, "
        "rows_loaded, rows_rejected, status, flags) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (run_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), source_file, file_hash,
         table, rows_read, rows_loaded, rows_rejected, status, json.dumps(flags or [])))


def trailing_median_rows(con: sqlite3.Connection, table: str, n: int) -> float | None:
    rows = [r[0] for r in con.execute(
        "SELECT rows_read FROM run_log WHERE table_name=? AND status='loaded' "
        "ORDER BY id DESC LIMIT ?", (table, n))]
    if len(rows) < 3:
        return None
    return float(pd.Series(rows).median())
