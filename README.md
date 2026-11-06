# Automated Seller Reporting (Project B)

A repeatable Python + SQL workflow that replaces a manual analyst task: reconcile daily seller
lifecycle exports, compute weekly funnel KPIs, and publish a formatted summary plus a
machine-readable quality report.

> Uses **fictional, generated seller data only.** No real marketplace data is involved.

## Context

**Problem.** A business team receives daily CSV exports for four stages of a seller's lifecycle:
sourcing → onboarding → activation → transactions. An analyst manually opens each file, checks
for broken rows, de-duplicates, joins them in a spreadsheet, computes conversion rates, and
sends a weekly update. This takes hours, is error-prone, and is impossible to audit.

**Manual process being replaced.**

| Step | Manual today | Automated here |
|---|---|---|
| Collect files | Download from shared drive | Drop into `data/inbox/` |
| Check for bad rows | Eyeball in Excel | Validation rules, rejects quarantined with a reason |
| De-duplicate | Remove Duplicates button | Keep-first on natural key, in-file and across runs |
| Join stages | VLOOKUP across sheets | SQLite tables with referential checks |
| Compute KPIs | Pivot tables | Cohort-based SQL, parameterised window |
| Distribute | Copy-paste into email | Excel workbook + JSON report per run |

**Intended users.** Ops / growth analysts who consume the Excel summary; a data engineer who
owns the pipeline and watches the quality report.

## Architecture

```
data/inbox/*.csv ──► ingest.py ──► load.py (SQLite) ──► kpis.sql ──► report.py
                     validate       upsert + run_log    cohort KPIs   Excel + JSON
                        │
                        └──► quarantine table (rejected rows + reason)
```

**Source** — one CSV per table per day: `sourcing_YYYY-MM-DD.csv`, `onboarding_…`,
`activation_…`, `transactions_…`. Schema per table lives in `config.yaml`.

**Processing** — `src/ingest.py` validates each file:
1. required columns present (file-level failure if not)
2. required values non-empty
3. dates are ISO `YYYY-MM-DD`
4. numerics parse (`gmv`, `first_listing_count`)
5. duplicate keys within the file → keep first, quarantine the rest
6. referential integrity — child key must exist in the parent table (DB or same batch)

**Storage** — `src/load.py` writes to SQLite (`output/seller_reporting.db`). Natural keys are
primary keys; loads use `INSERT OR REPLACE`. Every file processed gets a `run_log` row with its
SHA-256 hash; a file already logged as `loaded` is skipped on rerun.

**Report** — `src/kpis.sql` + `src/report.py` produce:
- `output/seller_kpis_<run_id>.xlsx` — Overview, Weekly Cohorts, By Channel, Run Log, Quarantine
- `output/quality_report_<run_id>.json` (+ `quality_report_latest.json`) — counts, reject reasons,
  flags, overall status `ok | warn`

## KPI definitions

All rates are **cohort rates**, not period rates. A cohort is the set of sellers sourced in the
same ISO week. Numerators are sellers *from that cohort* who reached the next stage.

| KPI | Definition | Notes |
|---|---|---|
| Onboarding conversion | distinct onboarded sellers / distinct sourced sellers | any `kyc_status` counts as onboarded |
| Activation conversion | activated sellers / onboarded sellers | activated = `activated_date` within **14 days** of `sourced_date` (`kpi.activation_window_days`) |
| End-to-end conversion | activated sellers / sourced sellers | |
| Time to activation | mean days from `sourced_date` to `activated_date`, activated sellers only | |

**Why cohort, not period?** A period rate (this week's activations ÷ this week's sourcings)
mixes sellers from different weeks in numerator and denominator. When onboarding lags sourcing
by days, period rates swing wildly and can exceed 100%. Cohort rates are stable and comparable.

**Maturity.** A cohort younger than the activation window cannot have reached its final rate.
The `is_mature` column flags this; recent cohorts will always look worse until they age in.
Compare mature cohorts only.

**Distinct-ID logic.** `seller_id` is unique per table (primary key), so a seller cannot be
counted twice within a stage. Duplicates across files are resolved by `INSERT OR REPLACE`
(last file wins — see Known limitations).

## Reproducibility

```bash
# 1. environment
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. generate 28 days of fictional data into data/inbox/
python src/generate_data.py --days 28 --sellers-per-day 40 --seed 42

# 3. run the pipeline
python src/run.py --as-of 2026-10-28

# 4. outputs
ls output/        # seller_kpis_<run_id>.xlsx, quality_report_<run_id>.json, seller_reporting.db
```

Options:
- `--dry-run` — validate only, write nothing
- `--keep-inbox` — don't move processed files to `data/processed/<run_id>/`
- `--as-of` — reference date for cohort maturity (defaults to today)
- `--config` — alternate config file

Environment variables: none required. All paths and thresholds are in `config.yaml`.

Expected output on the default seed (as-of 2026-10-28):

```
cohort_week  sourced  onboarded  activated  onboarding_conv  activation_conv  e2e_conv  is_mature
2026-W39         129         95         68            0.736            0.716     0.527          1
2026-W40         235        180        126            0.766            0.700     0.536          1
2026-W41         239        180        113            0.753            0.628     0.473          0
...
rows read 4847 / loaded 4808 / rejected 39
```

## Reliability

**Tests** — `python -m pytest` (11 tests, ~2s):
- unit: each validation rule in isolation (missing column, missing value, bad date, duplicate
  key, orphan FK, non-numeric)
- integration: end-to-end run on generated data; rerun is idempotent (table counts unchanged,
  all files skipped); processed files leave the inbox; no FK violations survive into the DB;
  KPI rates are bounded in [0, 1] and the funnel is monotonic

**Error paths**
- File missing a required column → status `failed`, moved to `data/quarantine/<run_id>/`,
  exit code 2
- Row fails any check → quarantined with `reject_reason` (semicolon-joined if several), row
  number, and original JSON. The clean rows in the same file still load.
- Rerun of a file already loaded (same name + hash) → skipped, logged as `skipped`
- Same filename, different content → treated as a new file and loaded (REPLACE on key)

**Alerts** — each file's row count is compared to the trailing median of the last 7 loads of
the same table (`alerts.*`). Deviation beyond ±30% adds a `volume_drop` / `volume_spike` flag to
the run log, the quality JSON, and the Excel Overview sheet; overall status becomes `warn`.

**Known limitations**
- Volume alert is noisy during ramp-up: a growing seller base makes every transactions file a
  "spike" vs its trailing median. A growth-adjusted baseline (or comparing to the same weekday
  last week) would fix this.
- `INSERT OR REPLACE` means a corrected re-export silently overwrites the earlier record; there
  is no history. A slowly-changing-dimension pattern would preserve it.
- Referential checks use the *current* DB state, so load order matters (parents first —
  enforced in `run.py`). A child file arriving before its parent file is quarantined and must
  be re-dropped.
- SQLite is single-writer; fine for a daily batch, not for concurrent loads.
- No scheduling included. Any cron / Task Scheduler / n8n trigger that runs
  `python src/run.py` works; exit code 2 signals a failed file.

**Next improvements** — scheduler + notification on `warn`, growth-adjusted anomaly baseline,
SCD-2 history on seller tables, a small dashboard on top of the SQLite file.

## Impact

Concrete, verifiable on this repo — no fabricated savings figures:

- The manual reconcile-and-report step is replaced by one command; a 28-day backfill
  (108 files, ~4.8k rows) runs in about 1 second.
- Every rejected row is retained with a reason, so data-quality issues are evidence, not
  anecdote. On the default seed the pipeline caught 39 bad rows across 5 defect types that a
  manual process would likely miss or silently include.
- Reruns are safe: the idempotency test proves a second run changes nothing.
- The cohort-vs-period distinction and the maturity flag prevent the most common reporting
  error in funnel metrics — comparing an immature cohort to a mature one.

## Repo layout

```
seller-reporting/
├── config.yaml              # paths, schemas, KPI window, alert thresholds
├── requirements.txt
├── README.md
├── data/
│   ├── inbox/               # drop zone (generator writes here)
│   ├── processed/<run_id>/  # files after successful load
│   └── quarantine/<run_id>/ # files that failed file-level checks
├── output/                  # SQLite DB, Excel summaries, JSON quality reports
├── src/
│   ├── common.py            # config, hashing, logging
│   ├── generate_data.py     # fictional data with injected defects
│   ├── ingest.py            # validation rules → clean rows + rejects
│   ├── load.py              # SQLite upsert, run_log, quarantine
│   ├── schema.sql
│   ├── kpis.sql             # weekly cohort funnel
│   ├── kpis_by_channel.sql
│   ├── report.py            # Excel + JSON
│   └── run.py               # orchestrator / CLI
└── tests/
    ├── test_ingest.py
    └── test_pipeline.py
```
