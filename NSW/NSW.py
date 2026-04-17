import pandas as pd
import geopandas as gpd
import glob
import os

from nsw_hourly_profile_fill import generate_hourly_profiles


# ==========================================
# CONFIGURATION 
# ==========================================
# Your exact folder location
FOLDER_PATH = r"C:\Users\Sanju\OneDrive - CromptonConcepts\Sharepoint - Documents\Clients\Application development\Apps\Cromton Traffic Impact Asssessment\NSW"

# The exact name of your station reference file
STATION_REF_FILENAME = "road_traffic_counts_station_reference.csv"
YEARLY_SUMMARY_FILENAME = "road_traffic_counts_yearly_summary.csv"

# Output will be saved in the exact same folder as this Python script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_GEOJSON = os.path.join(SCRIPT_DIR, "latest_nsw_traffic_volume.geojson")

HOURLY_COLUMNS = [f"hour_{i:02d}" for i in range(24)]
NUMERIC_COLUMNS = ["daily_total"] + HOURLY_COLUMNS
READ_COLUMNS = ["station_key", "date", "year"] + NUMERIC_COLUMNS
AM_DESIGN_HOUR_COLUMNS = ["hour_06", "hour_07", "hour_08", "hour_09"]
PM_DESIGN_HOUR_COLUMNS = ["hour_15", "hour_16", "hour_17", "hour_18"]


def normalize_station_key(series):
    return series.astype("string").str.strip()


def read_volume_file(file_path, chunksize=250000):
    if file_path.lower().endswith(".csv"):
        return pd.read_csv(
            file_path,
            usecols=lambda c: c in READ_COLUMNS,
            chunksize=chunksize,
            low_memory=False,
        )

    # Excel readers do not support chunksize, so wrap in a list for a common loop.
    return [pd.read_excel(file_path, usecols=lambda c: c in READ_COLUMNS)]


def prepare_chunk(chunk_df):
    if "station_key" not in chunk_df.columns:
        return pd.DataFrame()

    chunk_df["station_key"] = normalize_station_key(chunk_df["station_key"])
    chunk_df = chunk_df[chunk_df["station_key"].notna() & (chunk_df["station_key"] != "")]

    if "date" in chunk_df.columns:
        chunk_df["date"] = pd.to_datetime(chunk_df["date"], errors="coerce", utc=True)
    else:
        chunk_df["date"] = pd.NaT

    for col in NUMERIC_COLUMNS:
        if col in chunk_df.columns:
            chunk_df[col] = pd.to_numeric(chunk_df[col], errors="coerce").fillna(0)
        else:
            chunk_df[col] = 0

    return chunk_df[["station_key", "date"] + NUMERIC_COLUMNS]


def aggregate_latest_and_yearly(volume_files):
    grouped_chunks = []

    for file in volume_files:
        print(f" -> Loading {os.path.basename(file)}...")
        try:
            for chunk in read_volume_file(file):
                prepared = prepare_chunk(chunk)
                if prepared.empty:
                    continue

                grouped = (
                    prepared.groupby(["station_key", "date"], dropna=False)[NUMERIC_COLUMNS]
                    .sum()
                    .reset_index()
                )
                grouped_chunks.append(grouped)
        except Exception as e:
            print(f"    Skipping {os.path.basename(file)} due to error: {e}")

    if not grouped_chunks:
        return pd.DataFrame(), pd.DataFrame()

    station_date_totals = (
        pd.concat(grouped_chunks, ignore_index=True)
        .groupby(["station_key", "date"], dropna=False)[NUMERIC_COLUMNS]
        .sum()
        .reset_index()
    )

    valid_dates = station_date_totals[station_date_totals["date"].notna()].copy()
    if valid_dates.empty:
        latest_hourly = pd.DataFrame(columns=["station_key", "Latest_Date", "Latest_Year"] + NUMERIC_COLUMNS)
    else:
        latest_hourly = (
            valid_dates.sort_values("date", ascending=False)
            .drop_duplicates(subset=["station_key"], keep="first")
            .rename(columns={"date": "Latest_Date"})
        )
        latest_hourly["Latest_Year"] = latest_hourly["Latest_Date"].dt.year.astype("Int64")

    yearly_volume = (
        valid_dates.assign(year=valid_dates["date"].dt.year)
        .groupby(["station_key", "year"], as_index=False)["daily_total"]
        .sum()
        .rename(columns={"year": "Year", "daily_total": "Yearly_Volume"})
    )

    latest_yearly = (
        yearly_volume.sort_values("Year", ascending=False)
        .drop_duplicates(subset=["station_key"], keep="first")
        .rename(columns={"Year": "Latest_Year_For_Yearly"})
    )

    return latest_hourly, latest_yearly


def load_latest_yearly_summary(yearly_file_path):
    if not os.path.exists(yearly_file_path):
        print(f"Yearly summary file not found: {yearly_file_path}")
        return pd.DataFrame(columns=["station_key", "Yearly_Summary_Year", "Yearly_Summary_Count"])

    print(f"Loading yearly summary data from {os.path.basename(yearly_file_path)}...")
    cols = [
        "station_key",
        "year",
        "traffic_count",
        "period",
        "classification_type",
        "cardinal_direction_name",
    ]

    yearly_df = pd.read_csv(yearly_file_path, usecols=lambda c: c in cols, low_memory=False)
    yearly_df["station_key"] = normalize_station_key(yearly_df["station_key"])
    yearly_df["year"] = pd.to_numeric(yearly_df["year"], errors="coerce")
    yearly_df["traffic_count"] = pd.to_numeric(yearly_df["traffic_count"], errors="coerce")

    for col in ["period", "classification_type", "cardinal_direction_name"]:
        if col in yearly_df.columns:
            yearly_df[col] = yearly_df[col].astype("string").str.strip()

    yearly_df = yearly_df[
        yearly_df["station_key"].notna()
        & (yearly_df["station_key"] != "")
        & yearly_df["year"].notna()
        & yearly_df["traffic_count"].notna()
    ].copy()

    if yearly_df.empty:
        return pd.DataFrame(columns=["station_key", "Yearly_Summary_Year", "Yearly_Summary_Count"])

    # Pick one representative record per station-year, then latest year per station.
    yearly_df["pref_period"] = (yearly_df["period"] == "ALL DAYS").astype(int)
    yearly_df["pref_class"] = (yearly_df["classification_type"] == "ALL VEHICLES").astype(int)
    yearly_df["pref_dir"] = (yearly_df["cardinal_direction_name"] == "BOTH").astype(int)

    station_year_best = (
        yearly_df.sort_values(
            ["station_key", "year", "pref_period", "pref_class", "pref_dir", "traffic_count"],
            ascending=[True, True, False, False, False, False],
        )
        .drop_duplicates(subset=["station_key", "year"], keep="first")
    )

    latest_summary = (
        station_year_best.sort_values(["station_key", "year"], ascending=[True, False])
        .drop_duplicates(subset=["station_key"], keep="first")
        .rename(columns={"year": "Yearly_Summary_Year", "traffic_count": "Yearly_Summary_Count"})
    )

    return latest_summary[["station_key", "Yearly_Summary_Year", "Yearly_Summary_Count"]]


def add_design_hour_columns(df):
    available_am = [c for c in AM_DESIGN_HOUR_COLUMNS if c in df.columns]
    available_pm = [c for c in PM_DESIGN_HOUR_COLUMNS if c in df.columns]
    available_all = [c for c in HOURLY_COLUMNS if c in df.columns]

    if available_am:
        df["am_design_hour"] = df[available_am].max(axis=1)
        df["am_design_hour_name"] = df[available_am].idxmax(axis=1)
    else:
        df["am_design_hour"] = pd.NA
        df["am_design_hour_name"] = pd.NA

    if available_pm:
        df["pm_design_hour"] = df[available_pm].max(axis=1)
        df["pm_design_hour_name"] = df[available_pm].idxmax(axis=1)
    else:
        df["pm_design_hour"] = pd.NA
        df["pm_design_hour_name"] = pd.NA

    if available_all:
        df["daily_peak_hour"] = df[available_all].max(axis=1)
        df["daily_peak_hour_name"] = df[available_all].idxmax(axis=1)
    else:
        df["daily_peak_hour"] = pd.NA
        df["daily_peak_hour_name"] = pd.NA

    return df

def process_nsw_traffic_data():
    print("--- Starting NSW Traffic Data Processing ---")
    
    # 1. Load the Station Reference File
    ref_file_path = os.path.join(FOLDER_PATH, STATION_REF_FILENAME)
    if not os.path.exists(ref_file_path):
        print(f"ERROR: Cannot find the station reference file at {ref_file_path}")
        return

    print(f"Loading Station Reference Data...")
    # Loading coordinates and some useful metadata like road name and suburb
    ref_cols = ['station_key', 'wgs84_latitude', 'wgs84_longitude', 'road_name', 'suburb']
    
    # usecols with a lambda allows it to only load the columns if they exist, preventing errors
    stations_df = pd.read_csv(ref_file_path, usecols=lambda c: c in ref_cols)
    stations_df['station_key'] = normalize_station_key(stations_df['station_key'])
    stations_df = stations_df.drop_duplicates(subset=['station_key'])
    print(f"Station reference rows loaded: {len(stations_df)}")

    # 2. Find Hourly Volume Files
    hourly_csv_pattern = os.path.join(FOLDER_PATH, "road_traffic_counts_hourly_permanent*.csv")
    volume_files = glob.glob(hourly_csv_pattern)
    yearly_summary_path = os.path.join(FOLDER_PATH, YEARLY_SUMMARY_FILENAME)

    if not volume_files:
        print("No hourly traffic volume databases found.")
        return

    # 3. Read and Aggregate Hourly Files Efficiently
    print(f"Found {len(volume_files)} volume databases. Reading and aggregating...")
    latest_hourly, latest_yearly = aggregate_latest_and_yearly(volume_files)

    if latest_hourly.empty and latest_yearly.empty:
        print("No valid hourly volume data loaded. Exiting.")
        return

    # 4. Load latest yearly summary counts (latest year per station)
    latest_summary = load_latest_yearly_summary(yearly_summary_path)

    # 5. Build complete hourly profiles by filling missing stations using
    #    peak + proportional synthesis from donor stations.
    complete_profiles = pd.DataFrame()
    try:
        print("Building complete hourly profiles (observed + synthetic for missing stations)...")
        _, complete_profiles = generate_hourly_profiles(
            folder_path=FOLDER_PATH,
            station_ref_filename=STATION_REF_FILENAME,
            yearly_summary_filename=YEARLY_SUMMARY_FILENAME,
            hourly_pattern="road_traffic_counts_hourly_permanent*.csv",
        )
        print(f"Complete hourly profile rows: {len(complete_profiles)}")
    except Exception as e:
        print(f"Hourly profile synthesis failed, using observed-only hourly data. Reason: {e}")

    if not complete_profiles.empty:
        complete_profiles = complete_profiles.rename(columns={"latest_date": "Latest_Date"})
        complete_profiles["Latest_Date"] = pd.to_datetime(complete_profiles["Latest_Date"], errors="coerce", utc=True)
        complete_profiles["Latest_Year"] = complete_profiles["Latest_Date"].dt.year.astype("Int64")
        profile_cols = [
            "station_key",
            "Latest_Date",
            "Latest_Year",
            "daily_total",
            "profile_source",
            "is_synthetic",
            "peak_period_preference",
        ] + HOURLY_COLUMNS
        hourly_for_merge = complete_profiles[[c for c in profile_cols if c in complete_profiles.columns]].copy()
    else:
        hourly_for_merge = latest_hourly.copy()
        if "profile_source" not in hourly_for_merge.columns:
            hourly_for_merge["profile_source"] = "observed_hourly"
        if "is_synthetic" not in hourly_for_merge.columns:
            hourly_for_merge["is_synthetic"] = 0
        if "peak_period_preference" not in hourly_for_merge.columns:
            hourly_for_merge["peak_period_preference"] = "UNKNOWN"

    # 6. Merge Volumes with GPS Coordinates
    print("Merging traffic volumes with GPS coordinates...")
    # Left join preserves all station reference rows, even if a station has no volume records.
    merged_data = pd.merge(stations_df, hourly_for_merge, on='station_key', how='left')
    merged_data = pd.merge(merged_data, latest_yearly, on='station_key', how='left')
    merged_data = pd.merge(merged_data, latest_summary, on='station_key', how='left')

    # If synthesized rows are present, set latest year from summary where no observed date exists.
    if "Latest_Year" in merged_data.columns and "Yearly_Summary_Year" in merged_data.columns:
        merged_data["Latest_Year"] = merged_data["Latest_Year"].fillna(merged_data["Yearly_Summary_Year"]).astype("Int64")

    merged_data = add_design_hour_columns(merged_data)

    # Ensure no rows with missing coordinates slipped through
    merged_data.dropna(subset=['wgs84_latitude', 'wgs84_longitude'], inplace=True)

    matched_count = merged_data['Latest_Date'].notna().sum() if 'Latest_Date' in merged_data.columns else 0
    yearly_summary_count = merged_data['Yearly_Summary_Year'].notna().sum() if 'Yearly_Summary_Year' in merged_data.columns else 0
    print(f"Stations in output with coordinates: {len(merged_data)}")
    print(f"Stations with hourly data matched: {matched_count}")
    print(f"Stations with yearly summary matched: {yearly_summary_count}")

    if merged_data.empty:
        print("ERROR: No matching stations found after merging. GeoJSON will not be created.")
        return

    synthetic_count = merged_data['is_synthetic'].fillna(0).astype(int).sum() if 'is_synthetic' in merged_data.columns else 0
    print(f"Stations with synthesized hourly profile: {synthetic_count}")
    if 'am_design_hour' in merged_data.columns and 'pm_design_hour' in merged_data.columns:
        am_nonnull = merged_data['am_design_hour'].notna().sum()
        pm_nonnull = merged_data['pm_design_hour'].notna().sum()
        print(f"Stations with AM design hour: {am_nonnull}")
        print(f"Stations with PM design hour: {pm_nonnull}")

    # 7. Convert to GeoJSON using GeoPandas
    print("Converting to geospatial format...")
    gdf = gpd.GeoDataFrame(
        merged_data, 
        geometry=gpd.points_from_xy(merged_data['wgs84_longitude'], merged_data['wgs84_latitude']),
        crs="EPSG:4326"
    )
    
    # Drop the raw lat/lon columns as GeoJSON uses the actual 'geometry' property
    gdf.drop(columns=['wgs84_latitude', 'wgs84_longitude'], inplace=True)

    # 8. Export the file
    print(f"Exporting GeoJSON to {OUTPUT_GEOJSON}...")
    gdf.to_file(OUTPUT_GEOJSON, driver="GeoJSON")
    print("✅ Success! GeoJSON file exported and ready for your Traffic Impact Assessment App.")

if __name__ == "__main__":
    process_nsw_traffic_data()