from dataclasses import dataclass
from datetime import date
import pandas as pd


class ValidationError(ValueError):
    pass


@dataclass
class CheckResult:
    check: str
    status: str
    detail: str


ORDER_REQUIRED_COLUMNS = {
    "order_id",
    "customer_id",
    "restaurant_id",
    "created_at",
    "promised_eta",
    "pickup_at",
    "actual_delivery_at",
    "final_status",
}


def validate_required_columns(df, required_columns, dataset_name):
    missing = sorted(set(required_columns) - set(df.columns))

    if missing:
        raise ValidationError(
            f"{dataset_name}: missing required columns: {missing}"
        )

    return CheckResult(
        check=f"{dataset_name}.required_columns",
        status="PASS",
        detail=f"{len(required_columns)} required columns present",
    )


def validate_order_uniqueness(orders):
    duplicate_rows = int(orders.duplicated("order_id", keep=False).sum())

    # This source is intentionally known to contain a few duplicate records.
    # We report WARN so clean() can apply the agreed one-row-per-order rule.
    status = "WARN" if duplicate_rows else "PASS"

    return CheckResult(
        check="orders.order_id_uniqueness",
        status=status,
        detail=f"duplicate rows involved={duplicate_rows}",
    )


def validate_critical_nulls(orders):
    normalized_status = (
        orders["final_status"]
        .astype(str)
        .str.strip()
        .str.lower()
    )

    delivered = normalized_status.eq("delivered")

    missing_actual = int(
        (delivered & orders["actual_delivery_at"].isna()).sum()
    )

    # A delivered row without completion evidence cannot support delay metrics.
    status = "WARN" if missing_actual else "PASS"

    return CheckResult(
        check="orders.delivered_completion_timestamp",
        status=status,
        detail=f"delivered rows missing actual_delivery_at={missing_actual}",
    )


def validate_freshness(orders, run_date: date, max_age_days: int):
    parsed = pd.to_datetime(
        orders["created_at"],
        format="mixed",
        errors="coerce",
    )

    latest = parsed.max()

    if pd.isna(latest):
        raise ValidationError(
            "orders freshness check failed: no parseable created_at values"
        )

    age_days = (pd.Timestamp(run_date) - latest.normalize()).days

    if age_days > max_age_days:
        raise ValidationError(
            f"orders data is stale: latest_created_at={latest}, "
            f"age_days={age_days}, allowed={max_age_days}"
        )

    return CheckResult(
        check="orders.freshness",
        status="PASS",
        detail=f"latest_created_at={latest}; age_days={age_days}",
    )


def validate_dispatch(dispatch):
    required = {
        "order_id",
        "current_delivery_eta",
    }

    return validate_required_columns(
        dispatch,
        required,
        "dispatch",
    )


def run_raw_validations(orders, dispatch, run_date, max_age_days):
    results = [
        validate_required_columns(
            orders,
            ORDER_REQUIRED_COLUMNS,
            "orders",
        ),
        validate_order_uniqueness(orders),
        validate_critical_nulls(orders),
        validate_freshness(
            orders,
            run_date=run_date,
            max_age_days=max_age_days,
        ),
        validate_dispatch(dispatch),
    ]

    return results
