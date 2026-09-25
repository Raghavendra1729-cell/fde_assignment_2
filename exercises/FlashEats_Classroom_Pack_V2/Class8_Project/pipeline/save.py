from pathlib import Path
import json
import os
import tempfile


def atomic_write_csv(df, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".csv",
        delete=False,
        dir=destination.parent,
    ) as tmp:
        temp_path = Path(tmp.name)

    try:
        df.to_csv(temp_path, index=False)
        os.replace(temp_path, destination)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def save_outputs(
    journey,
    metrics,
    validation_results,
    project_root: Path,
    run_date: str,
    logger,
):
    partition = (
        project_root
        / "data"
        / "processed"
        / f"run_date={run_date}"
    )

    partition.mkdir(parents=True, exist_ok=True)

    journey_path = partition / "order_journey.csv"
    metrics_path = partition / "metrics.json"
    validation_path = partition / "validation_report.json"

    # Idempotency:
    # same run_date writes to the same logical partition and atomically replaces it.
    atomic_write_csv(journey, journey_path)

    metrics_path.write_text(
        json.dumps(metrics, indent=2)
    )

    validation_payload = [
        {
            "check": r.check,
            "status": r.status,
            "detail": r.detail,
        }
        for r in validation_results
    ]

    validation_path.write_text(
        json.dumps(validation_payload, indent=2)
    )

    logger.info(
        "Saved outputs | partition=%s journey=%s metrics=%s",
        partition,
        journey_path.name,
        metrics_path.name,
    )

    return {
        "journey": journey_path,
        "metrics": metrics_path,
        "validation": validation_path,
    }
