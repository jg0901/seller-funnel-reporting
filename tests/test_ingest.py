from pathlib import Path

import pandas as pd
import pytest

from ingest import validate_file

SOURCING_SPEC = {"required": ["seller_id", "sourced_date", "channel", "region"],
                 "date_cols": ["sourced_date"], "key": ["seller_id"], "referential": {}}
ONB_SPEC = {"required": ["seller_id", "onboarded_date", "kyc_status"],
            "date_cols": ["onboarded_date"], "key": ["seller_id"],
            "referential": {"seller_id": ["sourcing", "seller_id"]}}
TXN_SPEC = {"required": ["order_id", "seller_id", "order_date", "gmv"],
            "date_cols": ["order_date"], "key": ["order_id"],
            "referential": {"seller_id": ["activation", "seller_id"]}}


def _csv(tmp_path: Path, name: str, rows: list[dict]) -> Path:
    p = tmp_path / name
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def test_clean_file_passes(tmp_path):
    p = _csv(tmp_path, "sourcing_2026-10-01.csv", [
        {"seller_id": "S1", "sourced_date": "2026-10-01", "channel": "agent", "region": "NCR"}])
    r = validate_file(p, "sourcing", SOURCING_SPEC, {})
    assert r.file_error is None and r.rows_rejected == 0 and len(r.clean) == 1


def test_missing_column_fails_whole_file(tmp_path):
    p = _csv(tmp_path, "sourcing_x.csv", [{"seller_id": "S1", "sourced_date": "2026-10-01"}])
    r = validate_file(p, "sourcing", SOURCING_SPEC, {})
    assert r.file_error and "channel" in r.file_error
    assert len(r.clean) == 0


def test_bad_date_and_missing_value_rejected(tmp_path):
    p = _csv(tmp_path, "sourcing_x.csv", [
        {"seller_id": "S1", "sourced_date": "31/02/2026", "channel": "agent", "region": "NCR"},
        {"seller_id": "S2", "sourced_date": "", "channel": "agent", "region": "NCR"},
        {"seller_id": "S3", "sourced_date": "2026-10-01", "channel": "agent", "region": "NCR"}])
    r = validate_file(p, "sourcing", SOURCING_SPEC, {})
    reasons = {x["reject_reason"] for x in r.rejected}
    assert "bad_date:sourced_date" in reasons
    assert "missing_value:sourced_date" in reasons
    assert list(r.clean["seller_id"]) == ["S3"]


def test_duplicate_key_keeps_first(tmp_path):
    p = _csv(tmp_path, "sourcing_x.csv", [
        {"seller_id": "S1", "sourced_date": "2026-10-01", "channel": "agent", "region": "NCR"},
        {"seller_id": "S1", "sourced_date": "2026-10-02", "channel": "referral", "region": "NCR"}])
    r = validate_file(p, "sourcing", SOURCING_SPEC, {})
    assert len(r.clean) == 1 and r.clean.iloc[0]["channel"] == "agent"
    assert r.rejected[0]["reject_reason"] == "duplicate_key_in_file"
    assert r.rejected[0]["row_number"] == 3


def test_orphan_fk_rejected(tmp_path):
    p = _csv(tmp_path, "onboarding_x.csv", [
        {"seller_id": "S1", "onboarded_date": "2026-10-02", "kyc_status": "approved"},
        {"seller_id": "S999", "onboarded_date": "2026-10-02", "kyc_status": "approved"}])
    r = validate_file(p, "onboarding", ONB_SPEC, {"sourcing": {"S1"}})
    assert list(r.clean["seller_id"]) == ["S1"]
    assert "orphan_fk" in r.rejected[0]["reject_reason"]


def test_non_numeric_gmv_rejected_and_clean_cast(tmp_path):
    p = _csv(tmp_path, "transactions_x.csv", [
        {"order_id": "O1", "seller_id": "S1", "order_date": "2026-10-05", "gmv": "12.50"},
        {"order_id": "O2", "seller_id": "S1", "order_date": "2026-10-05", "gmv": "n/a"}])
    r = validate_file(p, "transactions", TXN_SPEC, {"activation": {"S1"}})
    assert len(r.clean) == 1 and r.clean.iloc[0]["gmv"] == pytest.approx(12.5)
    assert r.clean["gmv"].dtype == float
