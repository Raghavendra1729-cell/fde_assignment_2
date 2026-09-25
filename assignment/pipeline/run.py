# Entry point: python -m pipeline.run --months 2026-04 2026-05 2026-06
#
# ingest -> validate -> model -> metrics. Each month has to pass its validation gate.
# Outputs in outputs/ are only rewritten when every requested month passed, so the
# published evidence always comes from one complete, validated run.
import argparse
import logging
import re
import sys
import time

import duckdb

from pipeline import config, ingest, metrics, model, validate

log = logging.getLogger("pipeline")


def setup_logging():
    config.LOG_DIR.mkdir(exist_ok=True)
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    handlers = [logging.StreamHandler(sys.stdout),
                logging.FileHandler(config.LOG_DIR / "pipeline.log"),               # history of all runs
                logging.FileHandler(config.LOG_DIR / "last_run.log", mode="w")]     # just this run
    for h in handlers:
        h.setFormatter(fmt)
        log.addHandler(h)


def process_month(con, month, manifest, force):
    """Run one month through ingest + validate + model. Returns (passed, counts, rule_rows, check_rows)."""
    log.info("[%s] ingest", month)
    try:
        path = ingest.get_trip_file(month, manifest, force)
    except ingest.RetrievalError as e:
        log.error("  trip file for %s missing: %s", month, e)
        return False, None, [], [validate.check(month, "I01", "trip file retrieved", "FAIL-level", "FAIL", 0, str(e))]

    weather_ok = True
    try:
        wx_path = ingest.get_weather(month, manifest, force)
        ingest.load_weather(con, wx_path, month)
    except ingest.RetrievalError as e:
        # weather only feeds the wet/dry comparison, so this is a warning, not a stop
        log.warning("  %s; wet/dry metric will be n/a for this month", e)
        weather_ok = False

    parquet_rows = manifest["files"][path.name]["parquet_num_rows"]
    rows_loaded, schema_info = ingest.load_trips(con, path)
    log.info("  loaded %d rows (parquet footer says %d)", rows_loaded, parquet_rows)

    log.info("[%s] validate", month)
    checks = validate.file_checks(con, month, rows_loaded, parquet_rows, schema_info)
    checks.append(validate.check(month, "S09", "weather hours complete", "WARN-level",
                                 "PASS" if weather_ok else "WARN", int(weather_ok),
                                 f"{ingest.expected_hours(month)} hours expected"))
    checks.append(validate.volume_check(month, rows_loaded, manifest))
    if any(c["status"] == "FAIL" for c in checks):
        validate.gate(month, checks, {"candidates": 0, "valid": 0, "quality_excluded": 0})
        return False, None, [], checks
    for c in checks:
        if c["check_id"] == "S10" or (c["status"] in ("WARN", "INFO") and c["detail"]):
            log.log(logging.WARNING if c["status"] == "WARN" else logging.INFO,
                    "  %s %s: %s", c["check_id"], c["check"], c["detail"])

    validate.apply_trip_rules(con, month)
    rule_rows = validate.rule_report(con, month)
    for r in rule_rows:
        log.info("  %s %-45s %6d of %d  (%s)", r["rule_id"], r["rule"], r["rows_hit"], r["candidate_trips"],
                 r["action"])
    rec, counts = validate.reconcile(con, month, rows_loaded)
    checks += rec
    passed, gate_checks = validate.gate(month, checks, counts)
    checks += gate_checks
    for c in rec + gate_checks:
        log.info("  %s %s: %s (value %s; %s)", c["check_id"], c["check"], c["status"], c["value"], c["detail"])
    if not passed:
        return False, counts, rule_rows, checks

    validate.save_month(con, month)
    log.info("[%s] model", month)
    try:
        model.build_month(con, month, weather_ok)
    except RuntimeError as e:
        log.error("  model check failed: %s", e)
        return False, counts, rule_rows, checks
    return True, counts, rule_rows, checks


def main(argv=None):
    parser = argparse.ArgumentParser(description="JFK -> Manhattan trip-time pipeline")
    parser.add_argument("--months", nargs="+", required=True, help="months as YYYY-MM")
    parser.add_argument("--force-download", action="store_true", help="download raw files again")
    args = parser.parse_args(argv)
    months = sorted(set(args.months))
    bad = [m for m in months if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", m)]
    if bad:
        parser.error(f"bad month format: {bad}")

    setup_logging()
    start = time.time()
    log.info("pipeline start, months: %s", ", ".join(months))
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(config.DB_PATH))
    con.execute(f"SET memory_limit = '{config.DUCKDB_MEMORY}'")
    manifest = ingest.load_manifest()

    try:
        zone_path = ingest.get_zone_file(manifest, args.force_download)
    except ingest.RetrievalError as e:
        log.error("zone lookup not available, cannot continue: %s", e)
        return 1
    n_zones, jfk_name = ingest.load_zones(con, zone_path)
    log.info("zone lookup: %d zones, zone %d = %s", n_zones, config.JFK_ZONE, jfk_name)
    if jfk_name != "JFK Airport":
        log.error("zone %d is not JFK Airport in the lookup, stopping", config.JFK_ZONE)
        return 1

    results, counts, rule_rows, check_rows = {}, {}, [], []
    for month in months:
        ok, c, rules, checks = process_month(con, month, manifest, args.force_download)
        # working tables for one month; dropped even if the month failed so the db stays small
        con.execute("DROP TABLE IF EXISTS raw_month")
        con.execute("DROP TABLE IF EXISTS month_candidates")
        results[month] = ok
        rule_rows += rules
        check_rows += checks
        if c:
            counts[month] = c
        log.info("[%s] %s", month, "PASSED" if ok else "FAILED, not published")
    ingest.save_manifest(manifest)

    failed = [m for m, ok in results.items() if not ok]
    if failed:
        log.error("months failed: %s. outputs/ left unchanged (last good run stays published)", ", ".join(failed))
        con.close()
        return 1

    log.info("metrics + outputs")
    try:
        metrics.publish(con, months, counts, rule_rows, check_rows)
    except Exception:
        log.exception("publish failed, outputs/ left unchanged")
        con.close()
        return 1
    con.close()
    log.info("done in %.0f s, all %d months published", time.time() - start, len(months))
    return 0


if __name__ == "__main__":
    sys.exit(main())
