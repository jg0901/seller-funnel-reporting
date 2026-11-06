"""End-to-end tests on a tiny generated dataset in a temp dir."""
import sqlite3
from datetime import date

import pytest

from common import load_config
from generate_data import generate
from run import run


@pytest.fixture
def cfg(tmp_path):
    c = load_config()
    for k in ("inbox", "processed", "quarantine", "output"):
        c["paths"][k] = tmp_path / k
    c["paths"]["db"] = tmp_path / "test.db"
    generate(days=10, sellers_per_day=20, seed=1, start=date(2026, 10, 1), inbox=c["paths"]["inbox"])
    return c


def _counts(db):
    con = sqlite3.connect(db)
    out = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
           for t in ("sourcing", "onboarding", "activation", "transactions", "quarantine")}
    con.close()
    return out


def test_end_to_end_produces_outputs(cfg):
    out = run(cfg, as_of="2026-10-10", keep_inbox=True)
    assert out["xlsx"].exists() and out["json"].exists()
    assert out["summary"]["rows"]["loaded"] > 0
    counts = _counts(cfg["paths"]["db"])
    assert counts["sourcing"] > 0 and counts["transactions"] > 0


def test_rerun_is_idempotent(cfg):
    run(cfg, as_of="2026-10-10", keep_inbox=True)
    before = _counts(cfg["paths"]["db"])
    out = run(cfg, as_of="2026-10-10", keep_inbox=True)
    after = _counts(cfg["paths"]["db"])
    assert before == after
    assert out["summary"]["files"]["skipped"] == out["summary"]["files"]["total"]
    assert out["summary"]["rows"]["loaded"] == 0


def test_processed_files_leave_inbox(cfg):
    n_before = len(list(cfg["paths"]["inbox"].glob("*.csv")))
    out = run(cfg, as_of="2026-10-10", keep_inbox=False)
    assert len(list(cfg["paths"]["inbox"].glob("*.csv"))) == 0
    assert len(list((cfg["paths"]["processed"] / out["run_id"]).glob("*.csv"))) == n_before


def test_no_fk_violations_in_loaded_data(cfg):
    run(cfg, as_of="2026-10-10", keep_inbox=True)
    con = sqlite3.connect(cfg["paths"]["db"])
    orphans = con.execute(
        "SELECT COUNT(*) FROM activation a LEFT JOIN onboarding o USING(seller_id) "
        "WHERE o.seller_id IS NULL").fetchone()[0]
    assert orphans == 0
    orphans = con.execute(
        "SELECT COUNT(*) FROM transactions t LEFT JOIN activation a USING(seller_id) "
        "WHERE a.seller_id IS NULL").fetchone()[0]
    assert orphans == 0


def test_kpi_rates_bounded(cfg):
    run(cfg, as_of="2026-10-10", keep_inbox=True)
    import report
    con = sqlite3.connect(cfg["paths"]["db"])
    k = report.compute_kpis(con, 14, "2026-10-10")["weekly_cohorts"]
    for col in ("onboarding_conversion", "activation_conversion", "end_to_end_conversion"):
        assert k[col].dropna().between(0, 1).all()
    assert (k["activated"] <= k["onboarded"]).all()
    assert (k["onboarded"] <= k["sourced"]).all()
