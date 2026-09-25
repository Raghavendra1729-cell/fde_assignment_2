# Source map

The client is a (hypothetical) yellow-cab fleet. The ops manager's question is: at which hours is it worth
sending our drivers into the JFK taxi queue for a Manhattan fare? The data below is real public data.
Where the answer would need the fleet's own systems, I say so in the "gaps" column.

## Business question -> information -> source

| # | Business question | Information needed | Source system | Owner | Grain | Important gaps |
|---|---|---|---|---|---|---|
| 1 | How long does a JFK -> Manhattan trip take, by pickup hour and day? | pickup and dropoff time, pickup and dropoff zone | TLC yellow trip records (monthly parquet) | Published by NYC TLC; records are created by the TPEP vendors (CMT, Curb, Myle, Helix) in the cab's meter/tablet | one row per trip (meter on to meter off) | VendorID 7 (Helix) sends dropoff = pickup on every trip; zone level only, no address; about 2 month publish delay; files can be re-published |
| 2 | Which trips are really "JFK -> Manhattan"? | zone id to zone name and borough | TLC taxi zone lookup (CSV) | NYC TLC | one row per taxi zone (265) | zone 264 "Unknown" and 265 "Outside of NYC" can't be placed |
| 3 | Is the trip on the flat fare? | RatecodeID, fare_amount | TLC yellow trip records | same as 1; the data dictionary defines RatecodeID as the final rate code in effect | per trip | about 27% of rate code 2 records are not JFK -> Manhattan zone pairs, so rate code alone cannot define the route; Flex Fare trips have no rate code at all |
| 4 | What does the $70 work out to per hour for the driver? | fare, tip, trip duration | TLC yellow trip records | same as 1 | per trip | cash tips not recorded; queue wait not recorded; the fleet's lease / pay split is not in public data |
| 5 | Does bad weather go with slower trips? | hourly precipitation near JFK | Open-Meteo historical weather API (JSON) | Open-Meteo (model reanalysis data, not a station reading) | one row per hour per grid cell | one point at JFK only, nothing for the route or Manhattan; a model estimate |
| 6 | How long do drivers wait in the JFK queue before the pickup? | queue entry time, queue length by hour | JFK taxi dispatch / holding lot system (Port Authority), or the fleet's own driver app | Port Authority / the client | per cab per queue visit | **not available to me at all**. This is the biggest gap, see README |
| 7 | Which of these trips were driven by the client's own cabs? | medallion or driver id | the fleet's dispatch system | the client | per shift / per trip | public TLC data has no medallion or driver id, so I use all yellow cabs as a proxy |

## Source overview

| Source | Retrieval mode | What I pull | Refresh | How I know it's complete |
|---|---|---|---|---|
| TLC yellow trip parquet, `https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_YYYY-MM.parquet` | file over HTTPS (then queried with DuckDB SQL) | 3 files: 2026-04, 2026-05, 2026-06, 13 of 20-21 columns | monthly, about 2 months behind | size on disk = HTTP Content-Length, sha256 in `data/raw_manifest.json`, rows read = rows in parquet footer, every day of the month present, row count within 15% of the month before |
| TLC taxi zone lookup, `https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv` | file over HTTPS | 265 zones | rarely (Last-Modified is Feb 2024) | 265 rows, zone 132 = "JFK Airport", sha256 in manifest (the server gzips it, so there's no usable Content-Length) |
| Open-Meteo archive, `https://archive-api.open-meteo.com/v1/archive` | JSON API | hourly `precipitation`, `temperature_2m` at 40.64, -73.78, local NY time | daily, a few days behind | one row per hour (days x 24), no nulls, raw JSON saved per month |
| [TLC yellow data dictionary](https://www.nyc.gov/assets/tlc/downloads/pdf/data_dictionary_trip_records_yellow.pdf) (March 2025) | read by hand | meaning of VendorID, RatecodeID, payment_type | when TLC updates it | - |

The $70 JFK-Manhattan flat fare and applicable additions are checked against the official
[NYC TLC taxi fare page](https://www.nyc.gov/site/tlc/passengers/taxi-fare.page).

## Grain and join keys

- Trip file: one row per trip. There is no trip id in the public data, so the pipeline makes one
  (`YYYY-MM-nnnnnn`, numbered by pickup time and a few other columns so it comes out the same on every run).
- Trips join to zones on `PULocationID` / `DOLocationID` = `LocationID`.
- Trips join to weather on the pickup hour: `date_trunc('hour', tpep_pickup_datetime)` = weather `time`.
  Both are local New York time (TLC timestamps are local and have no timezone, Open-Meteo was asked for
  `timezone=America/New_York`).

## Things I found out about the sources along the way

- The June 2026 file has an extra column, `request_source`, that is not in the data dictionary. April and May
  don't have it. I only read the columns I need, so it can't break anything, and check S01 reports it.
- 21-26% of rows per month have no RatecodeID and no passenger_count. All of them are payment_type 0, "Flex Fare".
- VendorID 7 (Helix): 100% of its trips have dropoff time = pickup time, in all three months. 150,538 trips in the
  whole three-month data, 2,148 of them JFK -> Manhattan.
- Open-Meteo returns 24 rows for DST change days too (checked 2026-03-08 and 2025-11-02). Not an issue for
  April to June, but it matters if someone runs this for March or November.
- The weather JSON has a `generationtime_ms` field, so fetching the same month twice gives a different sha256.
  That's one reason the pipeline doesn't re-fetch weather it already has.
