# Workflow and data model

## 1. The business workflow (what happens to one JFK run)

```mermaid
flowchart LR
    A["Dispatcher / driver decides<br/>to go to JFK at hour H<br/>(intervention, not in data)"] --> B["Wait in JFK taxi queue<br/>(not in data)"]
    B --> C["Meter on at JFK<br/>zone 132<br/>tpep_pickup_datetime"]
    C --> D["Drive to Manhattan<br/>traffic, weather"]
    D --> E["Meter off in a Manhattan zone<br/>tpep_dropoff_datetime"]
    E --> F["Payment<br/>$70 flat fare + tolls,<br/>surcharges, tip"]
    F --> G["Outcome: trip minutes,<br/>$ per trip-hour,<br/>over 60 min or not"]
```

Plain-text version:

```
[decide to go to JFK at hour H] -> [queue wait] -> [meter on @ JFK] -> [drive] -> [meter off @ Manhattan] -> [payment]
      intervention (not in data)     (not in data)    pickup event                   dropoff event          fare_amount, tip
                                                        |________________ measurable outcome _________________|
                                                           duration_min, is_long (> 60 min), fare_per_trip_hour
```

States a trip can end up in after validation: `valid` (used for metrics), `quality_excluded` (broke one of
V01-V05), `out of scope` (zone pair is right but rate code is not 2). Flags F01-F03 don't change the state.

## 2. Pipeline flow

```mermaid
flowchart TD
    S1["TLC trip parquet<br/>(HTTPS file)"] --> INGEST
    S2["Taxi zone CSV<br/>(HTTPS file)"] --> INGEST
    S3["Open-Meteo<br/>(JSON API)"] --> INGEST
    INGEST["ingest.py<br/>download with retries, skip if size + sha256 match<br/>and Last-Modified unchanged,<br/>raw files + raw_manifest.json"] --> V
    V["validate.py<br/>file checks S01-S10, trip rules V01-V06 / F01-F03,<br/>row reconciliation R01-R02, gate G01-G02"] -->|month passed| M
    V -->|month failed| X["stop: month not published,<br/>outputs/ left as they were, exit code 1"]
    M["model.py<br/>dim_zone, dim_hour, dim_vendor, trip_fact,<br/>orphan key check"] --> K
    K["metrics.py (DuckDB SQL)<br/>by month, by day type x hour,<br/>wet vs dry"] --> O["outputs/<br/>evidence_table.md, CSVs, charts<br/>(written to outputs/.tmp, then moved)"]
```

## 3. Relational model (in `data/processed/jfk_trips.duckdb`)

```mermaid
erDiagram
    dim_zone ||--o{ trip_fact : "do_zone_id / pu_zone_id"
    dim_hour ||--o{ trip_fact : "pickup_hour_ts"
    dim_vendor ||--o{ trip_fact : "vendor_id"
    trips_validated ||--o| trip_fact : "trip_id (valid rows only)"
    weather_hourly ||--|| dim_hour : "hour_ts"

    trip_fact {
        varchar trip_id PK
        varchar source_month
        int vendor_id FK
        timestamp pickup_ts
        timestamp dropoff_ts
        timestamp pickup_hour_ts FK
        int pu_zone_id FK
        int do_zone_id FK
        double duration_min
        double fare_amount
        boolean is_long
        double fare_per_trip_hour
    }
    dim_hour {
        timestamp hour_ts PK
        int pickup_hour
        varchar day_type
        double precipitation_mm
        boolean is_wet
    }
    dim_zone {
        int zone_id PK
        varchar borough
        varchar zone_name
    }
    dim_vendor {
        int vendor_id PK
        varchar vendor_name
    }
    trips_validated {
        varchar trip_id PK
        boolean v01_to_v06_f01_to_f03
        boolean quality_excluded
        boolean scope_excluded
        boolean is_valid
    }
```

Two views sit on top: `trip_events` shows each trip as two events (meter_on at `pu_zone_id`, meter_off at
`do_zone_id`), and `trip_analysis` joins `trip_fact` to `dim_hour`, `dim_zone` and `dim_vendor`. The metrics
are computed from `trip_analysis`. After building a month the pipeline checks that every `trip_fact` row finds
its hour and both of its zones.
