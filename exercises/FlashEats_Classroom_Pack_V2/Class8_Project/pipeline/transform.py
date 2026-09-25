import pandas as pd


def build_order_journey(
    orders,
    interactions,
    interventions,
    dispatch,
    logger,
):
    outcomes = orders.copy()

    outcomes["delay_min"] = (
        outcomes["actual_delivery_at"] - outcomes["promised_eta"]
    ).dt.total_seconds() / 60

    outcomes["order_to_pickup_min"] = (
        outcomes["pickup_at"] - outcomes["created_at"]
    ).dt.total_seconds() / 60

    outcomes["transit_min"] = (
        outcomes["actual_delivery_at"] - outcomes["pickup_at"]
    ).dt.total_seconds() / 60

    outcomes["is_late"] = outcomes["delay_min"] > 0

    interaction_summary = (
        interactions
        .groupby("order_id")
        .agg(
            customer_action_count=("interaction_id", "count"),
        )
        .reset_index()
    )

    intervention_summary = (
        interventions
        .groupby("order_id")
        .agg(
            intervention_count=("intervention_id", "count"),
            intervention_types=(
                "intervention_type",
                lambda s: ",".join(sorted(set(map(str, s)))),
            ),
        )
        .reset_index()
    )

    dispatch_small = dispatch.copy()

    # Keep only fields needed by the model.
    dispatch_cols = [
        c for c in [
            "order_id",
            "current_delivery_eta",
            "assigned_driver_id",
            "reassignment_count",
        ]
        if c in dispatch_small.columns
    ]

    dispatch_small = dispatch_small[dispatch_cols]

    journey = (
        outcomes
        .merge(
            interaction_summary,
            on="order_id",
            how="left",
        )
        .merge(
            intervention_summary,
            on="order_id",
            how="left",
        )
        .merge(
            dispatch_small,
            on="order_id",
            how="left",
            suffixes=("", "_dispatch"),
        )
    )

    for col in [
        "customer_action_count",
        "intervention_count",
    ]:
        if col in journey.columns:
            journey[col] = journey[col].fillna(0).astype(int)

    logger.info(
        "Built order journey | rows=%s columns=%s",
        len(journey),
        len(journey.columns),
    )

    return journey


def build_metrics(journey):
    measurable = journey[
        journey["actual_delivery_at"].notna()
        & journey["promised_eta"].notna()
    ].copy()

    metrics = {
        "orders_in_journey": int(len(journey)),
        "measurable_deliveries": int(len(measurable)),
        "late_delivery_rate_pct": round(
            float(measurable["is_late"].mean() * 100),
            2,
        ) if len(measurable) else None,
        "median_order_to_pickup_min": round(
            float(measurable["order_to_pickup_min"].median()),
            2,
        ) if len(measurable) else None,
        "median_transit_min": round(
            float(measurable["transit_min"].median()),
            2,
        ) if len(measurable) else None,
        "orders_with_intervention": int(
            (journey["intervention_count"] > 0).sum()
        ),
    }

    return metrics
