import pandas as pd


def clean_orders(orders, logger):
    cleaned = orders.copy()

    before = len(cleaned)

    # Agreed business grain: one row per order_id.
    cleaned = cleaned.drop_duplicates(
        subset=["order_id"],
        keep="first",
    ).copy()

    cleaned["final_status"] = (
        cleaned["final_status"]
        .astype(str)
        .str.strip()
        .str.lower()
    )

    if "traffic_bucket" in cleaned.columns:
        cleaned["traffic_bucket"] = (
            cleaned["traffic_bucket"]
            .astype(str)
            .str.strip()
            .str.lower()
        )

    for col in [
        "created_at",
        "promised_eta",
        "pickup_at",
        "actual_delivery_at",
    ]:
        if col in cleaned.columns:
            cleaned[col] = pd.to_datetime(
                cleaned[col],
                format="mixed",
                errors="coerce",
            )

    logger.info(
        "Cleaned orders | input_rows=%s output_rows=%s deduplicated=%s",
        before,
        len(cleaned),
        before - len(cleaned),
    )

    return cleaned
