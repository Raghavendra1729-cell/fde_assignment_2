# Validation rules

Code: `pipeline/validate.py`. Thresholds: `pipeline/config.py`. Counts per run: `outputs/validation_report.csv`
(trip rules) and `outputs/pipeline_checks.csv` (file checks, reconciliation, gate).

Every JFK -> Manhattan zone-pair trip is stored in `trips_validated` with one true/false column per rule,
so any exclusion can be traced back to the raw row.

## Trip rules (on the 212,688 JFK -> Manhattan zone-pair trips, Apr-Jun 2026)

| Rule | What it checks | Business reason | Severity | Action | Apr | May | Jun | Total |
|---|---|---|---|---|---|---|---|---|
| V01 | pickup outside the file's month | would leak into another month's KPI | high | exclude | 0 | 0 | 1 | 1 |
| V02 | dropoff at or before pickup | no trip time, and trip time is the whole question. All of these are VendorID 7 | high | exclude | 594 | 773 | 781 | 2,148 |
| V03 | duration under 10 min | 13+ miles in under 10 min is not a real run | high | exclude | 13 | 9 | 14 | 36 |
| V04 | duration over 180 min | meter left running; would inflate P90 and long-trip rate | medium | exclude | 50 | 38 | 35 | 123 |
| V05 | fare zero or negative | refund, void or dispute reversal, not a paid run | medium | exclude | 492 | 494 | 449 | 1,435 |
| V06 | rate code is not 2 (JFK flat fare) | metered, negotiated, flex fare or missing; the flat-fare math doesn't apply | scope | exclude, reported as out of scope | 3,311 | 3,621 | 3,246 | 10,178 |
| F01 | distance under 5 miles | distance meter can fail on a real trip; time and fare look normal | low | flag, keep | 85 | 118 | 109 | 312 |
| F02 | rate code 2 but fare not exactly $70 | odd, but the trip looks real; actual fare is used in $/hour | low | flag, keep | 11 | 12 | 19 | 42 |
| F03 | passenger_count = 0 | driver-entered, not used by any metric | low | flag, keep | 103 | 123 | 85 | 311 |

Overlap, counted in notebook 02 from `trips_validated`: no trip breaks two of the quality rules V01-V05 at the
same time, so the 3,743 quality exclusions are just the sum of V01-V05. 389 of them also break V06. That's why
V06 shows 10,178 hits but only 9,789 trips are excluded for scope alone. 199,156 trips are valid.

## File checks (on the whole monthly file, about 4 million rows)

| Check | What | Level | Result Apr / May / Jun |
|---|---|---|---|
| S01 | required columns present; columns not in the data dictionary are reported | FAIL | pass; June has extra `request_source` |
| S02 | rows read = rows in the parquet footer | FAIL | 3,831,240 / 4,090,836 / 3,837,248, all match |
| S03 | at least 99.9% of pickups inside the file's month | FAIL | pass (12 / 14 / 17 rows outside) |
| S04 | every day of the month has pickups | FAIL | pass |
| S05 | vendor where more than 50% of trips have dropoff <= pickup | WARN | VendorID 7, 100% every month |
| S06 | share of rows with null RatecodeID | INFO | 20.9% / 23.4% / 26.4%, all Flex Fare |
| S07 | rate code 2 trips that are not JFK -> Manhattan | INFO | 24,310 / 26,727 / 25,176 |
| S08 | JFK pickups dropping in zone 264 / 265 | INFO | 7,251 / 6,534 / 6,843 |
| S09 | weather hours complete | WARN | 720 / 744 / 720, complete |
| S10 | row count within 15% of the previous month's file | WARN | n/a for April (March not downloaded), +6.8% May, -6.2% June |

## Reconciliation and gate

| Check | What | Level |
|---|---|---|
| R01 | raw rows = zone-pair candidates + all other rows | FAIL |
| R02 | candidates = valid + quality-excluded + out-of-scope | FAIL |
| G01 | quality exclusion rate <= 10% | FAIL (actual 1.6-1.8%) |
| G02 | at least 1,000 valid trips | FAIL (actual 65,594-67,271) |
| model | every trip_fact row finds its dim_hour and dim_zone row | stops the run |

If any FAIL-level check trips, that month is not published and `outputs/` is left as it was.
WARN means it's logged and shown in the checks file, but the month still publishes. If the weather API fails
(after 3 tries), S09 is a WARN, that month's weather columns stay empty, and the wet/dry rows of the evidence
table show n/a. The duration metrics do not need weather, so they still publish. This failure case is recorded in
`docs/run_logs/run5_weather_api_down.log` and covered by `tests/test_validation.py`. All output files
are built in `outputs/.tmp/` first and moved into `outputs/` only after generation succeeds, so validation and
generation failures leave the published files unchanged.

## Decisions for ambiguous data

- VendorID 7 durations are not imputed. The trips have normal fares and distances, but no usable duration.
- Null-rate-code trips at $70 remain outside the flat-fare population because they are Flex Fare trips.
- Zone 264 "Unknown" dropoffs are not classified as Manhattan.
- The thresholds (10 min, 180 min, 5 miles, 60 min) are documented judgement calls stored in configuration.
