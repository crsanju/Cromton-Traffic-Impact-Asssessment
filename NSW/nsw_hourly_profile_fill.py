import glob
import os
from typing import List, Tuple

import numpy as np
import pandas as pd


FOLDER_PATH = os.path.dirname(os.path.abspath(__file__))
STATION_REF_FILENAME = "road_traffic_counts_station_reference.csv"
YEARLY_SUMMARY_FILENAME = "road_traffic_counts_yearly_summary.csv"
HOURLY_PATTERN = "road_traffic_counts_hourly_permanent*.csv"

HOURLY_COLUMNS = [f"hour_{i:02d}" for i in range(24)]
READ_COLUMNS = ["station_key", "date", "daily_total"] + HOURLY_COLUMNS

OUTPUT_SYNTHETIC = os.path.join(FOLDER_PATH, "nsw_missing_station_hourly_profiles.csv")
OUTPUT_COMPLETE = os.path.join(FOLDER_PATH, "nsw_station_hourly_profiles_complete.csv")


def normalize_station_key(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip()


def clip_peak_share(value: float) -> float:
    # Practical urban/rural peak-hour envelope used as a guardrail.
    return float(np.clip(value, 0.07, 0.16))


def load_station_reference(file_path: str) -> pd.DataFrame:
    cols = [
        "station_key",
        "station_id",
        "road_name",
        "suburb",
        "road_functional_hierarchy",
        "road_classification_admin",
        "road_classification_type",
        "permanent_station",
    ]
    df = pd.read_csv(file_path, usecols=lambda c: c in cols, low_memory=False)
    df["station_key"] = normalize_station_key(df["station_key"])
    df = df[df["station_key"].notna() & (df["station_key"] != "")].copy()
    df = df.drop_duplicates(subset=["station_key"])

    for col in ["road_functional_hierarchy", "road_classification_admin", "road_classification_type"]:
        if col in df.columns:
            df[col] = df[col].astype("string").str.strip()

    return df


def load_latest_daily_from_yearly_summary(file_path: str) -> pd.DataFrame:
    cols = [
        "station_key",
        "year",
        "traffic_count",
        "period",
        "classification_type",
        "cardinal_direction_name",
    ]

    yearly_df = pd.read_csv(file_path, usecols=lambda c: c in cols, low_memory=False)
    yearly_df["station_key"] = normalize_station_key(yearly_df["station_key"])
    yearly_df["year"] = pd.to_numeric(yearly_df["year"], errors="coerce")
    yearly_df["traffic_count"] = pd.to_numeric(yearly_df["traffic_count"], errors="coerce")

    for col in ["period", "classification_type", "cardinal_direction_name"]:
        yearly_df[col] = yearly_df[col].astype("string").str.strip()

    yearly_df = yearly_df[
        yearly_df["station_key"].notna()
        & (yearly_df["station_key"] != "")
        & yearly_df["year"].notna()
        & yearly_df["traffic_count"].notna()
        & (yearly_df["traffic_count"] > 0)
    ].copy()

    if yearly_df.empty:
        return pd.DataFrame(columns=["station_key", "daily_total_from_summary", "summary_year"])

    yearly_df["pref_period"] = (yearly_df["period"] == "ALL DAYS").astype(int)
    yearly_df["pref_class"] = (yearly_df["classification_type"] == "ALL VEHICLES").astype(int)
    yearly_df["pref_dir"] = yearly_df["cardinal_direction_name"].isin(["BOTH", "NORTHBOUND AND SOUTHBOUND"]).astype(int)

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
        .rename(columns={"traffic_count": "daily_total_from_summary", "year": "summary_year"})
    )

    return latest_summary[["station_key", "daily_total_from_summary", "summary_year"]]


def load_peak_period_preference(file_path: str) -> pd.DataFrame:
    cols = [
        "station_key",
        "year",
        "period",
        "traffic_count",
        "classification_type",
        "cardinal_direction_name",
    ]

    yearly_df = pd.read_csv(file_path, usecols=lambda c: c in cols, low_memory=False)
    yearly_df["station_key"] = normalize_station_key(yearly_df["station_key"])
    yearly_df["year"] = pd.to_numeric(yearly_df["year"], errors="coerce")
    yearly_df["traffic_count"] = pd.to_numeric(yearly_df["traffic_count"], errors="coerce")
    yearly_df["period"] = yearly_df["period"].astype("string").str.strip()
    yearly_df["classification_type"] = yearly_df["classification_type"].astype("string").str.strip()
    yearly_df["cardinal_direction_name"] = yearly_df["cardinal_direction_name"].astype("string").str.strip()

    yearly_df = yearly_df[
        yearly_df["station_key"].notna()
        & (yearly_df["station_key"] != "")
        & yearly_df["year"].notna()
        & yearly_df["traffic_count"].notna()
        & (yearly_df["traffic_count"] > 0)
        & yearly_df["period"].isin(["AM PEAK", "PM PEAK"])
    ].copy()

    if yearly_df.empty:
        return pd.DataFrame(columns=["station_key", "peak_period_preference", "am_peak_count", "pm_peak_count"])

    yearly_df["pref_class"] = (yearly_df["classification_type"] == "ALL VEHICLES").astype(int)
    yearly_df["pref_dir"] = yearly_df["cardinal_direction_name"].isin(["BOTH", "NORTHBOUND AND SOUTHBOUND"]).astype(int)

    station_period_best = (
        yearly_df.sort_values(
            ["station_key", "year", "period", "pref_class", "pref_dir", "traffic_count"],
            ascending=[True, True, True, False, False, False],
        )
        .drop_duplicates(subset=["station_key", "year", "period"], keep="first")
    )

    latest_periods = (
        station_period_best.sort_values(["station_key", "year"], ascending=[True, False])
        .drop_duplicates(subset=["station_key", "period"], keep="first")
    )

    piv = (
        latest_periods.pivot_table(
            index="station_key",
            columns="period",
            values="traffic_count",
            aggfunc="first",
        )
        .rename(columns={"AM PEAK": "am_peak_count", "PM PEAK": "pm_peak_count"})
        .reset_index()
    )

    piv["am_peak_count"] = pd.to_numeric(piv.get("am_peak_count", 0), errors="coerce").fillna(0)
    piv["pm_peak_count"] = pd.to_numeric(piv.get("pm_peak_count", 0), errors="coerce").fillna(0)

    piv["peak_period_preference"] = np.where(
        piv["am_peak_count"] > piv["pm_peak_count"],
        "AM",
        np.where(piv["pm_peak_count"] > piv["am_peak_count"], "PM", "BALANCED"),
    )

    return piv[["station_key", "peak_period_preference", "am_peak_count", "pm_peak_count"]]


def read_hourly_chunks(file_path: str, chunksize: int = 250000):
    return pd.read_csv(
        file_path,
        usecols=lambda c: c in READ_COLUMNS,
        chunksize=chunksize,
        low_memory=False,
    )


def process_hourly_chunk(chunk: pd.DataFrame) -> pd.DataFrame:
    if "station_key" not in chunk.columns:
        return pd.DataFrame()

    chunk["station_key"] = normalize_station_key(chunk["station_key"])
    chunk = chunk[chunk["station_key"].notna() & (chunk["station_key"] != "")].copy()
    if chunk.empty:
        return chunk

    if "date" in chunk.columns:
        chunk["date"] = pd.to_datetime(chunk["date"], errors="coerce", utc=True)
    else:
        chunk["date"] = pd.NaT

    for col in ["daily_total"] + HOURLY_COLUMNS:
        if col in chunk.columns:
            chunk[col] = pd.to_numeric(chunk[col], errors="coerce").fillna(0)
        else:
            chunk[col] = 0

    chunk["sum_hours"] = chunk[HOURLY_COLUMNS].sum(axis=1)
    chunk["has_hourly_profile"] = chunk["sum_hours"] > 0
    return chunk


def build_observed_latest_and_profiles(volume_files: List[str]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    date_groups = []
    profile_rows = []

    for file in volume_files:
        print(f"Reading {os.path.basename(file)}")
        for chunk in read_hourly_chunks(file):
            prepared = process_hourly_chunk(chunk)
            if prepared.empty:
                continue

            grouped = (
                prepared.groupby(["station_key", "date"], dropna=False)[["daily_total", "sum_hours"] + HOURLY_COLUMNS]
                .sum()
                .reset_index()
            )
            grouped["has_hourly_profile"] = grouped["sum_hours"] > 0
            date_groups.append(grouped)

            prof = grouped[grouped["has_hourly_profile"]].copy()
            if not prof.empty:
                profile_rows.append(prof)

    if not date_groups:
        return pd.DataFrame(), pd.DataFrame()

    station_date = (
        pd.concat(date_groups, ignore_index=True)
        .groupby(["station_key", "date"], dropna=False)[["daily_total", "sum_hours"] + HOURLY_COLUMNS]
        .sum()
        .reset_index()
    )
    station_date["has_hourly_profile"] = station_date["sum_hours"] > 0

    valid_dates = station_date[station_date["date"].notna()].copy()
    latest_observed = (
        valid_dates.sort_values("date", ascending=False)
        .drop_duplicates(subset=["station_key"], keep="first")
        .rename(columns={"date": "latest_date"})
    )

    # Build donor proportions from all valid station-day profiles.
    prof_source = station_date[station_date["has_hourly_profile"]].copy()
    if prof_source.empty:
        donor_profiles = pd.DataFrame()
    else:
        denom = prof_source["sum_hours"].replace(0, np.nan)
        for col in HOURLY_COLUMNS:
            prof_source[f"prop_{col}"] = (prof_source[col] / denom).fillna(0)

        prop_cols = [f"prop_{c}" for c in HOURLY_COLUMNS]
        donor_profiles = (
            prof_source.groupby("station_key", as_index=False)[prop_cols + ["sum_hours"]]
            .median(numeric_only=True)
            .rename(columns={"sum_hours": "median_daily_volume"})
        )
        donor_profiles["peak_share"] = donor_profiles[prop_cols].max(axis=1)
        donor_profiles["peak_hour"] = donor_profiles[prop_cols].idxmax(axis=1).str.replace("prop_", "", regex=False)

    return latest_observed, donor_profiles


def derive_template_profile(donors: pd.DataFrame) -> Tuple[np.ndarray, int, float]:
    prop_cols = [f"prop_{c}" for c in HOURLY_COLUMNS]

    if donors.empty:
        base = np.full(24, 1.0 / 24.0, dtype=float)
        return base, 8, 1.0 / 24.0

    template = donors[prop_cols].median(numeric_only=True).to_numpy(dtype=float)
    if template.sum() <= 0:
        template = np.full(24, 1.0 / 24.0, dtype=float)
    else:
        template = template / template.sum()

    peak_idx = int(np.argmax(template))
    target_peak = clip_peak_share(float(donors["peak_share"].median()))
    return template, peak_idx, target_peak


def apply_peak_adjustment(template: np.ndarray, peak_idx: int, target_peak_share: float) -> np.ndarray:
    adjusted = template.copy()
    current_peak = float(adjusted[peak_idx])

    if current_peak <= 0:
        adjusted = np.full(24, 1.0 / 24.0, dtype=float)
        current_peak = float(adjusted[peak_idx])

    target_peak_share = clip_peak_share(target_peak_share)
    if abs(current_peak - target_peak_share) < 1e-6:
        return adjusted / adjusted.sum()

    non_peak_mask = np.ones(24, dtype=bool)
    non_peak_mask[peak_idx] = False
    non_peak_sum = float(adjusted[non_peak_mask].sum())
    if non_peak_sum <= 0:
        return adjusted / adjusted.sum()

    adjusted[peak_idx] = target_peak_share
    adjusted[non_peak_mask] = adjusted[non_peak_mask] * ((1.0 - target_peak_share) / non_peak_sum)
    return adjusted / adjusted.sum()


def shift_template_peak_to_period(template: np.ndarray, target_hour: int) -> np.ndarray:
    src_peak = int(np.argmax(template))
    shift = int(target_hour - src_peak)
    shifted = np.roll(template, shift)
    shifted = shifted / shifted.sum() if shifted.sum() > 0 else np.full(24, 1.0 / 24.0, dtype=float)
    return shifted


def proportional_hourly_split(daily_total: float, proportions: np.ndarray) -> np.ndarray:
    raw = daily_total * proportions
    rounded = np.rint(raw).astype(int)
    diff = int(round(daily_total - rounded.sum()))

    if diff != 0:
        peak_idx = int(np.argmax(proportions))
        rounded[peak_idx] += diff

    rounded = np.maximum(rounded, 0)
    return rounded


def choose_donors(station_row: pd.Series, station_donor_df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    same_func = station_donor_df[
        station_donor_df["road_functional_hierarchy"].fillna("")
        == str(station_row.get("road_functional_hierarchy", "") or "")
    ]
    if len(same_func) >= 20:
        return same_func, "road_functional_hierarchy"

    same_admin = station_donor_df[
        station_donor_df["road_classification_admin"].fillna("")
        == str(station_row.get("road_classification_admin", "") or "")
    ]
    if len(same_admin) >= 20:
        return same_admin, "road_classification_admin"

    if len(station_donor_df) > 0:
        return station_donor_df, "global"

    return pd.DataFrame(), "none"


def synthesize_missing_profiles(stations_df: pd.DataFrame, latest_observed: pd.DataFrame, donor_profiles: pd.DataFrame) -> pd.DataFrame:
    observed_keys = set(latest_observed[latest_observed["has_hourly_profile"]]["station_key"].astype(str))
    missing = stations_df[~stations_df["station_key"].isin(observed_keys)].copy()
    missing = missing[missing["daily_total_from_summary"].fillna(0) > 0].copy()

    if missing.empty:
        return pd.DataFrame(columns=["station_key", "daily_total"] + HOURLY_COLUMNS)

    station_donor_df = pd.merge(stations_df, donor_profiles, on="station_key", how="inner")

    records = []
    for _, row in missing.iterrows():
        donors, template_level = choose_donors(row, station_donor_df)
        template, peak_idx, target_peak = derive_template_profile(donors)

        peak_pref = str(row.get("peak_period_preference", "BALANCED") or "BALANCED")
        if peak_pref == "AM":
            template = shift_template_peak_to_period(template, target_hour=8)
        elif peak_pref == "PM":
            template = shift_template_peak_to_period(template, target_hour=17)

        peak_idx = int(np.argmax(template))
        adjusted = apply_peak_adjustment(template, peak_idx, target_peak)

        daily_total = float(row["daily_total_from_summary"])
        hourly_vals = proportional_hourly_split(daily_total, adjusted)

        rec = {
            "station_key": row["station_key"],
            "daily_total": int(round(daily_total)),
            "profile_source": "synthetic_austroads_peak_proportional",
            "template_level": template_level,
            "template_donor_count": int(len(donors)),
            "peak_period_preference": peak_pref,
            "peak_hour": HOURLY_COLUMNS[peak_idx],
            "peak_share": float(adjusted[peak_idx]),
            "latest_date": pd.NaT,
            "is_synthetic": 1,
        }
        for i, col in enumerate(HOURLY_COLUMNS):
            rec[col] = int(hourly_vals[i])

        records.append(rec)

    return pd.DataFrame(records)


def build_complete_output(latest_observed: pd.DataFrame, synthetic_df: pd.DataFrame) -> pd.DataFrame:
    observed = latest_observed.copy()
    observed = observed[observed["has_hourly_profile"]].copy()
    observed["profile_source"] = "observed_hourly"
    observed["template_level"] = "na"
    observed["template_donor_count"] = 0
    observed["peak_period_preference"] = "OBSERVED"
    observed["peak_hour"] = observed[HOURLY_COLUMNS].idxmax(axis=1)
    denom = observed["sum_hours"].replace(0, np.nan)
    observed["peak_share"] = (observed[HOURLY_COLUMNS].max(axis=1) / denom).fillna(0)
    observed["is_synthetic"] = 0

    keep_cols = [
        "station_key",
        "latest_date",
        "daily_total",
        "profile_source",
        "template_level",
        "template_donor_count",
        "peak_period_preference",
        "peak_hour",
        "peak_share",
        "is_synthetic",
    ] + HOURLY_COLUMNS

    observed = observed[keep_cols]

    if synthetic_df.empty:
        return observed.sort_values("station_key").reset_index(drop=True)

    combined = pd.concat([observed, synthetic_df[keep_cols]], ignore_index=True)
    combined = combined.sort_values(["station_key", "is_synthetic"]).drop_duplicates(
        subset=["station_key"], keep="first"
    )
    return combined.reset_index(drop=True)


def generate_hourly_profiles(
    folder_path: str = FOLDER_PATH,
    station_ref_filename: str = STATION_REF_FILENAME,
    yearly_summary_filename: str = YEARLY_SUMMARY_FILENAME,
    hourly_pattern: str = HOURLY_PATTERN,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    station_ref_path = os.path.join(folder_path, station_ref_filename)
    yearly_summary_path = os.path.join(folder_path, yearly_summary_filename)
    volume_files = glob.glob(os.path.join(folder_path, hourly_pattern))

    if not os.path.exists(station_ref_path):
        raise FileNotFoundError(f"Missing station reference file: {station_ref_path}")
    if not os.path.exists(yearly_summary_path):
        raise FileNotFoundError(f"Missing yearly summary file: {yearly_summary_path}")
    if not volume_files:
        raise FileNotFoundError("No hourly permanent traffic files found.")

    stations_df = load_station_reference(station_ref_path)
    yearly_daily = load_latest_daily_from_yearly_summary(yearly_summary_path)
    peak_pref = load_peak_period_preference(yearly_summary_path)
    stations_df = pd.merge(stations_df, yearly_daily, on="station_key", how="left")
    stations_df = pd.merge(stations_df, peak_pref, on="station_key", how="left")

    latest_observed, donor_profiles = build_observed_latest_and_profiles(volume_files)
    synthetic_df = synthesize_missing_profiles(stations_df, latest_observed, donor_profiles)
    complete_df = build_complete_output(latest_observed, synthetic_df)
    return synthetic_df, complete_df


def main() -> None:
    station_ref_path = os.path.join(FOLDER_PATH, STATION_REF_FILENAME)
    yearly_summary_path = os.path.join(FOLDER_PATH, YEARLY_SUMMARY_FILENAME)
    volume_files = glob.glob(os.path.join(FOLDER_PATH, HOURLY_PATTERN))

    if not os.path.exists(station_ref_path):
        raise FileNotFoundError(f"Missing station reference file: {station_ref_path}")
    if not os.path.exists(yearly_summary_path):
        raise FileNotFoundError(f"Missing yearly summary file: {yearly_summary_path}")
    if not volume_files:
        raise FileNotFoundError("No hourly permanent traffic files found.")

    synthetic_df, complete_df = generate_hourly_profiles(
        folder_path=FOLDER_PATH,
        station_ref_filename=STATION_REF_FILENAME,
        yearly_summary_filename=YEARLY_SUMMARY_FILENAME,
        hourly_pattern=HOURLY_PATTERN,
    )

    synthetic_df.to_csv(OUTPUT_SYNTHETIC, index=False)
    complete_df.to_csv(OUTPUT_COMPLETE, index=False)

    print("NSW hourly profile synthesis complete.")
    print(f"Observed stations with hourly profiles: {len(complete_df[complete_df['is_synthetic'] == 0])}")
    print(f"Synthetic stations generated: {len(synthetic_df)}")
    print(f"Synthetic output: {OUTPUT_SYNTHETIC}")
    print(f"Complete output: {OUTPUT_COMPLETE}")


if __name__ == "__main__":
    main()