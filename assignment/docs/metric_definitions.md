# Metric definitions

Population for every metric: trips in `trip_fact`, meaning pickup in zone 132 (JFK Airport), dropoff in any
of the 69 Manhattan zones, RatecodeID = 2, and no exclude rule broken (V01-V06). Times are local New York time.
Code: `pipeline/metrics.py`.

| # | Metric | Definition | Why it's tied to the KPI / decision |
|---|---|---|---|
| 1 | Long-trip rate (project KPI) | trips with duration over 60 min / all trips in the group | The fare is fixed at $70, so time is the only thing that changes how good the run is. Over 60 min means under $70 per hour on the meter. |
| 2 | Median and P90 duration | `median(duration_min)` and `quantile_cont(duration_min, 0.9)` | Median is the typical trip, P90 is "how bad does a bad one get". The mean is not used (see notebook 02). |
| 3 | Effective fare per trip-hour (actual fare / hours on the meter) | `sum(fare_amount) / sum(duration_min / 60)`. Almost every fare is exactly $70, the few odd ones (flag F02) use their real value | Puts the time cost in dollars, which is how the ops manager and drivers think about it. Pooled ratio, so a few short trips can't blow it up. Excludes tips, tolls and surcharges. |
| 4 | Quality exclusion rate (a data-trust metric, not an operations one) | trips excluded by V01-V05 / all JFK -> Manhattan zone-pair trips | Tells the reader how much of the raw data had to be thrown out. It also feeds the gate: over 10% and the month isn't published. |
| 5 | Wet vs dry long-trip rate (n/a for a month whose weather couldn't be fetched) | long-trip rate for pickups in hours with >= 0.5 mm precipitation vs hours with less. Also a same-hour version: the wet minus dry gap inside each day type x pickup hour cell, averaged with wet-trip weights | Checks whether weather would need to be part of the advice. It's an association, not a cause. |

Group levels in the outputs:

- `outputs/metrics_by_month.csv`: per month and "all" (all three months pooled, not an average of months).
- `outputs/metrics_by_hour.csv`: per day type (weekday = Mon-Fri, weekend = Sat-Sun) x pickup hour, all months pooled.

Advice column (in `metrics_by_hour.csv` and the evidence table):

- steer toward: long-trip rate 10% or less
- steer away: long-trip rate 50% or more
- neutral: in between
- too few trips: fewer than 100 trips in that cell, no advice

Why 60 minutes: at $70 a trip, 60 minutes is exactly $70 per hour on the meter, which is an easy line for
drivers to understand. It's also a bit above the overall median (52.7 min), so it separates slow trips from
normal ones. Someone could reasonably pick 50 or 75 minutes instead; it's one setting,
`config.LONG_TRIP_MINUTES`.
