import duckdb
import pandas as pd
import pytest

from pipeline import config, ingest, metrics, run, validate

MONTH = "2026-04"


def trip(**changes):
    # a normal JFK -> Manhattan flat-fare trip; each test changes one thing
    row = {
        "VendorID": 2, "tpep_pickup_datetime": pd.Timestamp("2026-04-10 14:00:00"),
        "tpep_dropoff_datetime": pd.Timestamp("2026-04-10 14:55:00"), "passenger_count": 1,
        "trip_distance": 18.0, "RatecodeID": 2, "PULocationID": 132, "DOLocationID": 161,
        "payment_type": 1, "fare_amount": 70.0, "tip_amount": 10.0, "tolls_amount": 6.94,
        "total_amount": 95.0,
    }
    row.update(changes)
    return row


@pytest.fixture
def con():
    c = duckdb.connect()
    zones = pd.DataFrame({"zone_id": [132, 161, 138], "borough": ["Queens", "Manhattan", "Queens"],
                          "zone_name": ["JFK Airport", "Midtown Center", "LaGuardia Airport"],
                          "service_zone": ["Airports", "Yellow Zone", "Airports"]})
    c.register("zones_df", zones)
    c.execute("CREATE TABLE dim_zone AS SELECT * FROM zones_df")
    return c


def load(con, rows):
    df = pd.DataFrame(rows)
    df["RatecodeID"] = df["RatecodeID"].astype("Int64")
    con.register("raw_df", df)
    con.execute("CREATE OR REPLACE TABLE raw_month AS SELECT * FROM raw_df")
    return len(df)


def test_each_rule_catches_its_case(con):
    rows = [
        trip(),                                                             # valid
        trip(tpep_pickup_datetime=pd.Timestamp("2026-03-31 23:50:00"),
             tpep_dropoff_datetime=pd.Timestamp("2026-04-01 00:40:00")),    # V01
        trip(tpep_dropoff_datetime=pd.Timestamp("2026-04-10 14:00:00")),    # V02 (Helix-style)
        trip(tpep_dropoff_datetime=pd.Timestamp("2026-04-10 14:05:00")),    # V03
        trip(tpep_dropoff_datetime=pd.Timestamp("2026-04-10 19:00:00")),    # V04
        trip(fare_amount=-70.0),                                            # V05
        trip(RatecodeID=1, fare_amount=80.0),                               # V06
        trip(RatecodeID=None, payment_type=0),                              # V06 (flex fare, null code)
        trip(trip_distance=0.0),                                            # F01 only, stays valid
        trip(DOLocationID=138),                                             # not a candidate at all
    ]
    n = load(con, rows)
    assert validate.apply_trip_rules(con, MONTH) == 9
    hits = {r["rule_id"]: r["rows_hit"] for r in validate.rule_report(con, MONTH)}
    assert hits == {"V01": 1, "V02": 1, "V03": 1, "V04": 1, "V05": 1, "V06": 2,
                    "F01": 1, "F02": 0, "F03": 0}
    checks, counts = validate.reconcile(con, MONTH, n)
    assert counts == {"candidates": 9, "valid": 2, "quality_excluded": 5, "scope_excluded": 2}
    assert all(c["status"] == "PASS" for c in checks)


def test_flag_rules_do_not_exclude(con):
    load(con, [trip(trip_distance=0.0), trip(passenger_count=0), trip(fare_amount=73.5)])
    validate.apply_trip_rules(con, MONTH)
    assert con.execute("SELECT count(*) FROM month_candidates WHERE is_valid").fetchone()[0] == 3


def test_gate_fails_when_too_many_trips_are_bad(con):
    rows = [trip()] * 8 + [trip(fare_amount=-70.0)] * 2   # 20% quality exclusions
    n = load(con, rows)
    validate.apply_trip_rules(con, MONTH)
    checks, counts = validate.reconcile(con, MONTH, n)
    passed, gate_checks = validate.gate(MONTH, checks, counts)
    assert not passed
    assert [c["status"] for c in gate_checks] == ["FAIL", "FAIL"]   # rate too high and < 1000 trips


def test_weather_completeness():
    hours = pd.date_range("2026-04-01", periods=720, freq="h").strftime("%Y-%m-%dT%H:%M").tolist()
    good = {"hourly": {"time": hours, "precipitation": [0.0] * 720, "temperature_2m": [15.0] * 720}}
    assert ingest.check_weather(good, MONTH)[0]
    short = {"hourly": {k: v[:-24] for k, v in good["hourly"].items()}}
    assert not ingest.check_weather(short, MONTH)[0]
    holes = {"hourly": dict(good["hourly"], precipitation=[None] + [0.0] * 719)}
    assert not ingest.check_weather(holes, MONTH)[0]
    assert not ingest.check_weather({"error": True, "reason": "bad dates"}, MONTH)[0]


def test_expected_hours():
    assert ingest.expected_hours("2026-04") == 720
    assert ingest.expected_hours("2026-02") == 672
    assert ingest.expected_hours("2026-03") == 744   # DST month, Open-Meteo still gives 24 rows a day


def test_download_is_skipped_when_file_matches(tmp_path, monkeypatch):
    f = tmp_path / "x.parquet"
    f.write_bytes(b"hello")
    manifest = {"files": {"x.parquet": {"bytes": 5, "sha256": ingest.sha256_of(f)}}}

    def no_download(*a, **k):
        raise AssertionError("should not download")
    monkeypatch.setattr(ingest.requests, "get", no_download)
    assert ingest.download_file("http://x", f, manifest) == manifest["files"]["x.parquet"]


class FakeResponse:
    def __init__(self, status):
        self.status_code = status
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_missing_month_raises_without_retrying(tmp_path, monkeypatch):
    calls = []

    def forbidden(*a, **k):
        calls.append(1)
        return FakeResponse(403)
    monkeypatch.setattr(ingest.requests, "get", forbidden)
    with pytest.raises(ingest.RetrievalError):
        ingest.download_file("http://x", tmp_path / "y.parquet", {"files": {}})
    assert len(calls) == 1
    assert not (tmp_path / "y.parquet").exists()


def test_advice_rule():
    assert metrics.steer({"trips": 5000, "long_trip_rate": 0.05}) == "steer toward"
    assert metrics.steer({"trips": 5000, "long_trip_rate": 0.30}) == "neutral"
    assert metrics.steer({"trips": 5000, "long_trip_rate": 0.75}) == "steer away"
    assert metrics.steer({"trips": 40, "long_trip_rate": 0.0}) == "too few trips"


def test_weather_api_down_raises_after_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    monkeypatch.setattr(config, "BACKOFF_SECONDS", 0)
    calls = []

    def down(*a, **k):
        calls.append(1)
        raise ingest.requests.ConnectionError("no network")
    monkeypatch.setattr(ingest.requests, "get", down)
    with pytest.raises(ingest.RetrievalError):
        ingest.get_weather(MONTH, {"files": {}})
    assert len(calls) == config.DOWNLOAD_RETRIES


def fake_month_files(folder):
    """A tiny April 2026 trip file (40 JFK -> Manhattan trips a day) and a 3-zone lookup."""
    rows = []
    for day in range(1, 31):
        for i in range(40):
            start = pd.Timestamp(f"2026-04-{day:02d}") + pd.Timedelta(minutes=30 * i)
            rows.append(trip(tpep_pickup_datetime=start,
                             tpep_dropoff_datetime=start + pd.Timedelta(minutes=40 + i)))
    df = pd.DataFrame(rows)
    df["passenger_count"] = df["passenger_count"].astype("int64")
    trips = folder / "yellow_tripdata_2026-04.parquet"
    df.to_parquet(trips)
    zones = folder / "taxi_zone_lookup.csv"
    zones.write_text('"LocationID","Borough","Zone","service_zone"\n'
                     '132,"Queens","JFK Airport","Airports"\n'
                     '138,"Queens","LaGuardia Airport","Airports"\n'
                     '161,"Manhattan","Midtown Center","Yellow Zone"\n')
    return trips, zones


def test_weather_api_down_still_publishes_with_na(tmp_path, monkeypatch):
    trips, zones = fake_month_files(tmp_path)
    for name, path in [("OUTPUT_DIR", tmp_path / "out"), ("LOG_DIR", tmp_path / "logs"),
                       ("PROCESSED_DIR", tmp_path / "proc"), ("DB_PATH", tmp_path / "proc" / "t.duckdb"),
                       ("MANIFEST_PATH", tmp_path / "manifest.json")]:
        monkeypatch.setattr(config, name, path)

    def trip_file(month, manifest, force=False):
        manifest["files"][trips.name] = {"parquet_num_rows": 1200}
        return trips

    def weather_down(month, manifest, force=False):
        raise ingest.RetrievalError("weather for 2026-04 not available: simulated outage")

    monkeypatch.setattr(ingest, "get_trip_file", trip_file)
    monkeypatch.setattr(ingest, "get_zone_file", lambda manifest, force=False: zones)
    monkeypatch.setattr(ingest, "get_weather", weather_down)

    assert run.main(["--months", "2026-04"]) == 0
    evidence = (tmp_path / "out" / "evidence_table.md").read_text()
    assert "n/a" in evidence
    monthly = pd.read_csv(tmp_path / "out" / "metrics_by_month.csv")
    assert monthly.loc[monthly.source_month == "2026-04", "valid"].iloc[0] == 1200
    assert pd.isna(monthly.loc[monthly.source_month == "2026-04", "wet_trips"].iloc[0])
    assert not (tmp_path / "out" / ".tmp").exists()


class FakeHead:
    def __init__(self, last_modified):
        self.status_code = 200
        self.headers = {"Last-Modified": last_modified}


def test_republished_month_is_downloaded_again(tmp_path, monkeypatch):
    f = tmp_path / "x.parquet"
    f.write_bytes(b"hello")
    entry = {"bytes": 5, "sha256": ingest.sha256_of(f), "last_modified": "Fri, 05 Jun 2026 20:44:34 GMT"}
    gets = []

    def get(*a, **k):
        gets.append(1)
        raise ingest.requests.ConnectionError("stop here")
    monkeypatch.setattr(ingest.requests, "get", get)
    monkeypatch.setattr(config, "BACKOFF_SECONDS", 0)

    # same Last-Modified: skip
    monkeypatch.setattr(ingest.requests, "head", lambda *a, **k: FakeHead(entry["last_modified"]))
    assert ingest.download_file("http://x", f, {"files": {"x.parquet": entry}}, check_remote=True) == entry
    assert gets == []

    # server can't be reached: keep the local copy
    def offline(*a, **k):
        raise ingest.requests.ConnectionError("offline")
    monkeypatch.setattr(ingest.requests, "head", offline)
    assert ingest.download_file("http://x", f, {"files": {"x.parquet": entry}}, check_remote=True) == entry
    assert gets == []

    # newer Last-Modified: it tries to download again
    monkeypatch.setattr(ingest.requests, "head", lambda *a, **k: FakeHead("Thu, 17 Sep 2026 18:34:43 GMT"))
    with pytest.raises(ingest.RetrievalError):
        ingest.download_file("http://x", f, {"files": {"x.parquet": entry}}, check_remote=True)
    assert len(gets) == config.DOWNLOAD_RETRIES
    assert f.read_bytes() == b"hello"   # failed re-download didn't touch the old file
