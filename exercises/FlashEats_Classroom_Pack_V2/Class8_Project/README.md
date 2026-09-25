# FlashEats — Class 8 Dependable Data Pipeline

## Story

Class 7 produced a useful `order_journey` model and workflow metrics.

Class 8 asks:

> Can this run tomorrow without the person who wrote the notebook?

This project turns the one-time analysis into a small repeatable pipeline.

## Pipeline

```text
EXTRACT
  ↓
VALIDATE
  ↓
CLEAN
  ↓
TRANSFORM
  ↓
SAVE
  ↓
LOG
```

If validation fails, processed output is not published.

## Setup

From this folder:

```bash
python -m pip install -r requirements.txt
```

No `.env` file is required for the classroom defaults.

`config/.env.example` documents the supported environment variables.

## Run the complete flow

```bash
python run_pipeline.py --run-date 2026-09-22
```

The command automatically starts the local FlashEats mock Dispatch API when needed.

The API intentionally demonstrates:
- pagination,
- a transient HTTP 500,
- a transient HTTP 429,
- retry behavior.

## Outputs

A successful run writes:

```text
data/
├── raw/
│   └── dispatch/
│       └── run_date=YYYY-MM-DD/
│           └── dispatch_page_*.json
└── processed/
    └── run_date=YYYY-MM-DD/
        ├── order_journey.csv
        ├── metrics.json
        └── validation_report.json
```

Logs are written to:

```text
logs/pipeline_YYYY-MM-DD.log
```

## Idempotency

Run the same command twice:

```bash
python run_pipeline.py --run-date 2026-09-22
python run_pipeline.py --run-date 2026-09-22
```

The same logical partition is atomically replaced rather than appended.

## Chaos / failure demos

### Missing required column

```bash
python run_pipeline.py \
  --run-date 2026-09-22 \
  --chaos missing_column
```

Expected: validation failure and no new processed output.

### Duplicate order

```bash
python run_pipeline.py \
  --run-date 2026-09-22 \
  --chaos duplicate_order
```

Expected: uniqueness warning; agreed cleaning rule deduplicates at order grain.

### Stale source data

```bash
python run_pipeline.py \
  --run-date 2026-09-22 \
  --chaos stale_data
```

Expected: freshness validation failure.

## What this project deliberately does NOT include

- Airflow
- Spark
- Kafka
- distributed processing
- enterprise observability
- production secrets management

The goal is to learn the reliability concepts underneath those systems.
