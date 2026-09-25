# Stage 4: metrics from the model, plus the published outputs (CSVs, evidence table, charts).
import logging
import shutil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from pipeline import config

log = logging.getLogger("pipeline")

MONTH_NAMES = {"01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May", "06": "Jun",
               "07": "Jul", "08": "Aug", "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec"}


def in_list(months):
    return "(" + ", ".join(f"'{m}'" for m in months) + ")"


def core_metrics(con, months, group_cols):
    """KPI + duration + $/hour for valid trips, grouped by group_cols (can be empty)."""
    select_groups = (", ".join(group_cols) + ",") if group_cols else ""
    group_by = ("GROUP BY " + ", ".join(group_cols) + " ORDER BY " + ", ".join(group_cols)) if group_cols else ""
    return con.execute(f"""
        SELECT {select_groups}
               count(*) AS trips,
               round(avg(CASE WHEN is_long THEN 1 ELSE 0 END), 4) AS long_trip_rate,
               round(median(duration_min), 1) AS median_min,
               round(quantile_cont(duration_min, 0.9), 1) AS p90_min,
               round(sum(fare_amount) / sum(duration_min / 60.0), 2) AS fare_per_trip_hour
        FROM trip_analysis
        WHERE source_month IN {in_list(months)}
        {group_by}
    """).df()


def rain_metrics(con, months, by_month):
    """Wet vs dry long-trip rate. The raw gap is mixed up with hour of day (rain is not spread evenly
    over hours), so I also compare wet and dry trips inside the same day type + pickup hour cell and
    weight the differences by wet trips."""
    g = "source_month," if by_month else ""
    gm = "GROUP BY source_month" if by_month else ""
    raw = con.execute(f"""
        SELECT {g}
               -- is_wet is NULL for a month whose weather could not be fetched; that month keeps its row
               -- here and ends up as n/a instead of disappearing
               CASE WHEN count(is_wet) = 0 THEN NULL ELSE count(*) FILTER (WHERE is_wet) END AS wet_trips,
               round(avg(CASE WHEN is_long THEN 1 ELSE 0 END) FILTER (WHERE is_wet), 4) AS long_rate_wet,
               round(avg(CASE WHEN is_long THEN 1 ELSE 0 END) FILTER (WHERE NOT is_wet), 4) AS long_rate_dry
        FROM trip_analysis
        WHERE source_month IN {in_list(months)}
        {gm}
    """).df()
    matched = con.execute(f"""
        WITH cell AS (
            SELECT {g} day_type, pickup_hour, is_wet, count(*) n,
                   avg(CASE WHEN is_long THEN 1 ELSE 0 END) lr
            FROM trip_analysis
            WHERE source_month IN {in_list(months)} AND is_wet IS NOT NULL
            GROUP BY ALL
        )
        SELECT {'w.source_month,' if by_month else ''}
               round(sum(w.n * (w.lr - d.lr)) / sum(w.n), 4) AS wet_minus_dry_same_hour,
               sum(w.n) AS wet_trips_matched
        FROM cell w JOIN cell d
          ON w.day_type = d.day_type AND w.pickup_hour = d.pickup_hour
             {'AND w.source_month = d.source_month' if by_month else ''}
        WHERE w.is_wet AND NOT d.is_wet
        {'GROUP BY w.source_month' if by_month else ''}
    """).df()
    if by_month:
        return raw.merge(matched, on="source_month", how="left")
    return pd.concat([raw, matched], axis=1)


def monthly_table(con, months, counts):
    m = core_metrics(con, months, ["source_month"])
    rain = rain_metrics(con, months, by_month=True)
    m = m.merge(rain, on="source_month", how="left")
    allm = core_metrics(con, months, [])
    allm.insert(0, "source_month", "all")
    rain_all = rain_metrics(con, months, by_month=False)
    for col in ["wet_trips", "long_rate_wet", "long_rate_dry", "wet_minus_dry_same_hour", "wet_trips_matched"]:
        allm[col] = rain_all[col].iloc[0] if len(rain_all) else None
    m = pd.concat([m, allm], ignore_index=True)

    c = pd.DataFrame([{"source_month": k, **v} for k, v in counts.items()])
    tot = c.drop(columns="source_month").sum()
    c = pd.concat([c, pd.DataFrame([{"source_month": "all", **tot.to_dict()}])], ignore_index=True)
    c["quality_exclusion_rate"] = (c["quality_excluded"] / c["candidates"]).round(4)
    c["scope_exclusion_rate"] = (c["scope_excluded"] / c["candidates"]).round(4)
    out = c.merge(m, on="source_month", how="left")
    cols = ["source_month", "candidates", "quality_excluded", "scope_excluded", "valid",
            "quality_exclusion_rate", "scope_exclusion_rate", "trips", "long_trip_rate", "median_min",
            "p90_min", "fare_per_trip_hour", "wet_trips", "long_rate_wet", "long_rate_dry",
            "wet_minus_dry_same_hour", "wet_trips_matched"]
    return out[cols]


def steer(row):
    if row["trips"] < config.MIN_TRIPS_FOR_ADVICE:
        return "too few trips"
    if row["long_trip_rate"] <= config.STEER_TOWARD_MAX_LONG_RATE:
        return "steer toward"
    if row["long_trip_rate"] >= config.STEER_AWAY_MIN_LONG_RATE:
        return "steer away"
    return "neutral"


def hourly_table(con, months):
    h = core_metrics(con, months, ["day_type", "pickup_hour"])
    h["advice"] = h.apply(steer, axis=1)
    return h


def pct(x):
    return "n/a" if pd.isna(x) else f"{x * 100:.1f}%"


def count(x):
    return "n/a" if pd.isna(x) else f"{int(x):,}"


def rain_note(monthly):
    # the "All months" rain numbers only pool months that have weather, say so if one is missing
    per_month = monthly[monthly.source_month != "all"]
    missing = list(per_month.loc[per_month.wet_trips.isna(), "source_month"])
    if not missing or len(missing) == len(per_month):
        return ""
    have = [m for m in per_month.source_month if m not in missing]
    return (f"Rows 5 and 5b, All months = months with weather only ({', '.join(have)}); "
            f"no weather for {', '.join(missing)}.\n\n")


def month_label(m):
    return "All months" if m == "all" else f"{MONTH_NAMES[m[5:]]} {m[:4]}"


def evidence_markdown(monthly, hourly, months):
    cols = list(monthly["source_month"])
    head = "| # | Metric | " + " | ".join(month_label(m) for m in cols) + " |"
    sep = "|---|---|" + "---|" * len(cols)
    r = monthly.set_index("source_month")

    def row(num, name, fn):
        return f"| {num} | {name} | " + " | ".join(fn(r.loc[m]) for m in cols) + " |"

    lines = [
        "# Evidence table: JFK -> Manhattan flat-fare runs",
        "",
        f"Months: {', '.join(months)}. Population: yellow taxi trips with pickup in zone 132 (JFK) and dropoff "
        "in a Manhattan zone, RatecodeID 2, that passed every exclude rule. Generated by `python -m pipeline.run`.",
        "",
        head, sep,
        row(1, f"Long-trip rate, share of trips over {config.LONG_TRIP_MINUTES} min (project KPI)",
            lambda x: pct(x["long_trip_rate"])),
        row(2, "Median / P90 trip duration (min)", lambda x: f"{x['median_min']:.1f} / {x['p90_min']:.1f}"),
        row(3, "Effective fare per trip-hour (actual fare / hours on the meter)",
            lambda x: f"${x['fare_per_trip_hour']:.2f}"),
        row(4, "Data trust: quality exclusion rate (rules V01-V05, of all JFK -> Manhattan trips)",
            lambda x: pct(x["quality_exclusion_rate"])),
        row(5, "Long-trip rate in wet vs dry pickup hours (raw)",
            lambda x: f"{pct(x['long_rate_wet'])} vs {pct(x['long_rate_dry'])}"),
        row("5b", "Wet minus dry, compared within the same day type and pickup hour",
            lambda x: "n/a" if pd.isna(x["wet_minus_dry_same_hour"]) else
            f"{x['wet_minus_dry_same_hour'] * 100:+.1f} pts"),
        "",
        rain_note(monthly) + "Trip counts behind the table:",
        "",
        "| | " + " | ".join(month_label(m) for m in cols) + " |",
        "|---|" + "---|" * len(cols),
        "| JFK -> Manhattan trips (zone pair) | " + " | ".join(f"{int(r.loc[m, 'candidates']):,}" for m in cols) + " |",
        "| excluded by quality rules | " + " | ".join(f"{int(r.loc[m, 'quality_excluded']):,}" for m in cols) + " |",
        "| excluded as out of scope (not rate code 2) | " + " | ".join(f"{int(r.loc[m, 'scope_excluded']):,}" for m in cols) + " |",
        "| valid trips used for metrics | " + " | ".join(f"{int(r.loc[m, 'valid']):,}" for m in cols) + " |",
        "| valid trips in wet hours | " + " | ".join(count(r.loc[m, "wet_trips"]) for m in cols) + " |",
        "",
        "## By pickup hour (all months together)",
        "",
        f"Advice rule: steer toward if long-trip rate <= {config.STEER_TOWARD_MAX_LONG_RATE:.0%}, steer away if "
        f">= {config.STEER_AWAY_MIN_LONG_RATE:.0%}, neutral in between, and no advice under "
        f"{config.MIN_TRIPS_FOR_ADVICE} trips.",
        "",
        "| Pickup hour | Weekday trips | Weekday long-trip rate | Weekday median / P90 min | Weekday $/trip-hour | Weekday advice "
        "| Weekend trips | Weekend long-trip rate | Weekend median / P90 min | Weekend $/trip-hour | Weekend advice |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for hr in range(24):
        cells = [f"{hr:02d}:00"]
        for dt in ["weekday", "weekend"]:
            x = hourly[(hourly.day_type == dt) & (hourly.pickup_hour == hr)]
            if x.empty:
                cells += ["0", "n/a", "n/a", "n/a", "no trips"]
                continue
            x = x.iloc[0]
            cells += [f"{int(x.trips):,}", pct(x.long_trip_rate), f"{x.median_min:.1f} / {x.p90_min:.1f}",
                      f"${x.fare_per_trip_hour:.2f}", x.advice]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def hour_axis(ax):
    ax.set_xticks(range(0, 24, 3))
    ax.set_xlabel("pickup hour at JFK")
    ax.grid(axis="y", alpha=0.3)


def charts(hourly, months, folder):
    span = f"{months[0]} to {months[-1]}"
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=120)
    for dt in ["weekday", "weekend"]:
        d = hourly[hourly.day_type == dt].sort_values("pickup_hour")
        ax.plot(d.pickup_hour, d.long_trip_rate * 100, marker="o", markersize=4, label=dt)
    ax.axhline(config.STEER_AWAY_MIN_LONG_RATE * 100, color="gray", linestyle=":")
    ax.text(0, config.STEER_AWAY_MIN_LONG_RATE * 100 + 1.5, "steer-away line (50%)", fontsize=8, color="gray")
    hour_axis(ax)
    ax.set_ylabel(f"% of trips over {config.LONG_TRIP_MINUTES} min")
    ax.set_ylim(0, 100)
    ax.set_title(f"JFK -> Manhattan flat-fare trips: long-trip rate by pickup hour ({span})", fontsize=10)
    ax.legend()
    fig.tight_layout()
    fig.savefig(folder / "chart_long_trip_rate_by_hour.png")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(9, 3.8), dpi=120, sharey=True)
    for ax, dt, color in [(axes[0], "weekday", "C0"), (axes[1], "weekend", "C1")]:
        d = hourly[hourly.day_type == dt].sort_values("pickup_hour")
        ax.plot(d.pickup_hour, d.median_min, color=color, label="median")
        ax.plot(d.pickup_hour, d.p90_min, color=color, linestyle="--", label="P90")
        ax.axhline(config.LONG_TRIP_MINUTES, color="gray", linestyle=":")
        hour_axis(ax)
        ax.set_title(dt, fontsize=10)
        ax.legend(loc="upper left", fontsize=8)
    axes[0].set_ylabel("trip duration (min)")
    fig.suptitle(f"Median and P90 duration by pickup hour, dotted line = {config.LONG_TRIP_MINUTES} min ({span})",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(folder / "chart_duration_by_hour.png")
    plt.close(fig)


def publish(con, months, counts, rule_rows, check_rows):
    # Build every output in outputs/.tmp first. Validation or generation failures therefore
    # leave the published files alone; final replacements happen only after all files exist.
    tmp = config.OUTPUT_DIR / ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    monthly = monthly_table(con, months, counts)
    hourly = hourly_table(con, months)
    monthly.to_csv(tmp / "metrics_by_month.csv", index=False)
    hourly.to_csv(tmp / "metrics_by_hour.csv", index=False)
    pd.DataFrame(rule_rows).to_csv(tmp / "validation_report.csv", index=False)
    pd.DataFrame(check_rows).to_csv(tmp / "pipeline_checks.csv", index=False)
    (tmp / "evidence_table.md").write_text(evidence_markdown(monthly, hourly, months))
    charts(hourly, months, tmp)
    for f in tmp.iterdir():
        f.replace(config.OUTPUT_DIR / f.name)
    tmp.rmdir()
    allm = monthly[monthly.source_month == "all"].iloc[0]
    log.info("  KPI (all months): long-trip rate %s, median %.1f min, P90 %.1f min, $%.2f per trip-hour",
             pct(allm.long_trip_rate), allm.median_min, allm.p90_min, allm.fare_per_trip_hour)
    return monthly, hourly
