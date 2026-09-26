# Stage 2: profile checks on the whole monthly file, then business rules on the
# JFK -> Manhattan candidate trips. Each trip gets a true/false column per rule
# and every rule's count goes into the validation report.
import logging

from pipeline import config

log = logging.getLogger("pipeline")

# (rule_id, rule, SQL condition on a candidate trip, severity, action, business reason)
# "exclude" rules remove the trip from the metrics, "flag" rules keep it but count it.
TRIP_RULES = [
    ("V01", "pickup outside the file's month", "strftime(pickup_ts, '%Y-%m') <> '{month}'",
     "high", "exclude", "clock error or late record; it would leak into the wrong month's KPI"),
    ("V02", "dropoff at or before pickup", "dropoff_ts <= pickup_ts",
     "high", "exclude", "trip time can't be measured, and trip time is the whole question"),
    ("V03", f"duration under {config.MIN_PLAUSIBLE_MINUTES} min",
     f"duration_min > 0 AND duration_min < {config.MIN_PLAUSIBLE_MINUTES}",
     "high", "exclude", "JFK to Manhattan is 13+ miles, under 10 min is not a real run"),
    ("V04", f"duration over {config.MAX_PLAUSIBLE_MINUTES} min", f"duration_min > {config.MAX_PLAUSIBLE_MINUTES}",
     "medium", "exclude", "meter most likely left running; would inflate P90 and long-trip rate"),
    ("V05", "fare zero or negative", "fare_amount <= 0",
     "medium", "exclude", "refund, void or dispute reversal, not a completed paid run"),
    ("V06", "rate code is not the JFK flat fare (2)", f"RatecodeID IS DISTINCT FROM {config.FLAT_FARE_RATECODE}",
     "scope", "exclude (out of scope)", "metered, negotiated, flex-fare or missing code; flat-fare economics don't apply"),
    ("F01", f"distance under {config.SHORT_DISTANCE_MILES} miles", f"trip_distance < {config.SHORT_DISTANCE_MILES}",
     "low", "flag, keep", "distance meter can fail on a real trip; duration and fare still look normal"),
    ("F02", "flat-fare trip with fare other than $70",
     f"RatecodeID = {config.FLAT_FARE_RATECODE} AND fare_amount > 0 AND fare_amount <> {config.FLAT_FARE_DOLLARS}",
     "low", "flag, keep", "flat fare should be exactly $70; kept, and the actual fare is used in $/hour"),
    ("F03", "passenger_count is 0", "passenger_count = 0",
     "low", "flag, keep", "driver-entered field, not used by any metric"),
]
QUALITY_RULES = ["V01", "V02", "V03", "V04", "V05"]
SCOPE_RULES = ["V06"]


def candidate_filter():
    # A "JFK -> Manhattan run" = meter engaged in zone 132 and disengaged in any Manhattan zone.
    # Defined by zone pair, not by RatecodeID alone: the observed final rate code does not reliably identify route.
    return (f"PULocationID = {config.JFK_ZONE} AND DOLocationID IN "
            f"(SELECT zone_id FROM dim_zone WHERE borough = 'Manhattan')")


def check(month, check_id, name, severity, status, value, detail=""):
    return {"month": month, "check_id": check_id, "check": name, "severity": severity,
            "status": status, "value": str(value), "detail": detail}


def file_checks(con, month, rows_loaded, parquet_rows, schema_info):
    """Check the whole monthly file (all ~4M rows) before narrowing to JFK."""
    out = []
    missing = schema_info["missing"]
    out.append(check(month, "S01", "required columns present", "FAIL-level",
                     "FAIL" if missing else "PASS", len(missing),
                     ("missing: " + ", ".join(missing)) if missing else
                     ("columns not in the data dictionary (ignored): " + ", ".join(schema_info["extra"]))
                     if schema_info["extra"] else ""))
    if missing:
        return out

    out.append(check(month, "S02", "rows loaded = rows in parquet footer", "FAIL-level",
                     "PASS" if rows_loaded == parquet_rows else "FAIL", rows_loaded,
                     f"parquet metadata says {parquet_rows}"))

    in_month, days_seen = con.execute(f"""
        SELECT avg(CASE WHEN strftime(tpep_pickup_datetime, '%Y-%m') = '{month}' THEN 1 ELSE 0 END),
               count(DISTINCT CASE WHEN strftime(tpep_pickup_datetime, '%Y-%m') = '{month}'
                                   THEN CAST(tpep_pickup_datetime AS DATE) END)
        FROM raw_month""").fetchone()
    days_in_month = con.execute(
        f"SELECT day(last_day(DATE '{month}-01'))").fetchone()[0]
    out.append(check(month, "S03", "share of pickups inside the file's month", "FAIL-level",
                     "PASS" if in_month >= config.MIN_SHARE_IN_MONTH else "FAIL", round(in_month, 6),
                     f"threshold {config.MIN_SHARE_IN_MONTH}"))
    out.append(check(month, "S04", "every day of the month has trips", "FAIL-level",
                     "PASS" if days_seen == days_in_month else "FAIL", days_seen,
                     f"{days_in_month} days expected"))

    # A vendor whose dropoff time always equals pickup time makes duration useless for its trips
    bad = con.execute("""
        SELECT VendorID, count(*) n, avg(CASE WHEN tpep_dropoff_datetime <= tpep_pickup_datetime
                                              THEN 1 ELSE 0 END) share_bad
        FROM raw_month GROUP BY 1 HAVING share_bad > 0.5 ORDER BY 1""").fetchall()
    detail = "; ".join(f"VendorID {v}: {s:.1%} of {n} trips have dropoff <= pickup" for v, n, s in bad)
    out.append(check(month, "S05", "vendors with unusable dropoff timestamps", "WARN-level",
                     "WARN" if bad else "PASS", len(bad), detail))

    null_rc, flex = con.execute("""
        SELECT avg(CASE WHEN RatecodeID IS NULL THEN 1 ELSE 0 END),
               avg(CASE WHEN RatecodeID IS NULL AND payment_type = 0 THEN 1 ELSE 0 END)
        FROM raw_month""").fetchone()
    out.append(check(month, "S06", "share of rows with null RatecodeID", "INFO", "INFO", round(null_rc, 4),
                     f"{flex:.2%} of all rows are null RatecodeID with payment_type 0 (Flex Fare)"))

    manhattan = "(SELECT zone_id FROM dim_zone WHERE borough = 'Manhattan')"
    jfk = config.JFK_ZONE
    rc2 = con.execute(f"""
        SELECT count(*),
               count(*) FILTER (WHERE PULocationID = {jfk} AND DOLocationID IN {manhattan}),
               count(*) FILTER (WHERE DOLocationID = {jfk} AND PULocationID IN {manhattan}),
               count(*) FILTER (WHERE PULocationID <> {jfk} AND DOLocationID <> {jfk})
        FROM raw_month WHERE RatecodeID = {config.FLAT_FARE_RATECODE}""").fetchone()
    out.append(check(month, "S07", "RatecodeID 2 trips that are not JFK -> Manhattan", "INFO", "INFO",
                     rc2[0] - rc2[1],
                     f"of {rc2[0]} RatecodeID 2 trips: {rc2[1]} JFK->Manhattan, {rc2[2]} Manhattan->JFK, "
                     f"{rc2[3]} touch neither end at JFK"))

    unknown = con.execute(f"""
        SELECT count(*) FILTER (WHERE DOLocationID = 264), count(*) FILTER (WHERE DOLocationID = 265)
        FROM raw_month WHERE PULocationID = {jfk}""").fetchone()
    out.append(check(month, "S08", "JFK pickups with unknown / outside-NYC dropoff zone", "INFO", "INFO",
                     unknown[0] + unknown[1],
                     f"{unknown[0]} to zone 264 (Unknown), {unknown[1]} to zone 265 (Outside of NYC); "
                     "not counted as Manhattan runs"))
    return out


def volume_check(month, rows_loaded, manifest):
    """Compare the row count with the previous month's file when available. A big jump or drop
    usually means a partial or duplicated file."""
    year, mon = int(month[:4]), int(month[5:])
    prev = f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"
    entry = manifest["files"].get(f"yellow_tripdata_{prev}.parquet", {})
    if "parquet_num_rows" not in entry:
        return check(month, "S10", "row count within 15% of previous month", "WARN-level", "INFO", "n/a",
                     f"{prev} not downloaded, nothing to compare with")
    change = rows_loaded / entry["parquet_num_rows"] - 1
    return check(month, "S10", "row count within 15% of previous month", "WARN-level",
                 "PASS" if abs(change) <= config.MAX_MONTH_CHANGE else "WARN", round(change, 4),
                 f"{rows_loaded} rows vs {entry['parquet_num_rows']} in {prev}")


def apply_trip_rules(con, month, source="raw_month"):
    """Build month_candidates: every JFK -> Manhattan trip with one boolean column per rule."""
    flags = ",\n".join(f"({cond.format(month=month)}) IS TRUE AS {rid.lower()}"
                       for rid, _, cond, *_ in TRIP_RULES)
    quality = " OR ".join(r.lower() for r in QUALITY_RULES)
    scope = " OR ".join(r.lower() for r in SCOPE_RULES)
    con.execute(f"""
        CREATE OR REPLACE TABLE month_candidates AS
        WITH c AS (
            SELECT '{month}' AS source_month,
                   VendorID AS vendor_id,
                   tpep_pickup_datetime AS pickup_ts,
                   tpep_dropoff_datetime AS dropoff_ts,
                   date_diff('second', tpep_pickup_datetime, tpep_dropoff_datetime) / 60.0 AS duration_min,
                   * EXCLUDE (VendorID, tpep_pickup_datetime, tpep_dropoff_datetime)
            FROM {source}
            WHERE {candidate_filter()}
        ), f AS (
            SELECT *, {flags} FROM c
        )
        SELECT source_month || '-' || lpad(CAST(row_number() OVER (
                   ORDER BY pickup_ts, dropoff_ts, vendor_id, DOLocationID, fare_amount,
                            trip_distance, tip_amount, total_amount) AS VARCHAR), 6, '0') AS trip_id,
               *,
               ({quality}) AS quality_excluded,
               ({scope}) AS scope_excluded,
               NOT ({quality}) AND NOT ({scope}) AS is_valid
        FROM f
    """)
    return con.execute("SELECT count(*) FROM month_candidates").fetchone()[0]


def rule_report(con, month):
    """One row per rule with how many candidate trips it hit."""
    total = con.execute("SELECT count(*) FROM month_candidates").fetchone()[0]
    rows = []
    for rid, name, _, severity, action, reason in TRIP_RULES:
        hit = con.execute(f"SELECT count(*) FILTER (WHERE {rid.lower()}) FROM month_candidates").fetchone()[0]
        rows.append({"month": month, "rule_id": rid, "rule": name, "severity": severity, "action": action,
                     "rows_hit": hit, "candidate_trips": total,
                     "share": round(hit / total, 6) if total else 0.0, "business_reason": reason})
    return rows


def reconcile(con, month, rows_loaded):
    """Row counts have to add up between stages, or something went missing."""
    cand, valid, qual, scope_only = con.execute("""
        SELECT count(*), count(*) FILTER (WHERE is_valid), count(*) FILTER (WHERE quality_excluded),
               count(*) FILTER (WHERE scope_excluded AND NOT quality_excluded)
        FROM month_candidates""").fetchone()
    non_cand = con.execute(f"SELECT count(*) FROM raw_month WHERE NOT ({candidate_filter()})").fetchone()[0]
    out = [
        check(month, "R01", "raw rows = JFK->Manhattan candidates + all other rows", "FAIL-level",
              "PASS" if rows_loaded == cand + non_cand else "FAIL", rows_loaded,
              f"{cand} candidates + {non_cand} other = {cand + non_cand}"),
        check(month, "R02", "candidates = valid + quality-excluded + scope-excluded", "FAIL-level",
              "PASS" if cand == valid + qual + scope_only else "FAIL", cand,
              f"{valid} valid + {qual} quality-excluded + {scope_only} out of scope = {valid + qual + scope_only}"),
    ]
    return out, {"candidates": cand, "valid": valid, "quality_excluded": qual, "scope_excluded": scope_only}


def gate(month, checks, counts):
    """Decide if the month can be published. Returns (passed, gate_checks)."""
    rate = counts["quality_excluded"] / counts["candidates"] if counts["candidates"] else 1.0
    gate_checks = [
        check(month, "G01", "quality exclusion rate within limit", "FAIL-level",
              "PASS" if rate <= config.MAX_QUALITY_EXCLUSION_RATE else "FAIL", round(rate, 6),
              f"limit {config.MAX_QUALITY_EXCLUSION_RATE}"),
        check(month, "G02", "enough valid trips to publish", "FAIL-level",
              "PASS" if counts["valid"] >= config.MIN_VALID_TRIPS else "FAIL", counts["valid"],
              f"minimum {config.MIN_VALID_TRIPS}"),
    ]
    failed = [c for c in checks + gate_checks if c["status"] == "FAIL"]
    for c in failed:
        log.error("  %s FAILED: %s (value %s, %s)", c["check_id"], c["check"], c["value"], c["detail"])
    return not failed, gate_checks


def save_month(con, month):
    # keep every candidate with its flags so exclusions can be audited later
    con.execute("CREATE TABLE IF NOT EXISTS trips_validated AS SELECT * FROM month_candidates WHERE false")
    con.execute("DELETE FROM trips_validated WHERE source_month = ?", [month])
    con.execute("INSERT INTO trips_validated SELECT * FROM month_candidates")
