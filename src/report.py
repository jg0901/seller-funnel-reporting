"""Build the Excel summary and the machine-readable quality report."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from common import ROOT

SQL_DIR = ROOT / "src"


def _run_sql(con: sqlite3.Connection, sql_file: str, params: dict) -> pd.DataFrame:
    sql = (SQL_DIR / sql_file).read_text()
    return pd.read_sql_query(sql, con, params=params)


def compute_kpis(con: sqlite3.Connection, window_days: int, as_of: str) -> dict[str, pd.DataFrame]:
    params = {"window_days": window_days, "as_of": as_of}
    return {
        "weekly_cohorts": _run_sql(con, "kpis.sql", params),
        "by_channel": _run_sql(con, "kpis_by_channel.sql", params),
    }


def quality_summary(con: sqlite3.Connection, run_id: str) -> dict:
    runlog = pd.read_sql_query("SELECT * FROM run_log WHERE run_id=?", con, params=(run_id,))
    quarantine = pd.read_sql_query(
        "SELECT table_name, reject_reason, COUNT(*) AS n FROM quarantine WHERE run_id=? "
        "GROUP BY table_name, reject_reason ORDER BY n DESC", con, params=(run_id,))
    flags = []
    for _, r in runlog.iterrows():
        for f in json.loads(r["flags"] or "[]"):
            flags.append({"file": r["source_file"], "flag": f})
    return {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": {
            "total": int(len(runlog)),
            "loaded": int((runlog["status"] == "loaded").sum()),
            "skipped": int((runlog["status"] == "skipped").sum()),
            "failed": int((runlog["status"] == "failed").sum()),
        },
        "rows": {
            "read": int(runlog["rows_read"].sum()),
            "loaded": int(runlog["rows_loaded"].sum()),
            "rejected": int(runlog["rows_rejected"].sum()),
        },
        "reject_reasons": quarantine.to_dict(orient="records"),
        "flags": flags,
        "status": "warn" if flags or (runlog["status"] == "failed").any() else "ok",
    }


def write_quality_json(summary: dict, out_dir: Path, run_id: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"quality_report_{run_id}.json"
    path.write_text(json.dumps(summary, indent=2))
    # also keep a stable "latest" pointer for downstream consumers
    (out_dir / "quality_report_latest.json").write_text(json.dumps(summary, indent=2))
    return path


def write_excel(kpis: dict[str, pd.DataFrame], summary: dict, con: sqlite3.Connection,
                out_dir: Path, run_id: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"seller_kpis_{run_id}.xlsx"

    runlog = pd.read_sql_query(
        "SELECT run_ts, source_file, table_name, rows_read, rows_loaded, rows_rejected, status, flags "
        "FROM run_log WHERE run_id=? ORDER BY id", con, params=(run_id,))
    quarantine = pd.read_sql_query(
        "SELECT source_file, table_name, row_number, reject_reason, raw_row FROM quarantine "
        "WHERE run_id=? ORDER BY source_file, row_number", con, params=(run_id,))

    overview = pd.DataFrame([
        ["Run ID", summary["run_id"]],
        ["Generated (UTC)", summary["generated_at"]],
        ["Status", summary["status"].upper()],
        ["Files loaded / skipped / failed",
         f'{summary["files"]["loaded"]} / {summary["files"]["skipped"]} / {summary["files"]["failed"]}'],
        ["Rows read / loaded / rejected",
         f'{summary["rows"]["read"]} / {summary["rows"]["loaded"]} / {summary["rows"]["rejected"]}'],
        ["Flags", "; ".join(f'{f["file"]}: {f["flag"]}' for f in summary["flags"]) or "none"],
    ], columns=["Metric", "Value"])

    sheets = {
        "Overview": overview,
        "Weekly Cohorts": kpis["weekly_cohorts"],
        "By Channel": kpis["by_channel"],
        "Run Log": runlog,
        "Quarantine": quarantine,
    }

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            _style(ws, df)
        ws = xw.sheets["Weekly Cohorts"]
        for col_name in ("onboarding_conversion", "activation_conversion", "end_to_end_conversion"):
            if col_name in kpis["weekly_cohorts"].columns:
                ci = list(kpis["weekly_cohorts"].columns).index(col_name) + 1
                for row in range(2, ws.max_row + 1):
                    ws.cell(row=row, column=ci).number_format = "0.0%"
        ws = xw.sheets["By Channel"]
        if "end_to_end_conversion" in kpis["by_channel"].columns:
            ci = list(kpis["by_channel"].columns).index("end_to_end_conversion") + 1
            for row in range(2, ws.max_row + 1):
                ws.cell(row=row, column=ci).number_format = "0.0%"
    return path


def _style(ws, df: pd.DataFrame) -> None:
    header_fill = PatternFill("solid", fgColor="1F3A5F")
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = header_fill
        c.alignment = Alignment(vertical="center")
    for i, col in enumerate(df.columns, start=1):
        width = max([len(str(col))] + [len(str(v)) for v in df[col].head(200)]) + 2
        ws.column_dimensions[get_column_letter(i)].width = min(width, 60)
    ws.freeze_panes = "A2"
