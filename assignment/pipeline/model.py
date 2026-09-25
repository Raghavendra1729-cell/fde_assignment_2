# Stage 3: turn validated trips into a small relational model.
#
#   dim_zone (zone_id)      dim_hour (hour_ts) <- weather joined here
#        ^                        ^
#        | do_zone_id             | pickup_hour_ts
#   trip_fact (trip_id) ------ vendor_id -> dim_vendor
#
# trip_events is a view that shows each trip as two events (meter on, meter off).
import logging

from pipeline import config

log = logging.getLogger("pipeline")

# from the TLC yellow trip data dictionary (March 2025 version)
VENDORS = [(1, "Creative Mobile Technologies"), (2, "Curb Mobility"),
           (6, "Myle Technologies"), (7, "Helix")]


def build_static(con):
    con.execute("CREATE OR REPLACE TABLE dim_vendor (vendor_id INTEGER, vendor_name VARCHAR)")
    con.executemany("INSERT INTO dim_vendor VALUES (?, ?)", VENDORS)


def build_dim_hour(con, month):
    # one row per clock hour of the month; weather columns stay NULL if the API failed
    con.execute("""CREATE TABLE IF NOT EXISTS dim_hour (
        hour_ts TIMESTAMP, source_month VARCHAR, pickup_date DATE, pickup_hour INTEGER,
        day_type VARCHAR, precipitation_mm DOUBLE, temperature_c DOUBLE, is_wet BOOLEAN)""")
    con.execute("DELETE FROM dim_hour WHERE source_month = ?", [month])
    con.execute(f"""
        INSERT INTO dim_hour
        SELECT h.hour_ts, ?, CAST(h.hour_ts AS DATE), hour(h.hour_ts),
               CASE WHEN dayofweek(h.hour_ts) IN (0, 6) THEN 'weekend' ELSE 'weekday' END,
               w.precipitation, w.temperature_2m,
               CASE WHEN w.precipitation IS NULL THEN NULL ELSE w.precipitation >= {config.WET_HOUR_MM} END
        FROM (SELECT unnest(generate_series(TIMESTAMP '{month}-01',
                     CAST(last_day(DATE '{month}-01') AS TIMESTAMP) + INTERVAL 23 HOUR,
                     INTERVAL 1 HOUR)) AS hour_ts) h
        LEFT JOIN weather_hourly w ON w.hour_ts = h.hour_ts AND w.source_month = ?
    """, [month, month])
    return con.execute("SELECT count(*) FROM dim_hour WHERE source_month = ?", [month]).fetchone()[0]


def build_trip_fact(con, month):
    con.execute("""CREATE TABLE IF NOT EXISTS trip_fact (
        trip_id VARCHAR, source_month VARCHAR, vendor_id INTEGER, pickup_ts TIMESTAMP, dropoff_ts TIMESTAMP,
        pickup_hour_ts TIMESTAMP, pu_zone_id INTEGER, do_zone_id INTEGER, duration_min DOUBLE,
        trip_distance DOUBLE, fare_amount DOUBLE, tip_amount DOUBLE, tolls_amount DOUBLE, total_amount DOUBLE,
        payment_type BIGINT, is_long BOOLEAN, fare_per_trip_hour DOUBLE,
        flag_short_distance BOOLEAN, flag_odd_fare BOOLEAN, flag_zero_passengers BOOLEAN)""")
    con.execute("DELETE FROM trip_fact WHERE source_month = ?", [month])
    con.execute(f"""
        INSERT INTO trip_fact
        SELECT trip_id, source_month, vendor_id, pickup_ts, dropoff_ts,
               date_trunc('hour', pickup_ts), PULocationID, DOLocationID, duration_min,
               trip_distance, fare_amount, tip_amount, tolls_amount, total_amount, payment_type,
               duration_min > {config.LONG_TRIP_MINUTES},
               fare_amount / (duration_min / 60.0),
               f01, f02, f03
        FROM trips_validated
        WHERE source_month = ? AND is_valid
    """, [month])
    n = con.execute("SELECT count(*) FROM trip_fact WHERE source_month = ?", [month]).fetchone()[0]

    # every fact row must find its hour and its zone, otherwise joins would drop trips
    orphans = con.execute("""
        SELECT count(*) FILTER (WHERE h.hour_ts IS NULL),
               count(*) FILTER (WHERE pz.zone_id IS NULL OR dz.zone_id IS NULL)
        FROM trip_fact t
        LEFT JOIN dim_hour h ON h.hour_ts = t.pickup_hour_ts
        LEFT JOIN dim_zone pz ON pz.zone_id = t.pu_zone_id
        LEFT JOIN dim_zone dz ON dz.zone_id = t.do_zone_id
        WHERE t.source_month = ?""", [month]).fetchone()
    return n, orphans


def build_views(con):
    con.execute("""
        CREATE OR REPLACE VIEW trip_events AS
        SELECT trip_id, 'meter_on' AS event, pickup_ts AS event_ts, pu_zone_id AS zone_id FROM trip_fact
        UNION ALL
        SELECT trip_id, 'meter_off', dropoff_ts, do_zone_id FROM trip_fact
    """)
    # the table the metrics are computed from: fact joined to its dimensions
    con.execute("""
        CREATE OR REPLACE VIEW trip_analysis AS
        SELECT t.*, h.pickup_date, h.pickup_hour, h.day_type, h.precipitation_mm, h.is_wet,
               z.zone_name AS do_zone_name, v.vendor_name
        FROM trip_fact t
        JOIN dim_hour h ON h.hour_ts = t.pickup_hour_ts
        JOIN dim_zone z ON z.zone_id = t.do_zone_id
        LEFT JOIN dim_vendor v ON v.vendor_id = t.vendor_id
    """)


def build_month(con, month, weather_ok):
    build_static(con)
    if not weather_ok:
        con.execute("CREATE TABLE IF NOT EXISTS weather_hourly (source_month VARCHAR, hour_ts TIMESTAMP, "
                    "precipitation DOUBLE, temperature_2m DOUBLE)")
        con.execute("DELETE FROM weather_hourly WHERE source_month = ?", [month])
    hours = build_dim_hour(con, month)
    n, (no_hour, no_zone) = build_trip_fact(con, month)
    build_views(con)
    log.info("  model: %d hours in dim_hour, %d trips in trip_fact, orphans hour=%d zone=%d",
             hours, n, no_hour, no_zone)
    if no_hour or no_zone:
        raise RuntimeError(f"{month}: trip_fact rows without a matching dimension row")
    return n
