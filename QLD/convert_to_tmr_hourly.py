#!/usr/bin/env python3
"""Convert council traffic count GeoJSON files to TMR-style hourly records.

This script standardizes multiple council schemas into a common hourly format that
matches the TMR structure used in the traffic impact app:
- SITE_ID
- ROAD_NAME
- GAZETTAL_DIRECTION
- LATITUDE
- LONGITUDE
- DESCRIPTION
- HOURS
- WEEKDAY_AVERAGE
- WEEKEND_AVERAGE

It can also add optional helper fields for analysis:
- SOURCE_FILE
- SOURCE_COUNCIL
- SOURCE_RECORD_ID
- DAILY_DIRECTIONAL_TOTAL
- PROPORTIONAL_VOLUME
- DIRECTION_AM_PEAK_HOUR / DIRECTION_AM_PEAK_VOL
- DIRECTION_PM_PEAK_HOUR / DIRECTION_PM_PEAK_VOL
- DIRECTION_DAILY_PEAK_HOUR / DIRECTION_DAILY_PEAK_VOL

Assumptions aligned to Austroads-style planning practice:
- Uses a configurable 24-hour proportional profile for weekday and weekend flows.
- Defaults to a typical urban commuter profile (AM + PM peaks).
- Uses directional totals where supplied; otherwise infers splits from available data.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


TMR_REQUIRED_FIELDS = [
    "SITE_ID",
    "ROAD_NAME",
    "GAZETTAL_DIRECTION",
    "LATITUDE",
    "LONGITUDE",
    "DESCRIPTION",
    "HOURS",
    "WEEKDAY_AVERAGE",
    "WEEKEND_AVERAGE",
]

EXTRA_FIELDS = [
    "SOURCE_FILE",
    "SOURCE_COUNCIL",
    "SOURCE_RECORD_ID",
    "DAILY_DIRECTIONAL_TOTAL",
    "PROPORTIONAL_VOLUME",
    "DIRECTION_AM_PEAK_HOUR",
    "DIRECTION_AM_PEAK_VOL",
    "DIRECTION_PM_PEAK_HOUR",
    "DIRECTION_PM_PEAK_VOL",
    "DIRECTION_DAILY_PEAK_HOUR",
    "DIRECTION_DAILY_PEAK_VOL",
]

# 24-hour labels used in TMR data.
HOUR_LABELS = [f"{h} to {h + 1}" for h in range(24)]

# Typical urban weekday / weekend hourly profiles (sum = 1.0).
# These are configurable and can be replaced with local observed profiles.
WEEKDAY_PROFILE = [
    0.010,
    0.008,
    0.006,
    0.005,
    0.007,
    0.020,
    0.050,
    0.080,
    0.070,
    0.055,
    0.045,
    0.040,
    0.045,
    0.050,
    0.055,
    0.065,
    0.075,
    0.080,
    0.065,
    0.050,
    0.040,
    0.030,
    0.020,
    0.014,
]

WEEKEND_PROFILE = [
    0.014,
    0.010,
    0.008,
    0.006,
    0.006,
    0.010,
    0.020,
    0.035,
    0.045,
    0.055,
    0.060,
    0.065,
    0.070,
    0.070,
    0.065,
    0.060,
    0.055,
    0.050,
    0.045,
    0.040,
    0.035,
    0.028,
    0.020,
    0.013,
]


@dataclass
class DirectionalCounts:
    road_name: str
    description: str
    latitude: float
    longitude: float
    site_id: str
    source_record_id: str
    source_file: str
    source_council: str
    gazettal_direction_name: str
    against_direction_name: str
    gazettal_daily_total: Optional[float]
    against_daily_total: Optional[float]
    am_peak_hour: Optional[int] = None
    am_peak_vol: Optional[float] = None
    pm_peak_hour: Optional[int] = None
    pm_peak_vol: Optional[float] = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert council GeoJSON to TMR hourly format.")
    parser.add_argument(
        "--input-dir",
        default=".",
        help="Folder containing source GeoJSON files.",
    )
    parser.add_argument(
        "--output-prefix",
        default="common_tmr_hourly",
        help="Output file prefix (without extension).",
    )
    parser.add_argument(
        "--include",
        nargs="*",
        default=["Brisbane.geojson", "goldcoast.geojson", "Ipswich.geojson", "logan.geojson", "toowoomba.geojson"],
        help="Specific GeoJSON files to convert.",
    )
    parser.add_argument(
        "--weekend-factor",
        type=float,
        default=0.90,
        help="Fallback weekend factor applied to weekday totals where weekend totals are unavailable.",
    )
    parser.add_argument(
        "--include-helper-fields",
        action="store_true",
        help="Include non-TMR helper fields in output (off by default for exact TMR schema).",
    )
    parser.add_argument(
        "--split-by-source",
        action="store_true",
        help="Also write one normalized output per source database file.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Folder to write output files. Defaults to --input-dir if not set.",
    )
    parser.add_argument(
        "--compress",
        action="store_true",
        help="Write gzip-compressed CSV (.csv.gz) instead of plain CSV. Skips GeoJSON output. Ideal for GitHub.",
    )
    parser.add_argument(
        "--split-geojson-mb",
        type=float,
        default=20.0,
        help="Max MB per GeoJSON output file. Files larger than this are split into numbered chunks. Default 20 (safe under GitHub 25MB limit).",
    )
    return parser.parse_args()


def normalize_profile(profile: List[float]) -> List[float]:
    total = sum(profile)
    if total <= 0:
        raise ValueError("Profile total must be > 0")
    return [p / total for p in profile]


def safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if math.isfinite(float(value)):
            return float(value)
        return None
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "NA", "NULL", "NONE"}:
        return None
    text = text.replace(",", "")
    try:
        val = float(text)
        if math.isfinite(val):
            return val
    except ValueError:
        return None
    return None


def parse_hour_index(value: Any) -> Optional[int]:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        num = int(float(value))
        if 0 <= num <= 23:
            return num
        if 100 <= num <= 2359:
            return max(0, min(23, num // 100))
        return None

    text = str(value).strip()
    if not text:
        return None

    # Accept formats like "7", "07", "07:00", "700", "1700".
    if re.match(r"^\d{1,2}:\d{2}$", text):
        hour = int(text.split(":", 1)[0])
        return hour if 0 <= hour <= 23 else None

    if re.match(r"^\d{3,4}$", text):
        hour = int(text) // 100
        return hour if 0 <= hour <= 23 else None

    if re.match(r"^\d{1,2}$", text):
        hour = int(text)
        return hour if 0 <= hour <= 23 else None

    return None


def extract_direction_and_volume(raw: Any) -> Tuple[Optional[str], Optional[float]]:
    if raw is None:
        return None, None
    text = str(raw).strip()
    if not text:
        return None, None

    # Matches patterns like:
    # "East 705", "Westbound only 7345", "Nthbound only 5338"
    m = re.match(r"^(.*?)(-?\d+(?:,\d{3})*(?:\.\d+)?)$", text)
    if not m:
        num = safe_float(text)
        if num is not None:
            return None, num
        return text, None

    direction = m.group(1).strip()
    volume = safe_float(m.group(2))
    return direction or None, volume


def get_coordinates(feature: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []
    if isinstance(coords, list) and len(coords) >= 2:
        lon = safe_float(coords[0])
        lat = safe_float(coords[1])
        return lat, lon
    return None, None


def slug_council_name(file_name: str) -> str:
    return Path(file_name).stem.strip().upper()


def make_site_id(council: str, source_id: str) -> str:
    source_id = source_id.strip() if source_id else "UNKNOWN"
    return f"{council}_{source_id}"


def normalize_direction_name(name: Optional[str], fallback: str) -> str:
    if not name:
        return fallback
    txt = name.strip().upper()
    txt = txt.replace("NTHBOUND", "NORTHBOUND")
    txt = txt.replace("SOUTHBOUND ONLY", "SOUTHBOUND")
    txt = txt.replace("NORTHBOUND ONLY", "NORTHBOUND")
    txt = txt.replace("EASTBOUND ONLY", "EASTBOUND")
    txt = txt.replace("WESTBOUND ONLY", "WESTBOUND")
    txt = txt.replace("ONLY", "").strip()
    return txt if txt else fallback


def extract_from_brisbane_or_goldcoast(props: Dict[str, Any], feature: Dict[str, Any], file_name: str) -> DirectionalCounts:
    lat, lon = get_coordinates(feature)
    council = slug_council_name(file_name)

    source_id = str(props.get("OBJECTID") or feature.get("id") or "")
    site_id = make_site_id(council, source_id)

    road_name = str(props.get("STREET") or "UNKNOWN ROAD")
    description = str(props.get("LOCATION") or "")

    d1_name, d1_vol = extract_direction_and_volume(props.get("DIRECTION_1A"))
    d2_name, d2_vol = extract_direction_and_volume(props.get("DIRECTION_2A"))

    vpd = safe_float(props.get("VPD"))

    if d1_vol is None and d2_vol is None and vpd is not None:
        d1_vol = vpd * 0.5
        d2_vol = vpd * 0.5
    elif d1_vol is not None and d2_vol is None:
        d2_vol = max((vpd - d1_vol), 0.0) if vpd is not None else d1_vol
    elif d2_vol is not None and d1_vol is None:
        d1_vol = max((vpd - d2_vol), 0.0) if vpd is not None else d2_vol

    gaz_name = normalize_direction_name(d1_name, "GAZETTAL")
    ag_name = normalize_direction_name(d2_name, "AGAINST GAZETTAL")

    return DirectionalCounts(
        road_name=road_name,
        description=description,
        latitude=lat or 0.0,
        longitude=lon or 0.0,
        site_id=site_id,
        source_record_id=source_id,
        source_file=file_name,
        source_council=council,
        gazettal_direction_name=gaz_name,
        against_direction_name=ag_name,
        gazettal_daily_total=d1_vol,
        against_daily_total=d2_vol,
    )


def extract_from_logan(props: Dict[str, Any], feature: Dict[str, Any], file_name: str) -> DirectionalCounts:
    lat, lon = get_coordinates(feature)
    council = slug_council_name(file_name)

    source_id = str(props.get("OBJECTID") or feature.get("id") or "")
    site_id = make_site_id(council, source_id)

    road_name = str(props.get("STREET_NAME") or "UNKNOWN ROAD")
    between = props.get("COUNTER_LOCATION_BETWEEN") or ""
    description = str(between)

    d1_name = props.get("DIR1")
    d2_name = props.get("DIR2")
    d1_vol = safe_float(props.get("VOL1"))
    d2_vol = safe_float(props.get("VOL2"))

    aadt = safe_float(props.get("AADT")) or safe_float(props.get("AAWT"))
    if d1_vol is None and d2_vol is None and aadt is not None:
        d1_vol = aadt * 0.5
        d2_vol = aadt * 0.5
    elif d1_vol is not None and d2_vol is None:
        d2_vol = max((aadt - d1_vol), 0.0) if aadt is not None else d1_vol
    elif d2_vol is not None and d1_vol is None:
        d1_vol = max((aadt - d2_vol), 0.0) if aadt is not None else d2_vol

    am_hour = parse_hour_index(props.get("AM_FROM_HUR"))
    pm_hour = parse_hour_index(props.get("PM_FROM_HUR"))

    return DirectionalCounts(
        road_name=road_name,
        description=description,
        latitude=lat or 0.0,
        longitude=lon or 0.0,
        site_id=site_id,
        source_record_id=source_id,
        source_file=file_name,
        source_council=council,
        gazettal_direction_name=normalize_direction_name(str(d1_name) if d1_name else None, "GAZETTAL"),
        against_direction_name=normalize_direction_name(str(d2_name) if d2_name else None, "AGAINST GAZETTAL"),
        gazettal_daily_total=d1_vol,
        against_daily_total=d2_vol,
        am_peak_hour=am_hour,
        am_peak_vol=safe_float(props.get("AM_PEAK")),
        pm_peak_hour=pm_hour,
        pm_peak_vol=safe_float(props.get("PM_PEAK")),
    )


def extract_from_ipswich(props: Dict[str, Any], feature: Dict[str, Any], file_name: str) -> DirectionalCounts:
    lat, lon = get_coordinates(feature)
    council = slug_council_name(file_name)

    source_id = str(props.get("Id") or feature.get("id") or "")
    site_id = make_site_id(council, source_id)

    road_name = str(props.get("Road Name") or "UNKNOWN ROAD")
    description = str(props.get("Site Description") or "")

    adt = safe_float(props.get("Average Daily Traffic Adt Vehicles Per Day"))
    direction = props.get("Direction")

    am_peak_hour = parse_hour_index(props.get("Weekday Avg AM Peak Start Hour"))
    pm_peak_hour = parse_hour_index(props.get("Weekday Avg PM Peak Start Hour"))

    return DirectionalCounts(
        road_name=road_name,
        description=description,
        latitude=lat or 0.0,
        longitude=lon or 0.0,
        site_id=site_id,
        source_record_id=source_id,
        source_file=file_name,
        source_council=council,
        gazettal_direction_name=normalize_direction_name(str(direction) if direction else None, "GAZETTAL"),
        against_direction_name="AGAINST GAZETTAL",
        gazettal_daily_total=adt,
        against_daily_total=0.0,
        am_peak_hour=am_peak_hour,
        am_peak_vol=safe_float(props.get("Weekday Avg AM Peak Flow Vehicles Per Hour")),
        pm_peak_hour=pm_peak_hour,
        pm_peak_vol=safe_float(props.get("Weekday Avg PM Peak Flow Vehicles Per Hour")),
    )


def extract_from_toowoomba(props: Dict[str, Any], feature: Dict[str, Any], file_name: str) -> DirectionalCounts:
    lat, lon = get_coordinates(feature)
    council = slug_council_name(file_name)

    source_id = str(props.get("OBJECTID") or feature.get("id") or "")
    site_id = make_site_id(council, source_id)

    road_name = str(props.get("Road_Name") or "UNKNOWN ROAD")
    description = str(props.get("Local_Road_Alias") or "")

    adt = safe_float(props.get("ADT"))
    d1 = adt * 0.5 if adt is not None else None
    d2 = adt * 0.5 if adt is not None else None

    return DirectionalCounts(
        road_name=road_name,
        description=description,
        latitude=lat or 0.0,
        longitude=lon or 0.0,
        site_id=site_id,
        source_record_id=source_id,
        source_file=file_name,
        source_council=council,
        gazettal_direction_name="GAZETTAL",
        against_direction_name="AGAINST GAZETTAL",
        gazettal_daily_total=d1,
        against_daily_total=d2,
    )


def pick_extractor(file_name: str):
    stem = Path(file_name).stem.lower()
    if stem in {"brisbane", "goldcoast"}:
        return extract_from_brisbane_or_goldcoast
    if stem == "logan":
        return extract_from_logan
    if stem == "ipswich":
        return extract_from_ipswich
    if stem == "toowoomba":
        return extract_from_toowoomba
    return None


def force_peaks(hourly: List[float], am_hour: Optional[int], am_vol: Optional[float], pm_hour: Optional[int], pm_vol: Optional[float]) -> List[float]:
    result = hourly[:]

    def apply_peak(target_hour: Optional[int], target_vol: Optional[float]) -> None:
        if target_hour is None or target_vol is None:
            return
        if target_hour < 0 or target_hour > 23:
            return
        if target_vol < 0:
            return
        current = result[target_hour]
        if current <= 0:
            return
        factor = target_vol / current
        if factor <= 0:
            return
        result[target_hour] = target_vol

        # Rebalance remaining hours so daily total remains stable.
        original_total = sum(hourly)
        new_total = sum(result)
        remainder_old = original_total - current
        remainder_new = new_total - target_vol
        if remainder_old <= 0 or remainder_new <= 0:
            return
        scale = remainder_old / remainder_new
        for idx in range(24):
            if idx != target_hour:
                result[idx] *= scale

    apply_peak(am_hour, am_vol)
    apply_peak(pm_hour, pm_vol)
    return result


def daily_to_hourly(daily_total: Optional[float], profile: List[float]) -> List[float]:
    if daily_total is None or daily_total <= 0:
        return [0.0] * 24
    return [daily_total * p for p in profile]


def peak_hour_and_vol(hourly: List[float]) -> Tuple[str, float]:
    if not hourly:
        return "", 0.0
    idx = max(range(len(hourly)), key=lambda i: hourly[i])
    return HOUR_LABELS[idx], hourly[idx]


def build_rows_for_direction(
    base: DirectionalCounts,
    direction_label: str,
    daily_total: Optional[float],
    weekday_profile: List[float],
    weekend_profile: List[float],
    weekend_factor: float,
) -> List[Dict[str, Any]]:
    weekday_hourly = daily_to_hourly(daily_total, weekday_profile)

    # If source supplies AM/PM peak data, enforce those values while preserving total.
    weekday_hourly = force_peaks(
        weekday_hourly,
        base.am_peak_hour,
        base.am_peak_vol,
        base.pm_peak_hour,
        base.pm_peak_vol,
    )

    weekend_total = (daily_total * weekend_factor) if daily_total is not None else None
    weekend_hourly = daily_to_hourly(weekend_total, weekend_profile)

    peak_hour, peak_vol = peak_hour_and_vol(weekday_hourly)

    rows: List[Dict[str, Any]] = []
    for h in range(24):
        wd = weekday_hourly[h]
        we = weekend_hourly[h]
        proportional = (wd / daily_total) if (daily_total is not None and daily_total > 0) else 0.0

        am_hour_label = ""
        if base.am_peak_hour is not None and 0 <= base.am_peak_hour <= 23:
            am_hour_label = HOUR_LABELS[base.am_peak_hour]

        pm_hour_label = ""
        if base.pm_peak_hour is not None and 0 <= base.pm_peak_hour <= 23:
            pm_hour_label = HOUR_LABELS[base.pm_peak_hour]

        row: Dict[str, Any] = {
            "SITE_ID": base.site_id,
            "ROAD_NAME": base.road_name,
            "GAZETTAL_DIRECTION": direction_label,
            "LATITUDE": round(base.latitude, 10),
            "LONGITUDE": round(base.longitude, 10),
            "DESCRIPTION": base.description,
            "HOURS": HOUR_LABELS[h],
            "WEEKDAY_AVERAGE": int(round(wd)),
            "WEEKEND_AVERAGE": int(round(we)),
            "SOURCE_FILE": base.source_file,
            "SOURCE_COUNCIL": base.source_council,
            "SOURCE_RECORD_ID": base.source_record_id,
            "DAILY_DIRECTIONAL_TOTAL": 0 if daily_total is None else int(round(daily_total)),
            "PROPORTIONAL_VOLUME": round(proportional, 6),
            "DIRECTION_AM_PEAK_HOUR": am_hour_label,
            "DIRECTION_AM_PEAK_VOL": "" if base.am_peak_vol is None else int(round(base.am_peak_vol)),
            "DIRECTION_PM_PEAK_HOUR": pm_hour_label,
            "DIRECTION_PM_PEAK_VOL": "" if base.pm_peak_vol is None else int(round(base.pm_peak_vol)),
            "DIRECTION_DAILY_PEAK_HOUR": peak_hour,
            "DIRECTION_DAILY_PEAK_VOL": int(round(peak_vol)),
        }
        rows.append(row)

    return rows


def convert_feature(feature: Dict[str, Any], file_name: str, weekend_factor: float) -> List[Dict[str, Any]]:
    props = feature.get("properties") or {}
    extractor = pick_extractor(file_name)
    if extractor is None:
        return []

    base = extractor(props, feature, file_name)

    rows = []
    rows.extend(
        build_rows_for_direction(
            base,
            "GAZETTAL",
            base.gazettal_daily_total,
            normalize_profile(WEEKDAY_PROFILE),
            normalize_profile(WEEKEND_PROFILE),
            weekend_factor,
        )
    )
    rows.extend(
        build_rows_for_direction(
            base,
            "AGAINST GAZETTAL",
            base.against_daily_total,
            normalize_profile(WEEKDAY_PROFILE),
            normalize_profile(WEEKEND_PROFILE),
            weekend_factor,
        )
    )
    return rows


def rows_to_geojson(rows: List[Dict[str, Any]], fields: List[str]) -> Dict[str, Any]:
    features = []
    for idx, row in enumerate(rows, start=1):
        props = {k: row.get(k, "") for k in fields}
        feat = {
            "type": "Feature",
            "id": idx,
            "geometry": {
                "type": "Point",
                "coordinates": [row["LONGITUDE"], row["LATITUDE"], 0],
            },
            "properties": props,
        }
        features.append(feat)

    return {
        "type": "FeatureCollection",
        "features": features,
    }


def write_csv(path: Path, rows: List[Dict[str, Any]], fields: List[str], compress: bool = False) -> Path:
    if compress:
        out_path = path.with_suffix(".csv.gz")
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})
        with gzip.open(out_path, "wt", encoding="utf-8", compresslevel=9) as f:
            f.write(buf.getvalue())
        return out_path
    else:
        out_path = path.with_suffix(".csv")
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in fields})
        return out_path


def write_geojson_split(base_path: Path, rows: List[Dict[str, Any]], fields: List[str], max_mb: float) -> List[Path]:
    """Write GeoJSON file(s). If the estimated size exceeds max_mb, split into
    numbered chunks (_part1.geojson, _part2.geojson, ...), each a valid
    FeatureCollection that the traffic app can load independently."""
    max_bytes = max_mb * 1024 * 1024

    # Estimate rows-per-chunk using the first 200 rows as a sample.
    sample = rows[:200] if len(rows) >= 200 else rows
    sample_geojson = json.dumps(rows_to_geojson(sample, fields), separators=(",", ":"))
    bytes_per_row = len(sample_geojson.encode("utf-8")) / max(len(sample), 1)
    # Add 5% safety margin.
    chunk_size = max(1, int((max_bytes / bytes_per_row) * 0.95))

    written: List[Path] = []

    # If everything fits in one file, write normally.
    if len(rows) <= chunk_size:
        out_path = base_path.with_suffix(".geojson")
        geojson = rows_to_geojson(rows, fields)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(geojson, f, separators=(",", ":"))
        written.append(out_path)
        return written

    # Split into numbered parts.
    total_parts = math.ceil(len(rows) / chunk_size)
    for part in range(total_parts):
        chunk = rows[part * chunk_size : (part + 1) * chunk_size]
        out_path = base_path.parent / f"{base_path.stem}_part{part + 1}.geojson"
        geojson = rows_to_geojson(chunk, fields)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(geojson, f, separators=(",", ":"))
        written.append(out_path)

    return written


def write_outputs(
    base_path: Path,
    rows: List[Dict[str, Any]],
    fields: List[str],
    compress: bool = False,
    split_geojson_mb: float = 20.0,
) -> Tuple[Path, List[Path]]:
    out_csv = write_csv(base_path, rows, fields, compress=compress)
    if compress:
        return out_csv, []
    geojson_files = write_geojson_split(base_path, rows, fields, max_mb=split_geojson_mb)
    return out_csv, geojson_files


def load_geojson(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    args = parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir) if args.output_dir else input_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    include_files = [Path(name).name for name in args.include]
    compress = args.compress

    output_fields = TMR_REQUIRED_FIELDS + EXTRA_FIELDS if args.include_helper_fields else TMR_REQUIRED_FIELDS

    all_rows: List[Dict[str, Any]] = []
    rows_by_source: Dict[str, List[Dict[str, Any]]] = {}
    for file_name in include_files:
        path = input_dir / file_name
        if not path.exists():
            print(f"[WARN] Missing input file: {path}")
            continue

        data = load_geojson(path)
        features = data.get("features") or []
        before = len(all_rows)
        source_rows: List[Dict[str, Any]] = []
        for feature in features:
            converted = convert_feature(feature, file_name, args.weekend_factor)
            all_rows.extend(converted)
            source_rows.extend(converted)
        rows_by_source[Path(file_name).stem.lower()] = source_rows
        print(f"[OK] {file_name}: {len(features)} source records -> {len(all_rows) - before} hourly rows")

    split_mb = args.split_geojson_mb
    out_csv, out_geojsons = write_outputs(output_dir / args.output_prefix, all_rows, output_fields, compress=compress, split_geojson_mb=split_mb)

    if args.split_by_source:
        for source_name, source_rows in rows_by_source.items():
            src_base = output_dir / source_name
            src_csv, src_geojsons = write_outputs(src_base, source_rows, output_fields, compress=compress, split_geojson_mb=split_mb)
            print(f"[DONE] Source CSV: {src_csv}")
            for gj in src_geojsons:
                mb = round(gj.stat().st_size / 1024 / 1024, 1)
                print(f"[DONE] Source GeoJSON ({mb}MB): {gj.name}")

    ext = ".csv.gz" if compress else ".csv"
    print(f"[DONE] Wrote {len(all_rows)} rows | Fields: {', '.join(output_fields)}")
    print(f"[DONE] Combined {ext}: {out_csv}")
    for gj in out_geojsons:
        mb = round(gj.stat().st_size / 1024 / 1024, 1)
        print(f"[DONE] Combined GeoJSON ({mb}MB): {gj.name}")


if __name__ == "__main__":
    main()
