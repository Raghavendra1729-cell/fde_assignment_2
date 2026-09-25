# Gate 2 — Data Readiness Review

Run date used: `2026-09-22`. Evidence comes from the runs in `FlashEats_Class8_Walkthrough.ipynb`, `logs/pipeline_2026-09-22.log` and `data/processed/run_date=2026-09-22/`.

## Pipeline run

- [x] Complete flow runs with one command (`python run_pipeline.py --run-date 2026-09-22`, return code 0)
- [x] Same run can be safely repeated (second run: 1600 rows again, output identical to the first)
- [x] Raw API responses are preserved (8 pages in `data/raw/dispatch/run_date=2026-09-22/`)
- [x] Processed output is written only after validation (missing_column and stale_data runs returned 2 and did not touch the processed files)

## Validation

| Check | Status | Evidence / note |
|---|---|---|
| Required columns | PASS | 8 required order columns present. With `--chaos missing_column` the run stops with `orders: missing required columns: ['promised_eta']` |
| Critical nulls | WARN | 37 delivered orders have no `actual_delivery_at`. They are left out of the late rate (1495 measurable deliveries) |
| Order uniqueness | WARN | 6 rows involved (3 order_ids). Clean keeps the first row, output has 1600 unique orders. `duplicate_order` chaos: 8 rows involved, still 1600 out |
| Freshness | PASS | latest `created_at` 2026-08-28 23:33, 25 days old, limit is 60. `stale_data` chaos fails with age_days=390 |
| Dispatch retrieval completeness | PASS | 1600 records fetched = `total_records` 1600, 8 pages of 200 |

## Reliability

| Capability | Status | Evidence / note |
|---|---|---|
| Bounded retries | PASS | Every run: page 3 HTTP 500 and page 5 HTTP 429, both retried once after 1.0s (attempt 1/3), then succeeded |
| Useful failure message | PASS | Error names the dataset and the column, log line says "no processed output written", return code 2 |
| Logging | PASS | Console and `logs/pipeline_2026-09-22.log`, with row counts per stage, retry attempts and the validation results |
| Idempotent rerun | PASS | Same partition replaced, 1600 rows both times, no extra files in the folder |
| Configuration outside core logic | PASS | API URL, retries, backoff, freshness limit, page size, log level read from environment (`config/.env.example`) |

## Known limitations

- 37 delivered orders have no delivery timestamp, so the late rate (56.39%) is calculated on 1495 orders only.
- The Late Delivery Rate still has no documented owner (Class 6). The pipeline computes the "any delay > 0" version, which is not agreed yet.
- `driver_arrived_at_restaurant` is still not captured, so the order-to-pickup time (median 26.07 min) can't be split into kitchen time and driver time.
- Only `order_journey.csv` is written atomically. `metrics.json` and `validation_report.json` use a plain write.
- A failed run leaves the previous day's partition in place and nothing alerts anyone, so a dashboard could keep showing old numbers without anyone knowing.
- The Class 8 pack ships its own smaller `order_interventions.csv` (260 rows). I kept the Class 7 file (430 rows) from the pack so the numbers match my Class 7 notebook.

## Gate decision

**NOT READY**

Reason: the pipeline itself passed every reliability check (one command, retries, validation gate, idempotent rerun, logs). The data is not ready for the next AI phase: the late label has no owner, 37 deliveries have no completion time, and the event that would explain most of the delay (driver arrival at the restaurant) does not exist. The daily metrics feed can run now as long as the definition is shown next to the number. The AI delay predictor should wait until the KPI owner signs off the definition and the arrival event is captured.
