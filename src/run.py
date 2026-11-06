"""Orchestrator: inbox -> validate -> SQLite -> KPIs -> Excel + JSON quality report.

Usage:
    python src/run.py                    # process everything in data/inbox
    python src/run.py --as-of 2026-10-28 # pin the KPI "as of" date (default: today)
    python src/run.py --dry-run          # validate only, write nothing

Idempotent: a file whose (name, sha256) is already in run_log with status=loaded is skipped.
Processed files are moved to data/processed/<run_id>/ unless --keep-inbox.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import uuid
from datetime import date, datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

from common import file_hash, get_logger, load_config
from ingest import validate_file
import load as db
import report

log = get_logger("run")

# Load order matters for referential checks: parents before children.
LOAD_ORDER = ["sourcing", "onboarding", "activation", "transactions"]


def discover(inbox: Path, tables: dict) -> list[tuple[str, Path]]:
    """Return (table, path) pairs sorted by load order then filename (date)."""
    found = []
    for t in LOAD_ORDER:
        pattern = tables[t]["pattern"]
        for p in sorted(inbox.glob("*.csv")):
            if fnmatch(p.name, pattern):
                found.append((t, p))
    return found


def run(cfg: dict, as_of: str, dry_run: bool = False, keep_inbox: bool = False) -> dict:
    # timestamp + short random suffix: two runs in the same second must not share an id
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    paths = cfg["paths"]
    con = db.connect(paths["db"])
    files = discover(paths["inbox"], cfg["tables"])
    log.info("run_id=%s files=%d dry_run=%s", run_id, len(files), dry_run)

    # keys seen in this batch, so children can reference parents loaded in the same run
    batch_keys: dict[str, set[str]] = {t: set() for t in LOAD_ORDER}
    processed_dir = paths["processed"] / run_id

    for table, path in files:
        spec = cfg["tables"][table]
        fh = file_hash(path)

        if db.already_loaded(con, path.name, fh):
            log.info("skip %s (already loaded)", path.name)
            if not dry_run:
                db.log_run(con, run_id, path.name, fh, table, 0, 0, 0, "skipped")
            continue

        # parents known = DB + current batch
        known = {}
        for col, (ptable, pcol) in spec.get("referential", {}).items():
            known[ptable] = db.known_keys(con, ptable, pcol) | batch_keys[ptable]

        res = validate_file(path, table, spec, known)

        if res.file_error:
            log.error("FAIL %s: %s", path.name, res.file_error)
            if not dry_run:
                db.log_run(con, run_id, path.name, fh, table, res.rows_read, 0, res.rows_read,
                           "failed", [res.file_error])
                _move(path, paths["quarantine"] / run_id)
            continue

        flags = _volume_flags(con, table, res.rows_read, cfg["alerts"])
        if dry_run:
            log.info("DRY %s read=%d clean=%d rejected=%d flags=%s",
                     path.name, res.rows_read, len(res.clean), res.rows_rejected, flags)
            continue

        loaded = db.upsert(con, table, res.clean, path.name, run_id)
        db.write_quarantine(con, table, res.rejected, path.name, run_id)
        db.log_run(con, run_id, path.name, fh, table, res.rows_read, loaded, res.rows_rejected,
                   "loaded", flags)
        con.commit()
        batch_keys[table] |= set(res.clean[spec["key"][0]].astype(str))
        log.info("load %s read=%d loaded=%d rejected=%d%s", path.name, res.rows_read, loaded,
                 res.rows_rejected, f" FLAGS={flags}" if flags else "")
        if not keep_inbox:
            _move(path, processed_dir)

    if dry_run:
        con.close()
        return {"run_id": run_id, "dry_run": True}

    kpis = report.compute_kpis(con, cfg["kpi"]["activation_window_days"], as_of)
    summary = report.quality_summary(con, run_id)
    json_path = report.write_quality_json(summary, paths["output"], run_id)
    xlsx_path = report.write_excel(kpis, summary, con, paths["output"], run_id)
    con.close()

    log.info("done status=%s rows loaded=%d rejected=%d -> %s, %s",
             summary["status"], summary["rows"]["loaded"], summary["rows"]["rejected"],
             xlsx_path.name, json_path.name)
    return {"run_id": run_id, "summary": summary, "xlsx": xlsx_path, "json": json_path}


def _volume_flags(con, table: str, rows_read: int, alerts: dict) -> list[str]:
    med = db.trailing_median_rows(con, table, alerts["trailing_runs"])
    if med is None or med == 0:
        return []
    delta = (rows_read - med) / med
    if abs(delta) > alerts["volume_drop_threshold"]:
        direction = "drop" if delta < 0 else "spike"
        return [f"volume_{direction}: {rows_read} rows vs trailing median {med:.0f} ({delta:+.0%})"]
    return []


def _move(path: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest_dir / path.name))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--as-of", default=date.today().isoformat())
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-inbox", action="store_true")
    args = ap.parse_args()
    out = run(load_config(args.config), args.as_of, args.dry_run, args.keep_inbox)
    if not args.dry_run and out["summary"]["files"]["failed"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
