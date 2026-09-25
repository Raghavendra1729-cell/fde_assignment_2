# Stage 1: retrieve raw inputs (parquet + csv files over HTTPS, weather JSON from an API)
# and load the trip file into DuckDB. Raw files are saved untouched in data/raw/.
import hashlib
import json
import logging
import time
from datetime import datetime, timezone

import pandas as pd
import pyarrow.parquet as pq
import requests

from pipeline import config

log = logging.getLogger("pipeline")

class RetrievalError(Exception):
    pass


class NotPublished(RetrievalError):
    pass


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest():
    if config.MANIFEST_PATH.exists():
        return json.loads(config.MANIFEST_PATH.read_text())
    return {"files": {}}


def save_manifest(manifest):
    manifest["files"] = dict(sorted(manifest["files"].items()))
    config.MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def republished(url, entry):
    # TLC sometimes replaces a month's file. A HEAD request is cheap, so compare Last-Modified.
    # If the server can't be reached we keep the local copy, so an offline rerun still works.
    try:
        r = requests.head(url, timeout=30)
    except requests.RequestException as e:
        log.warning("  could not check %s for a newer version (%s), using local copy", url, str(e)[:100])
        return False
    last_modified = r.headers.get("Last-Modified", "")
    if r.status_code != 200 or not last_modified:
        log.warning("  HEAD %s gave HTTP %s, using local copy", url, r.status_code)
        return False
    return last_modified != entry.get("last_modified")


def download_file(url, dest, manifest, force=False, check_remote=False):
    """Download url to dest unless we already have it. Returns the manifest entry."""
    name = dest.name
    entry = manifest["files"].get(name)

    # Skip if the local file has the same size and sha256 as when we downloaded it
    # (and, for trip files, the server's Last-Modified hasn't changed).
    if dest.exists() and entry and not force:
        if dest.stat().st_size == entry["bytes"] and sha256_of(dest) == entry["sha256"]:
            if check_remote and republished(url, entry):
                log.warning("  %s was re-published since the last download, downloading again", name)
            else:
                log.info("  skip download, %s already present (sha256 matches manifest)", name)
                return entry
        else:
            log.warning("  local %s does not match manifest, downloading again", name)

    tmp = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, config.DOWNLOAD_RETRIES + 1):
        try:
            log.info("  downloading %s (attempt %d)", url, attempt)
            with requests.get(url, stream=True, timeout=60) as r:
                if r.status_code in (403, 404):
                    # CloudFront answers 403 for a month that isn't published; retrying won't help
                    raise NotPublished(f"{url} returned HTTP {r.status_code} (file not published?)")
                r.raise_for_status()
                # Content-Length is only the file size when the server didn't compress the response
                expected = None if "Content-Encoding" in r.headers else r.headers.get("Content-Length")
                last_modified = r.headers.get("Last-Modified", "")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        f.write(chunk)
            size = tmp.stat().st_size
            if expected is not None and size != int(expected):
                raise RetrievalError(f"got {size} bytes, expected {expected}")
            tmp.replace(dest)
            break
        except NotPublished:
            tmp.unlink(missing_ok=True)
            raise
        except (requests.RequestException, RetrievalError) as e:
            log.warning("  download attempt %d failed: %s", attempt, e)
            if attempt == config.DOWNLOAD_RETRIES:
                tmp.unlink(missing_ok=True)
                raise RetrievalError(f"giving up on {url} after {attempt} attempts") from e
            time.sleep(config.BACKOFF_SECONDS * 2 ** (attempt - 1))

    entry = {
        "url": url,
        "local_path": str(dest.relative_to(config.ROOT)),
        "bytes": dest.stat().st_size,
        "http_content_length": int(expected) if expected is not None else None,
        "last_modified": last_modified,
        "sha256": sha256_of(dest),
        "retrieved_at": now_utc(),
    }
    manifest["files"][name] = entry
    log.info("  saved %s (%d bytes, sha256 %s...)", name, entry["bytes"], entry["sha256"][:12])
    return entry


def get_trip_file(month, manifest, force=False):
    dest = config.RAW_DIR / "trips" / f"yellow_tripdata_{month}.parquet"
    entry = download_file(config.TRIP_URL.format(month=month), dest, manifest, force, check_remote=True)
    # parquet footer tells us how many rows the publisher wrote; used for the completeness check
    entry["parquet_num_rows"] = pq.ParquetFile(dest).metadata.num_rows
    return dest


def get_zone_file(manifest, force=False):
    dest = config.RAW_DIR / "taxi_zone_lookup.csv"
    download_file(config.ZONE_URL, dest, manifest, force)
    return dest


def month_bounds(month):
    start = pd.Timestamp(month + "-01")
    end = start + pd.offsets.MonthEnd(0)
    return start, end


def expected_hours(month):
    # Open-Meteo returns 24 local-clock hours per day, even on DST change days
    # (notebook 01 checks this for 2026-03-08 and 2025-11-02). So days * 24.
    start, end = month_bounds(month)
    return end.day * 24


def check_weather(payload, month):
    """Return (ok, message) for a weather JSON payload."""
    if "hourly" not in payload:
        return False, f"no hourly block in response: {str(payload)[:200]}"
    hourly = pd.DataFrame(payload["hourly"])
    exp = expected_hours(month)
    nulls = int(hourly[["precipitation", "temperature_2m"]].isna().sum().sum())
    if len(hourly) != exp:
        return False, f"{len(hourly)} hours returned, expected {exp}"
    if nulls:
        return False, f"{nulls} null values in precipitation/temperature"
    return True, f"{len(hourly)} of {exp} hours, no nulls"


def get_weather(month, manifest, force=False):
    """Fetch hourly weather for the month from Open-Meteo. Returns the raw JSON file path.
    Raises RetrievalError if we cannot get a complete month."""
    dest = config.RAW_DIR / "weather" / f"open_meteo_{month}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        ok, msg = check_weather(json.loads(dest.read_text()), month)
        if ok:
            log.info("  skip weather API call, %s already saved (%s)", dest.name, msg)
            return dest
        log.warning("  saved weather file incomplete (%s), calling API again", msg)

    start, end = month_bounds(month)
    params = {
        "latitude": config.WEATHER_LAT, "longitude": config.WEATHER_LON,
        "start_date": start.strftime("%Y-%m-%d"), "end_date": end.strftime("%Y-%m-%d"),
        "hourly": "precipitation,temperature_2m", "timezone": config.TIMEZONE,
    }
    last_error = None
    for attempt in range(1, config.DOWNLOAD_RETRIES + 1):
        try:
            r = requests.get(config.WEATHER_URL, params=params, timeout=60)
            if r.status_code == 400:
                # bad request (e.g. dates not in archive yet), retrying will not help
                raise RetrievalError(f"weather API said: {r.text[:200]}")
            r.raise_for_status()
            payload = r.json()
            ok, msg = check_weather(payload, month)
            if not ok:
                raise RetrievalError(f"weather response incomplete: {msg}")
            dest.write_text(r.text)
            length = None if "Content-Encoding" in r.headers else r.headers.get("Content-Length")
            manifest["files"][dest.name] = {
                "url": r.url, "local_path": str(dest.relative_to(config.ROOT)),
                "bytes": dest.stat().st_size,
                "http_content_length": int(length) if length is not None else None,
                "sha256": sha256_of(dest),
                "hours": len(payload["hourly"]["time"]), "retrieved_at": now_utc(),
            }
            log.info("  saved %s (%s)", dest.name, msg)
            return dest
        except RetrievalError as e:
            last_error = str(e)[:150]  # connection errors are very long
            if "API said" in str(e):
                break
        except (requests.RequestException, ValueError) as e:
            last_error = str(e)[:150]  # connection errors are very long
        log.warning("  weather attempt %d failed: %s", attempt, last_error)
        if attempt < config.DOWNLOAD_RETRIES:
            time.sleep(config.BACKOFF_SECONDS * 2 ** (attempt - 1))
    raise RetrievalError(f"weather for {month} not available: {last_error}")


def load_trips(con, path):
    """Load only the needed columns into a DuckDB table raw_month. Returns (rows_loaded, schema_info)."""
    schema_cols = pq.ParquetFile(path).schema_arrow.names
    missing = [c for c in config.TRIP_COLUMNS if c not in schema_cols]
    extra = [c for c in schema_cols if c not in config.DICTIONARY_COLUMNS]
    if missing:
        return 0, {"missing": missing, "extra": extra}
    cols = ", ".join(config.TRIP_COLUMNS)
    con.execute(f"CREATE OR REPLACE TABLE raw_month AS SELECT {cols} FROM read_parquet('{path}')")
    rows = con.execute("SELECT count(*) FROM raw_month").fetchone()[0]
    return rows, {"missing": missing, "extra": extra}


def load_zones(con, path):
    con.execute(f"""
        CREATE OR REPLACE TABLE dim_zone AS
        SELECT LocationID AS zone_id, Borough AS borough, Zone AS zone_name, service_zone
        FROM read_csv('{path}', header=true)
    """)
    n, jfk = con.execute(
        "SELECT count(*), max(CASE WHEN zone_id = ? THEN zone_name END) FROM dim_zone",
        [config.JFK_ZONE]).fetchone()
    return n, jfk


def load_weather(con, path, month):
    payload = json.loads(path.read_text())
    wx = pd.DataFrame(payload["hourly"])
    wx["hour_ts"] = pd.to_datetime(wx["time"])
    wx["source_month"] = month
    wx = wx[["source_month", "hour_ts", "precipitation", "temperature_2m"]]
    con.execute("""CREATE TABLE IF NOT EXISTS weather_hourly (
        source_month VARCHAR, hour_ts TIMESTAMP, precipitation DOUBLE, temperature_2m DOUBLE)""")
    con.execute("DELETE FROM weather_hourly WHERE source_month = ?", [month])
    con.register("wx_df", wx)
    con.execute("INSERT INTO weather_hourly SELECT * FROM wx_df")
    con.unregister("wx_df")
    return len(wx)
