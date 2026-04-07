from __future__ import annotations

import json
import math
import re
from pathlib import Path


DATASET_PATH = Path(__file__).resolve().parents[1] / 'goldcoast.geojson'
ANCHOR_LAT = -28.0167
ANCHOR_LON = 153.4000

# Austroads-aligned commuter profiles with distinct directional peaks.
AM_BIAS_TEMPLATE = [
    0.009, 0.007, 0.005, 0.005, 0.011, 0.028, 0.072, 0.090, 0.098, 0.070,
    0.054, 0.048, 0.046, 0.048, 0.054, 0.066, 0.074, 0.078, 0.052, 0.034,
    0.024, 0.018, 0.013, 0.010,
]
PM_BIAS_TEMPLATE = [
    0.009, 0.007, 0.005, 0.005, 0.010, 0.024, 0.056, 0.070, 0.076, 0.064,
    0.054, 0.050, 0.050, 0.054, 0.062, 0.078, 0.098, 0.108, 0.062, 0.038,
    0.026, 0.019, 0.014, 0.011,
]
AM_PEAK_HOUR = max(range(24), key=lambda idx: AM_BIAS_TEMPLATE[idx])
PM_PEAK_HOUR = max(range(24), key=lambda idx: PM_BIAS_TEMPLATE[idx])


def parse_direction_total(raw_value: object) -> int:
    if raw_value is None:
        return 0
    match = re.search(r'(-?\d+(?:\.\d+)?)\s*$', str(raw_value).strip())
    if not match:
        return 0
    return max(0, int(round(float(match.group(1)))))


def get_direction_bearing(raw_label: object) -> int | None:
    value = str(raw_label or '').strip().lower()
    if not value or value in {'none', 'n/a', 'na'}:
        return None
    if re.search(r'(northwestbound|north-westbound|north westbound|\bnw\b)', value):
        return 315
    if re.search(r'(northeastbound|north-eastbound|north eastbound|\bne\b)', value):
        return 45
    if re.search(r'(southwestbound|south-westbound|south westbound|\bsw\b)', value):
        return 225
    if re.search(r'(southeastbound|south-eastbound|south eastbound|\bse\b)', value):
        return 135
    if re.search(r'(northbound|\bnorth\b|\bnb\b|\bnthbound\b|\bnorth\d+)', value):
        return 0
    if re.search(r'(eastbound|\beast\b|\beb\b)', value):
        return 90
    if re.search(r'(southbound|\bsouth\b|\bsb\b)', value):
        return 180
    if re.search(r'(westbound|\bwest\b|\bwb\b|\bwes\b)', value):
        return 270
    return None


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_lon = math.radians(lon2 - lon1)
    y = math.sin(delta_lon) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lon)
    angle = math.degrees(math.atan2(y, x))
    return (angle + 360.0) % 360.0


def normalize_angle_diff(angle_a: float, angle_b: float) -> float:
    return abs(((angle_a - angle_b + 540.0) % 360.0) - 180.0)


def choose_am_direction(feature: dict) -> int:
    props = feature.get('properties') or {}
    geometry = feature.get('geometry') or {}
    coords = geometry.get('coordinates') or []
    lon = float(coords[0]) if len(coords) >= 2 else None
    lat = float(coords[1]) if len(coords) >= 2 else None

    d1_total = parse_direction_total(props.get('DIRECTION_1A'))
    d2_total = parse_direction_total(props.get('DIRECTION_2A'))
    d1_bearing = get_direction_bearing(props.get('DIRECTION_1A'))
    d2_bearing = get_direction_bearing(props.get('DIRECTION_2A'))

    if lat is not None and lon is not None:
        to_anchor = bearing_deg(lat, lon, ANCHOR_LAT, ANCHOR_LON)
        if d1_bearing is not None and d2_bearing is not None:
            d1_diff = normalize_angle_diff(d1_bearing, to_anchor)
            d2_diff = normalize_angle_diff(d2_bearing, to_anchor)
            if abs(d1_diff - d2_diff) >= 5.0:
                return 1 if d1_diff < d2_diff else 2
        elif d1_bearing is not None:
            return 1 if normalize_angle_diff(d1_bearing, to_anchor) <= 90.0 else 2
        elif d2_bearing is not None:
            return 2 if normalize_angle_diff(d2_bearing, to_anchor) <= 90.0 else 1

    total = d1_total + d2_total
    if total > 0:
        d1_share = d1_total / total
        if d1_share >= 0.55:
            return 1
        if d1_share <= 0.45:
            return 2

    if d1_bearing is not None and d2_bearing is None:
        return 1
    if d2_bearing is not None and d1_bearing is None:
        return 2
    return 1 if d1_total >= d2_total else 2


def normalize_profile(template: list[float]) -> list[float]:
    total = sum(max(0.0, float(value)) for value in template)
    if total <= 0:
        return [1.0 / 24.0] * 24
    return [max(0.0, float(value)) / total for value in template]


def enforce_unique_peak(hourly: list[int], peak_hour: int, shares: list[float]) -> list[int]:
    if not hourly or sum(hourly) <= 0:
        return hourly
    while True:
        other_max = max(hourly[idx] for idx in range(24) if idx != peak_hour)
        if hourly[peak_hour] > other_max:
            return hourly
        donors = [
            idx for idx in range(24)
            if idx != peak_hour and hourly[idx] > 0
        ]
        if not donors:
            return hourly
        donors.sort(key=lambda idx: (hourly[idx], shares[idx], -abs(idx - peak_hour)), reverse=True)
        donor = donors[0]
        hourly[donor] -= 1
        hourly[peak_hour] += 1


def allocate_hourly(total: int, template: list[float], peak_hour: int) -> list[int]:
    total = max(0, int(round(total)))
    if total <= 0:
        return [0] * 24

    shares = normalize_profile(template)
    raw = [total * share for share in shares]
    hourly = [int(math.floor(value)) for value in raw]
    remainder = total - sum(hourly)

    if remainder > 0:
        ranking = sorted(
            range(24),
            key=lambda idx: (raw[idx] - hourly[idx], shares[idx], -abs(idx - peak_hour)),
            reverse=True,
        )
        for idx in ranking[:remainder]:
            hourly[idx] += 1

    return enforce_unique_peak(hourly, peak_hour, shares)


def main() -> None:
    payload = json.loads(DATASET_PATH.read_text(encoding='utf-8'))
    features = payload.get('features') or []

    for feature in features:
        props = feature.setdefault('properties', {})
        d1_total = parse_direction_total(props.get('DIRECTION_1A'))
        d2_total = parse_direction_total(props.get('DIRECTION_2A'))
        am_direction = choose_am_direction(feature)

        d1_template = AM_BIAS_TEMPLATE if am_direction == 1 else PM_BIAS_TEMPLATE
        d2_template = PM_BIAS_TEMPLATE if am_direction == 1 else AM_BIAS_TEMPLATE
        d1_peak_hour = AM_PEAK_HOUR if am_direction == 1 else PM_PEAK_HOUR
        d2_peak_hour = PM_PEAK_HOUR if am_direction == 1 else AM_PEAK_HOUR

        d1_hourly = allocate_hourly(d1_total, d1_template, d1_peak_hour)
        d2_hourly = allocate_hourly(d2_total, d2_template, d2_peak_hour)

        for hour, value in enumerate(d1_hourly):
            props[f'd1_h{hour:02d}'] = value
        for hour, value in enumerate(d2_hourly):
            props[f'd2_h{hour:02d}'] = value

    DATASET_PATH.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f'Updated {len(features)} Gold Coast features with directional hourly profiles.')


if __name__ == '__main__':
    main()