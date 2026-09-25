# All the settings and business thresholds live here so they are easy to find and change.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
OUTPUT_DIR = ROOT / "outputs"
LOG_DIR = ROOT / "logs"
MANIFEST_PATH = ROOT / "data" / "raw_manifest.json"
DB_PATH = PROCESSED_DIR / "jfk_trips.duckdb"

TRIP_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_{month}.parquet"
ZONE_URL = "https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv"
WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"

# JFK weather point (Open-Meteo snaps this to its nearest grid cell)
WEATHER_LAT = 40.64
WEATHER_LON = -73.78
TIMEZONE = "America/New_York"

# Columns we actually need from the trip files. Reading only these keeps memory low.
TRIP_COLUMNS = [
    "VendorID", "tpep_pickup_datetime", "tpep_dropoff_datetime", "passenger_count",
    "trip_distance", "RatecodeID", "PULocationID", "DOLocationID", "payment_type",
    "fare_amount", "tip_amount", "tolls_amount", "total_amount",
]

# All columns in the TLC yellow data dictionary (March 2025). A column outside this list is schema drift.
DICTIONARY_COLUMNS = TRIP_COLUMNS + [
    "store_and_fwd_flag", "extra", "mta_tax", "improvement_surcharge", "congestion_surcharge",
    "Airport_fee", "cbd_congestion_fee",
]

JFK_ZONE = 132
FLAT_FARE_RATECODE = 2          # RatecodeID 2 = final rate code for the JFK flat fare
FLAT_FARE_DOLLARS = 70          # JFK <-> Manhattan flat fare before surcharges, tolls and tip
LONG_TRIP_MINUTES = 60          # project KPI threshold
MIN_PLAUSIBLE_MINUTES = 10      # JFK to Manhattan is 13+ miles, under 10 min is not real
MAX_PLAUSIBLE_MINUTES = 180     # above 3 hours the meter was most likely left running
SHORT_DISTANCE_MILES = 5        # flag only, the distance meter can fail on a real trip
WET_HOUR_MM = 0.5               # an hour with at least 0.5 mm precipitation counts as wet

# Validation gate for a month. If it trips, that month's metrics are not published.
MAX_QUALITY_EXCLUSION_RATE = 0.10
MIN_VALID_TRIPS = 1000
MIN_SHARE_IN_MONTH = 0.999
MAX_MONTH_CHANGE = 0.15         # warn if a file has 15% more or fewer rows than the month before

# Decision rule used in the hourly table
STEER_TOWARD_MAX_LONG_RATE = 0.10
STEER_AWAY_MIN_LONG_RATE = 0.50
MIN_TRIPS_FOR_ADVICE = 100

DOWNLOAD_RETRIES = 3
BACKOFF_SECONDS = 2
DUCKDB_MEMORY = "3GB"
