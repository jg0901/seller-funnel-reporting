"""Generate fictional seller lifecycle data (sourcing -> onboarding -> activation -> transactions).

Produces one CSV per table per day in data/inbox/. Deliberately injects a small share of
bad rows (missing fields, bad dates, duplicates, orphan foreign keys) so the validation
layer has something to catch.

Usage:
    python src/generate_data.py --days 28 --sellers-per-day 40 --seed 42
"""
from __future__ import annotations

import argparse
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from common import load_config, get_logger

log = get_logger(__name__)

CHANNELS = ["referral", "agent", "self_signup", "campaign"]
REGIONS = ["NCR", "Luzon", "Visayas", "Mindanao"]
KYC = ["approved", "approved", "approved", "pending", "rejected"]


def _iso(d: date) -> str:
    return d.isoformat()


def generate(days: int, sellers_per_day: int, seed: int, start: date, inbox: Path,
             bad_rate: float = 0.03) -> dict[str, int]:
    rng = random.Random(seed)
    inbox.mkdir(parents=True, exist_ok=True)
    counts = {"sourcing": 0, "onboarding": 0, "activation": 0, "transactions": 0}

    sellers: list[dict] = []  # master list for FK consistency
    seller_seq = 1

    for d in range(days):
        day = start + timedelta(days=d)
        # weekday seasonality: fewer sellers on weekends
        n = sellers_per_day if day.weekday() < 5 else int(sellers_per_day * 0.5)
        n = max(1, int(n * rng.uniform(0.8, 1.2)))

        src_rows, onb_rows, act_rows, txn_rows = [], [], [], []

        # --- sourcing: new sellers today
        for _ in range(n):
            sid = f"S{seller_seq:06d}"
            seller_seq += 1
            s = {"seller_id": sid, "sourced_date": day, "channel": rng.choice(CHANNELS),
                 "region": rng.choice(REGIONS), "onboarded": None, "activated": None}
            sellers.append(s)
            src_rows.append({"seller_id": sid, "sourced_date": _iso(day),
                             "channel": s["channel"], "region": s["region"]})

        # --- onboarding: sellers sourced 0-10 days ago, ~70% eventually onboard
        for s in sellers:
            if s["onboarded"] is None and s["sourced_date"] <= day:
                lag = (day - s["sourced_date"]).days
                if lag <= 10 and rng.random() < 0.12:
                    s["onboarded"] = day
                    onb_rows.append({"seller_id": s["seller_id"], "onboarded_date": _iso(day),
                                     "kyc_status": rng.choice(KYC)})

        # --- activation: onboarded sellers, 0-14 days later, ~60%
        for s in sellers:
            if s["onboarded"] is not None and s["activated"] is None and s["onboarded"] <= day:
                lag = (day - s["onboarded"]).days
                if lag <= 14 and rng.random() < 0.10:
                    s["activated"] = day
                    act_rows.append({"seller_id": s["seller_id"], "activated_date": _iso(day),
                                     "first_listing_count": rng.randint(1, 25)})

        # --- transactions: activated sellers generate 0-3 orders/day
        for s in sellers:
            if s["activated"] is not None and s["activated"] <= day:
                for _ in range(rng.choices([0, 1, 2, 3], weights=[55, 30, 10, 5])[0]):
                    txn_rows.append({"order_id": f"O{rng.randint(10**8, 10**9 - 1)}",
                                     "seller_id": s["seller_id"], "order_date": _iso(day),
                                     "gmv": round(rng.lognormvariate(5.5, 0.8), 2)})

        # --- inject bad rows
        _inject_bad(rng, src_rows, onb_rows, act_rows, txn_rows, bad_rate)

        for name, rows in [("sourcing", src_rows), ("onboarding", onb_rows),
                           ("activation", act_rows), ("transactions", txn_rows)]:
            if rows:
                df = pd.DataFrame(rows)
                df.to_csv(inbox / f"{name}_{_iso(day)}.csv", index=False)
                counts[name] += len(df)

    log.info("generated %s", counts)
    return counts


def _inject_bad(rng, src, onb, act, txn, rate):
    """Mutate a few rows in place to create realistic data-quality problems."""
    if src and rng.random() < rate * 10:
        src.append(dict(src[rng.randrange(len(src))]))          # duplicate seller_id
    if src and rng.random() < rate * 10:
        src.append({"seller_id": f"S{rng.randint(1, 999999):06d}", "sourced_date": "31/02/2026",
                    "channel": "agent", "region": "NCR"})          # bad date format
    if onb and rng.random() < rate * 10:
        onb.append({"seller_id": f"X{rng.randint(1, 999999):06d}", "onboarded_date": onb[0]["onboarded_date"],
                    "kyc_status": "approved"})                     # orphan FK
    if act and rng.random() < rate * 10:
        r = dict(act[rng.randrange(len(act))]); r["activated_date"] = ""
        act.append(r)                                              # missing required value
    if txn and rng.random() < rate * 10:
        r = dict(txn[rng.randrange(len(txn))]); r["gmv"] = "n/a"
        txn.append(r)                                              # non-numeric gmv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--sellers-per-day", type=int, default=40)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--start", type=str, default="2026-10-01")
    ap.add_argument("--config", type=str, default=None)
    args = ap.parse_args()

    cfg = load_config(args.config)
    generate(args.days, args.sellers_per_day, args.seed, date.fromisoformat(args.start),
             cfg["paths"]["inbox"])


if __name__ == "__main__":
    main()
