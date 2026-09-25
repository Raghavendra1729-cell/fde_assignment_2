from pathlib import Path
import json
import sqlite3
import time

import pandas as pd
import requests


class RetryableAPIError(RuntimeError):
    pass


class NonRetryableAPIError(RuntimeError):
    pass


def extract_local_sources(pack_root: Path, logger):
    db_path = pack_root / "database" / "flasheats.db"

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    with sqlite3.connect(db_path) as con:
        orders = pd.read_sql("SELECT * FROM orders", con)
        customers = pd.read_sql("SELECT * FROM customers", con)
        drivers = pd.read_sql("SELECT * FROM drivers", con)
        restaurants = pd.read_sql("SELECT * FROM restaurants", con)

    data_dir = pack_root / "data"

    interactions = pd.read_csv(data_dir / "customer_interactions.csv")
    interventions = pd.read_csv(data_dir / "order_interventions.csv")

    logger.info(
        "Extracted local sources | orders=%s interactions=%s interventions=%s",
        len(orders),
        len(interactions),
        len(interventions),
    )

    return {
        "orders": orders,
        "customers": customers,
        "drivers": drivers,
        "restaurants": restaurants,
        "interactions": interactions,
        "interventions": interventions,
    }


def _request_page(session, url, page, page_size, max_retries, base_seconds, logger):
    for attempt in range(1, max_retries + 1):
        try:
            response = session.get(
                f"{url}/dispatch/orders",
                params={"page": page, "page_size": page_size},
                timeout=5,
            )

            if response.status_code == 200:
                return response.json()

            if response.status_code in {429, 500, 502, 503, 504}:
                retry_after = response.headers.get("Retry-After")
                if response.status_code == 429:
                    try:
                        body = response.json()
                        retry_after = body.get(
                            "retry_after_seconds",
                            retry_after,
                        )
                    except Exception:
                        pass

                wait_seconds = (
                    float(retry_after)
                    if retry_after is not None
                    else base_seconds * (2 ** (attempt - 1))
                )

                logger.warning(
                    "Retryable dispatch API failure | page=%s status=%s attempt=%s/%s wait=%.1fs",
                    page,
                    response.status_code,
                    attempt,
                    max_retries,
                    wait_seconds,
                )

                if attempt < max_retries:
                    time.sleep(wait_seconds)
                    continue

                raise RetryableAPIError(
                    f"Dispatch API page {page} failed after "
                    f"{max_retries} attempts; status={response.status_code}"
                )

            raise NonRetryableAPIError(
                f"Dispatch API returned non-retryable status "
                f"{response.status_code} on page {page}"
            )

        except requests.RequestException as exc:
            wait_seconds = base_seconds * (2 ** (attempt - 1))
            logger.warning(
                "Dispatch API request exception | page=%s attempt=%s/%s error=%s",
                page,
                attempt,
                max_retries,
                exc,
            )
            if attempt < max_retries:
                time.sleep(wait_seconds)
                continue
            raise RetryableAPIError(
                f"Dispatch API page {page} failed after "
                f"{max_retries} attempts: {exc}"
            ) from exc


def extract_dispatch_api(
    api_url,
    page_size,
    max_retries,
    retry_base_seconds,
    raw_output_dir: Path,
    logger,
):
    raw_output_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    page = 1
    rows = []
    expected_total = None

    while True:
        payload = _request_page(
            session=session,
            url=api_url,
            page=page,
            page_size=page_size,
            max_retries=max_retries,
            base_seconds=retry_base_seconds,
            logger=logger,
        )

        # Preserve each raw API response before transforming it.
        (raw_output_dir / f"dispatch_page_{page:03d}.json").write_text(
            json.dumps(payload, indent=2)
        )

        if expected_total is None:
            expected_total = int(payload.get("total_records", 0))

        page_rows = payload.get("data", [])
        rows.extend(page_rows)

        logger.info(
            "Fetched dispatch API page | page=%s rows=%s cumulative=%s",
            page,
            len(page_rows),
            len(rows),
        )

        if not payload.get("has_more", False):
            break

        page += 1

    if expected_total and len(rows) != expected_total:
        raise ValueError(
            f"Dispatch retrieval incomplete: expected={expected_total}, "
            f"received={len(rows)}"
        )

    logger.info(
        "Dispatch extraction complete | records=%s expected=%s",
        len(rows),
        expected_total,
    )

    return pd.DataFrame(rows)
