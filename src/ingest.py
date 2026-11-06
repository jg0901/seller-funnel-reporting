"""Read + validate a single CSV. Returns clean rows and quarantined rows with reasons.

Validation order (each check tags the row; a row can fail for several reasons):
  1. required columns present in the file (file-level — fails the whole file)
  2. required values non-empty
  3. date columns parse as ISO YYYY-MM-DD
  4. numeric columns parse (gmv, first_listing_count)
  5. duplicate keys within the file (keep first, reject the rest)
  6. referential integrity: parent key must exist in DB OR in the same batch
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

NUMERIC_COLS = {"gmv": float, "first_listing_count": int}


@dataclass
class IngestResult:
    table: str
    source_file: str
    rows_read: int
    clean: pd.DataFrame
    rejected: list[dict] = field(default_factory=list)   # {row_number, reject_reason, raw_row}
    file_error: str | None = None

    @property
    def rows_rejected(self) -> int:
        return len(self.rejected)


def _is_iso_date(s: str) -> bool:
    try:
        return pd.to_datetime(s, format="%Y-%m-%d", errors="raise") is not pd.NaT
    except (ValueError, TypeError):
        return False


def validate_file(path: Path, table: str, spec: dict,
                  known_parents: dict[str, set[str]]) -> IngestResult:
    """spec = config['tables'][table]; known_parents = {table: set(key values)} from DB + batch."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    result = IngestResult(table=table, source_file=path.name, rows_read=len(df), clean=df.iloc[0:0])

    missing_cols = [c for c in spec["required"] if c not in df.columns]
    if missing_cols:
        result.file_error = f"missing required columns: {missing_cols}"
        return result

    reasons: list[list[str]] = [[] for _ in range(len(df))]

    # 2. required values
    for c in spec["required"]:
        for i in df.index[df[c].str.strip() == ""]:
            reasons[i].append(f"missing_value:{c}")

    # 3. dates
    for c in spec.get("date_cols", []):
        for i in df.index:
            v = df.at[i, c]
            if v != "" and not _is_iso_date(v):
                reasons[i].append(f"bad_date:{c}")

    # 4. numerics
    for c, typ in NUMERIC_COLS.items():
        if c in df.columns:
            for i in df.index:
                v = df.at[i, c]
                if v == "":
                    continue
                try:
                    typ(float(v)) if typ is int else typ(v)
                except ValueError:
                    reasons[i].append(f"not_numeric:{c}")

    # 5. in-file duplicates on key
    dup_mask = df.duplicated(subset=spec["key"], keep="first")
    for i in df.index[dup_mask]:
        reasons[i].append("duplicate_key_in_file")

    # 6. referential integrity
    for col, (parent_table, parent_col) in spec.get("referential", {}).items():
        parents = known_parents.get(parent_table, set())
        for i in df.index:
            v = df.at[i, col]
            if v != "" and v not in parents:
                reasons[i].append(f"orphan_fk:{col}->{parent_table}.{parent_col}")

    bad_idx = [i for i, r in enumerate(reasons) if r]
    for i in bad_idx:
        result.rejected.append({
            "row_number": int(i) + 2,                    # 1-based + header
            "reject_reason": ";".join(reasons[i]),
            "raw_row": json.dumps(df.iloc[i].to_dict()),
        })

    clean = df.drop(index=bad_idx).copy()
    for c, typ in NUMERIC_COLS.items():
        if c in clean.columns:
            clean[c] = clean[c].astype(float).astype(typ)
    result.clean = clean
    return result
