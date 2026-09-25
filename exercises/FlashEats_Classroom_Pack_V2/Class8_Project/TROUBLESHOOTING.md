# Troubleshooting Notes

## `Could not find FlashEats pack root`

Run the command from inside `Class8_Project`, or ensure the project remains inside the classroom pack.

The pipeline searches for a parent folder containing:

```text
database/flasheats.db
```

It does not depend on only one folder name.

## Dispatch API cannot start

Install requirements:

```bash
python -m pip install -r requirements.txt
```

Check whether port 8000 is already occupied.

You can disable automatic API startup:

```bash
START_MOCK_API=false python run_pipeline.py --run-date 2026-09-22
```

If you do this, you must start the API separately.

## Pipeline stops on freshness

The logical run date is too far from the latest source record.

For the classroom data, either:
- use the class run date, or
- adjust `MAX_DATA_AGE_DAYS` deliberately.

Example:

```bash
MAX_DATA_AGE_DAYS=90 python run_pipeline.py --run-date 2026-09-22
```

Do not silently remove the freshness check.

## Validation failure

Read the final error and then open:

```text
logs/pipeline_<run-date>.log
```

A dependable pipeline should make the failed stage and reason visible.
