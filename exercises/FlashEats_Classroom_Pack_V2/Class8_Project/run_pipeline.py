from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pandas as pd
import requests

from pipeline.config import PipelineConfig
from pipeline.logging_utils import build_logger
from pipeline.extract import (
    extract_local_sources,
    extract_dispatch_api,
)
from pipeline.validate import (
    ValidationError,
    run_raw_validations,
)
from pipeline.clean import clean_orders
from pipeline.transform import (
    build_order_journey,
    build_metrics,
)
from pipeline.save import save_outputs


def find_pack_root(start: Path) -> Path:
    start = start.resolve()

    # Fast path: current folder itself is pack root.
    if (start / "database" / "flasheats.db").exists():
        return start

    # Search parents first.
    for parent in [start, *start.parents]:
        if (parent / "database" / "flasheats.db").exists():
            return parent

        for name in [
            "FlashEats_Classroom_Pack_V2",
            "FlashEats_Classroom_Pack",
        ]:
            candidate = parent / name
            if (candidate / "database" / "flasheats.db").exists():
                return candidate

    # Limited recursive search under current folder.
    for candidate in start.rglob("*"):
        if (
            candidate.is_dir()
            and (candidate / "database" / "flasheats.db").exists()
        ):
            return candidate

    raise FileNotFoundError(
        "Could not find FlashEats pack root containing "
        "database/flasheats.db"
    )


def wait_for_health(api_url: str, timeout_seconds: int = 12) -> bool:
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        try:
            response = requests.get(
                f"{api_url}/health",
                timeout=1,
            )
            if response.status_code == 200:
                return True
        except requests.RequestException:
            pass

        time.sleep(0.5)

    return False


def maybe_start_mock_api(config, logger):
    if not config.start_mock_api:
        logger.info("Mock API autostart disabled")
        return None

    if wait_for_health(config.dispatch_api_url, timeout_seconds=1):
        logger.info("Dispatch API already running")
        return None

    script = config.pack_root / "api" / "mock_dispatch_api.py"

    if not script.exists():
        raise FileNotFoundError(
            f"Mock API script not found: {script}"
        )

    logger.info("Starting local mock dispatch API")

    process = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not wait_for_health(config.dispatch_api_url):
        process.terminate()
        raise RuntimeError(
            "Mock dispatch API did not become healthy"
        )

    logger.info("Mock dispatch API is healthy")
    return process


def apply_chaos_scenario(sources, scenario, run_date, logger):
    if scenario == "none":
        return sources

    sources = dict(sources)
    orders = sources["orders"].copy()

    if scenario == "missing_column":
        logger.warning(
            "CHAOS: dropping promised_eta to simulate schema break"
        )
        orders = orders.drop(columns=["promised_eta"])

    elif scenario == "duplicate_order":
        logger.warning(
            "CHAOS: injecting additional duplicate order"
        )
        orders = pd.concat(
            [orders, orders.iloc[[0]].copy()],
            ignore_index=True,
        )

    elif scenario == "stale_data":
        logger.warning(
            "CHAOS: shifting created_at backward by 365 days"
        )
        parsed = pd.to_datetime(
            orders["created_at"],
            format="mixed",
            errors="coerce",
        )
        orders["created_at"] = (
            parsed - pd.Timedelta(days=365)
        ).astype(str)

    else:
        raise ValueError(
            f"Unknown chaos scenario: {scenario}"
        )

    sources["orders"] = orders
    return sources


def run(run_date: date, chaos: str):
    project_root = Path(__file__).resolve().parent
    pack_root = find_pack_root(project_root)

    config = PipelineConfig.from_env(
        pack_root=pack_root,
        project_root=project_root,
    )

    log_path = (
        project_root
        / "logs"
        / f"pipeline_{run_date.isoformat()}.log"
    )

    logger = build_logger(
        log_path=log_path,
        level=config.log_level,
    )

    api_process = None

    try:
        logger.info(
            "Pipeline started | run_date=%s chaos=%s",
            run_date,
            chaos,
        )

        api_process = maybe_start_mock_api(
            config,
            logger,
        )

        # 1. EXTRACT
        sources = extract_local_sources(
            config.pack_root,
            logger,
        )

        sources = apply_chaos_scenario(
            sources,
            chaos,
            run_date,
            logger,
        )

        raw_dispatch_dir = (
            project_root
            / "data"
            / "raw"
            / "dispatch"
            / f"run_date={run_date.isoformat()}"
        )

        dispatch = extract_dispatch_api(
            api_url=config.dispatch_api_url,
            page_size=config.page_size,
            max_retries=config.max_retries,
            retry_base_seconds=config.retry_base_seconds,
            raw_output_dir=raw_dispatch_dir,
            logger=logger,
        )

        # 2. VALIDATE RAW INPUTS
        validation_results = run_raw_validations(
            orders=sources["orders"],
            dispatch=dispatch,
            run_date=run_date,
            max_age_days=config.max_data_age_days,
        )

        for result in validation_results:
            logger.info(
                "Validation | check=%s status=%s detail=%s",
                result.check,
                result.status,
                result.detail,
            )

        # 3. CLEAN
        clean_orders_df = clean_orders(
            sources["orders"],
            logger,
        )

        # 4. TRANSFORM
        journey = build_order_journey(
            orders=clean_orders_df,
            interactions=sources["interactions"],
            interventions=sources["interventions"],
            dispatch=dispatch,
            logger=logger,
        )

        metrics = build_metrics(journey)

        # 5. SAVE
        outputs = save_outputs(
            journey=journey,
            metrics=metrics,
            validation_results=validation_results,
            project_root=project_root,
            run_date=run_date.isoformat(),
            logger=logger,
        )

        logger.info(
            "Pipeline completed successfully | rows=%s output=%s",
            len(journey),
            outputs["journey"],
        )

        print("\nPIPELINE SUCCESS")
        print(json.dumps(metrics, indent=2))
        print("\nOutputs:")
        for key, path in outputs.items():
            print(f"  {key}: {path}")

        return 0

    except ValidationError as exc:
        logger.error(
            "Pipeline stopped at validation gate | error=%s | no processed output written",
            exc,
        )
        print(f"\nPIPELINE FAILED: {exc}")
        return 2

    except Exception as exc:
        logger.exception(
            "Pipeline failed unexpectedly | error=%s",
            exc,
        )
        print(f"\nPIPELINE FAILED: {exc}")
        return 1

    finally:
        if api_process is not None:
            api_process.terminate()
            try:
                api_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                api_process.kill()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the FlashEats Class 8 data pipeline"
    )

    parser.add_argument(
        "--run-date",
        default=date.today().isoformat(),
        help="Logical run date in YYYY-MM-DD format",
    )

    parser.add_argument(
        "--chaos",
        default="none",
        choices=[
            "none",
            "missing_column",
            "duplicate_order",
            "stale_data",
        ],
        help="Optional failure scenario for classroom testing",
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    try:
        logical_run_date = date.fromisoformat(args.run_date)
    except ValueError:
        raise SystemExit(
            "--run-date must use YYYY-MM-DD"
        )

    raise SystemExit(
        run(
            run_date=logical_run_date,
            chaos=args.chaos,
        )
    )
