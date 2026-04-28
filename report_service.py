from __future__ import annotations

import json
import uuid
import base64
import re
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

# Load .env file if present (for GEMINI_API_KEY etc.)
_env_path = Path(__file__).with_name(".env")
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#"):
            continue
        if "=" in _line:
            _key, _, _val = _line.partition("=")
            _key = _key.strip()
            _val = _val.strip().strip("\"'")
            if _key and _key not in os.environ:
                os.environ[_key] = _val

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel


app = FastAPI(title="TIA Python Report Service", version="1.0.0")


def _parse_allowed_origins_from_env() -> list[str]:
  raw = os.environ.get("REPORT_ALLOWED_ORIGINS", "")
  if not raw.strip():
    return []
  origins = [item.strip() for item in raw.split(",") if item.strip()]
  return origins


ALLOWED_ORIGINS = _parse_allowed_origins_from_env()
ALLOWED_ORIGIN_REGEX = os.environ.get(
  "REPORT_ALLOWED_ORIGIN_REGEX",
  r"^https://[a-z0-9-]+\.github\.io$|^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
)
MAX_REQUEST_BODY_BYTES = max(100_000, int(os.environ.get("REPORT_MAX_REQUEST_BYTES", "12000000")))
MAX_DRAFTS = max(10, int(os.environ.get("REPORT_MAX_DRAFTS", "200")))
DRAFT_TTL_HOURS = max(1, int(os.environ.get("REPORT_DRAFT_TTL_HOURS", "12")))

app.add_middleware(
    CORSMiddleware,
  allow_origins=ALLOWED_ORIGINS,
  allow_origin_regex=ALLOWED_ORIGIN_REGEX,
  allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_private_network_access_headers(request: Request, call_next):
  content_length = request.headers.get("content-length")
  if content_length:
    try:
      content_length_value = int(content_length)
      if content_length_value > MAX_REQUEST_BODY_BYTES:
        max_mb = MAX_REQUEST_BODY_BYTES / (1024 * 1024)
        actual_mb = content_length_value / (1024 * 1024)
        raise HTTPException(
          status_code=413,
          detail=(
            f"Request body too large ({actual_mb:.2f} MB). "
            f"Server limit is {max_mb:.2f} MB. "
            "Set REPORT_MAX_REQUEST_BYTES to increase the limit if needed."
          ),
        )
    except ValueError:
      raise HTTPException(status_code=400, detail="Invalid Content-Length header")

  response = await call_next(request)
  # Required for browser Private Network Access preflight when calling localhost
  # from a secure origin (for example, GitHub Pages over HTTPS).
  response.headers["Access-Control-Allow-Private-Network"] = "true"
  response.headers["Vary"] = "Origin, Access-Control-Request-Method, Access-Control-Request-Headers, Access-Control-Request-Private-Network"
  response.headers["X-Content-Type-Options"] = "nosniff"
  response.headers["X-Frame-Options"] = "DENY"
  response.headers["Referrer-Policy"] = "no-referrer"
  response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

  # Keep CSP compatible with the inline editor while limiting remote execution vectors.
  content_type = str(response.headers.get("content-type", "")).lower()
  if "text/html" in content_type:
    response.headers["Content-Security-Policy"] = (
      "default-src 'self'; "
      "img-src 'self' data:; "
      "style-src 'self' 'unsafe-inline'; "
      "script-src 'self' 'unsafe-inline'; "
      "font-src 'self' data:; "
      "connect-src 'self'; "
      "frame-ancestors 'none'; "
      "base-uri 'self'; "
      "form-action 'self'"
    )
  return response


class DraftRequest(BaseModel):
    title: str = "TIA Report"
    payload: dict[str, Any]


DRAFTS: dict[str, dict[str, Any]] = {}


def _prune_drafts(now: datetime | None = None) -> None:
  if not DRAFTS:
    return

  now_dt = now or datetime.utcnow()
  cutoff = now_dt - timedelta(hours=DRAFT_TTL_HOURS)
  stale_ids: list[str] = []

  for draft_id, item in list(DRAFTS.items()):
    created_epoch = item.get("created_epoch")
    if isinstance(created_epoch, (int, float)):
      created_at = datetime.utcfromtimestamp(created_epoch)
    else:
      created_at_text = str(item.get("created_at") or "").replace("Z", "")
      try:
        created_at = datetime.fromisoformat(created_at_text)
      except ValueError:
        created_at = now_dt
    if created_at < cutoff:
      stale_ids.append(draft_id)

  for stale_id in stale_ids:
    DRAFTS.pop(stale_id, None)

  if len(DRAFTS) > MAX_DRAFTS:
    oldest_first = sorted(
      DRAFTS.items(),
      key=lambda kv: float(kv[1].get("created_epoch") or 0),
    )
    to_drop = len(DRAFTS) - MAX_DRAFTS
    for draft_id, _ in oldest_first[:to_drop]:
      DRAFTS.pop(draft_id, None)


def _load_logo_data_url() -> str:
    logo_path = Path(__file__).with_name("logo.jpeg")
    if not logo_path.exists():
        return ""
    try:
        raw = logo_path.read_bytes()
        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"
    except Exception:
        return ""


def _safe_text(value: Any, fallback: str = "-") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def _escape(value: Any, fallback: str = "-") -> str:
    return escape(_safe_text(value, fallback))


def _to_float(value: Any) -> float | None:
  if value is None:
    return None
  if isinstance(value, (int, float)):
    return float(value)
  text = str(value).replace(",", "").strip()
  if not text:
    return None
  match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
  if not match:
    return None
  try:
    return float(match.group(0))
  except Exception:
    return None


def _to_bool(value: Any) -> bool:
  if isinstance(value, bool):
    return value
  return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _format_number(value: Any, decimals: int = 0, fallback: str = "-") -> str:
  num = _to_float(value)
  if num is None:
    return fallback
  if decimals <= 0:
    return f"{round(num):,}"
  return f"{num:,.{decimals}f}"


def _format_au_date(value: Any, fallback: str | None = None) -> str:
  text = _safe_text(value, "")
  if not text:
    return fallback or datetime.now().strftime("%d/%m/%Y")

  parse_candidates = [text]
  if "T" in text:
    parse_candidates.append(text.split("T", 1)[0])

  for candidate in parse_candidates:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%B %d, %Y", "%d %B %Y"):
      try:
        return datetime.strptime(candidate, fmt).strftime("%d/%m/%Y")
      except ValueError:
        continue
    try:
      return datetime.fromisoformat(candidate).strftime("%d/%m/%Y")
    except ValueError:
      continue

  return text


def _build_report_context(payload: dict[str, Any]) -> dict[str, Any]:
  project = payload.get("project", {}) if isinstance(payload.get("project"), dict) else {}
  inputs = payload.get("inputs", {}) if isinstance(payload.get("inputs"), dict) else {}
  results = payload.get("results", {}) if isinstance(payload.get("results"), dict) else {}
  raw = payload.get("raw_js_results", {}) if isinstance(payload.get("raw_js_results"), dict) else {}
  notes = payload.get("notes", []) if isinstance(payload.get("notes"), list) else []
  site_details = payload.get("selected_site_details", {}) if isinstance(payload.get("selected_site_details"), dict) else {}
  peak_diagnostics = payload.get("peak_diagnostics", {}) if isinstance(payload.get("peak_diagnostics"), dict) else {}

  d1_vadt = _to_float(
    inputs.get("d1_vadt_opening_year") or inputs.get("d1_vadt") or raw.get("d1_vadt")
  )
  d2_vadt = _to_float(
    inputs.get("d2_vadt_opening_year") or inputs.get("d2_vadt") or raw.get("d2_vadt")
  )
  total_vadt = _to_float(
    inputs.get("opening_year_aadt") or inputs.get("aadt") or raw.get("vadt")
  )
  base_year_aadt = _to_float(inputs.get("base_year_aadt") or inputs.get("aadt") or raw.get("vadt"))
  opening_year = _safe_text(inputs.get("opening_year") or raw.get("opening_year"), "")
  growth_rate = _to_float(inputs.get("growth_rate_percent") or raw.get("growth_rate_percent"))
  worst_vcr = _to_float(results.get("worst_vcr") or raw.get("worst_vcr"))
  queue_peak = _to_float(results.get("queue_peak_m") or raw.get("queue_peak_m"))
  d1_queue_peak = _to_float(raw.get("d1_queue_peak_m"))
  d2_queue_peak = _to_float(raw.get("d2_queue_peak_m"))

  direction_total = (d1_vadt or 0) + (d2_vadt or 0)
  d1_pct = ((d1_vadt or 0) / direction_total * 100) if direction_total > 0 else None
  d2_pct = ((d2_vadt or 0) / direction_total * 100) if direction_total > 0 else None

  return {
    "project_name": _safe_text(project.get("name"), "Traffic Impact Assessment"),
    "location": _safe_text(project.get("location"), "Site location not specified"),
    "report_date": _format_au_date(project.get("report_date")),
    "prepared_by": _safe_text(project.get("prepared_by"), "Planner's Name"),
    "cc_number": _safe_text(project.get("cc_number"), "CC0000"),
    "road_mode": _safe_text(inputs.get("road_operation_mode"), "TWO-WAY"),
    "base_year": _safe_text(inputs.get("base_year") or raw.get("base_year"), "Current year"),
    "opening_year": opening_year,
    "total_vadt": total_vadt,
    "base_year_aadt": base_year_aadt,
    "d1_vadt": d1_vadt,
    "d2_vadt": d2_vadt,
    "d1_pct": d1_pct,
    "d2_pct": d2_pct,
    "growth_rate": growth_rate,
    "worst_vcr": worst_vcr,
    "queue_peak": queue_peak,
    "d1_queue_peak": d1_queue_peak,
    "d2_queue_peak": d2_queue_peak,
    "los": _safe_text(results.get("los"), "-"),
    "detour_recommended": _to_bool(results.get("detour_recommended")),
    "notes": [str(note).strip() for note in notes if str(note).strip()],
    "selected_site_details": {
      _safe_text(key, ""): _safe_text(value, "")
      for key, value in site_details.items()
      if _safe_text(key, "") and _safe_text(value, "")
    },
    "peak_diagnostics": {
      _safe_text(key, ""): value
      for key, value in peak_diagnostics.items()
      if _safe_text(key, "")
    },
  }


def _normalize_title_key(value: Any) -> str:
  text = _safe_text(value, "").lower()
  text = text.replace("direction 1", "d1")
  text = text.replace("direction 2", "d2")
  text = text.replace("directional", "direction")
  text = re.sub(r"[^a-z0-9]+", " ", text)
  return re.sub(r"\s+", " ", text).strip()


def _title_tokens(value: Any) -> set[str]:
  ignored = {
    "the", "and", "for", "with", "from", "table", "chart", "analysis",
    "summary", "detailed", "traffic", "results", "report",
  }
  return {token for token in _normalize_title_key(value).split() if token and token not in ignored}


def _mapping_to_table(title: str, data: Any, key_label: str, value_label: str, table_id: str = "") -> dict[str, Any]:
  if not isinstance(data, dict):
    return {"table_id": table_id, "title": title, "columns": [key_label, value_label], "rows": []}
  rows = [[str(key).replace("_", " ").title(), _safe_text(value)] for key, value in data.items()]
  return {"table_id": table_id, "title": title, "columns": [key_label, value_label], "rows": rows}


def _build_report_tables(payload: dict[str, Any]) -> list[dict[str, Any]]:
  inputs = payload.get("inputs", {}) if isinstance(payload.get("inputs"), dict) else {}
  results = payload.get("results", {}) if isinstance(payload.get("results"), dict) else {}
  payload_tables = payload.get("tables", []) if isinstance(payload.get("tables"), list) else []

  tables: list[dict[str, Any]] = []
  if inputs:
    tables.append(_mapping_to_table("Analysis Parameters", inputs, "Parameter", "Value", "analysis_parameters"))
  if results:
    tables.append(_mapping_to_table("Summary of Computed Results", results, "Metric", "Value", "summary_computed_results"))
  tables.extend(table for table in payload_tables if isinstance(table, dict))
  return tables


def _analyze_table_data(table_data: dict[str, Any]) -> dict[str, Any]:
  columns = table_data.get("columns", []) if isinstance(table_data.get("columns"), list) else []
  rows = table_data.get("rows", []) if isinstance(table_data.get("rows"), list) else []

  cleaned_rows: list[list[str]] = []
  numeric_cells: list[tuple[float, str, str]] = []
  first_column_labels: list[str] = []

  for row in rows:
    if not isinstance(row, list) or not row:
      continue
    cleaned_row = [_safe_text(cell, "-") for cell in row]
    cleaned_rows.append(cleaned_row)
    if cleaned_row and cleaned_row[0] not in {"-", ""}:
      first_column_labels.append(cleaned_row[0])

    for idx, cell in enumerate(cleaned_row):
      if idx == 0:
        continue
      num = _to_float(cell)
      if num is None:
        continue
      row_label = cleaned_row[0] if cleaned_row else f"Row {len(cleaned_rows)}"
      column_label = _safe_text(columns[idx], f"Column {idx + 1}") if idx < len(columns) else f"Column {idx + 1}"
      numeric_cells.append((num, row_label, column_label))

  top_numeric = sorted(numeric_cells, key=lambda item: item[0], reverse=True)[:3]
  bottom_numeric = sorted(numeric_cells, key=lambda item: item[0])[:2]
  distinct_labels = list(dict.fromkeys(first_column_labels))

  return {
    "title": _safe_text(table_data.get("title"), "Untitled Table"),
    "columns": columns,
    "rows": cleaned_rows,
    "row_count": len(cleaned_rows),
    "column_count": len(columns) if columns else max((len(row) for row in cleaned_rows), default=0),
    "numeric_count": len(numeric_cells),
    "numeric_min": min((cell[0] for cell in numeric_cells), default=None),
    "numeric_max": max((cell[0] for cell in numeric_cells), default=None),
    "top_numeric": top_numeric,
    "bottom_numeric": bottom_numeric,
    "labels": distinct_labels[:6],
    "sample_rows": cleaned_rows[:6],
  }


def _summarize_table_for_prompt(table_data: dict[str, Any]) -> dict[str, Any]:
  analysis = _analyze_table_data(table_data)
  top_cells = [
    {
      "value": value,
      "row_label": row_label,
      "column_label": column_label,
    }
    for value, row_label, column_label in analysis["top_numeric"]
  ]

  return {
    "table_id": _safe_text(table_data.get("table_id"), ""),
    "title": analysis["title"],
    "columns": analysis["columns"][:16],
    "row_count": analysis["row_count"],
    "column_count": analysis["column_count"],
    "numeric_count": analysis["numeric_count"],
    "numeric_min": analysis["numeric_min"],
    "numeric_max": analysis["numeric_max"],
    "labels": analysis["labels"],
    "sample_rows": analysis["sample_rows"][:12],
    "top_numeric_cells": top_cells,
    "bottom_numeric_cells": [
      {"value": v, "row_label": r, "column_label": c}
      for v, r, c in analysis["bottom_numeric"]
    ],
  }


def _build_table_scenario_text(title: str) -> str:
  title_key = _normalize_title_key(title)
  if "summary of computed results" in title_key:
    return (
      "These controlling outputs typically emerge when directional demand, lane closure constraints, and work-zone control delays align during the peak operating period. "
      "A high queue or V/C outcome usually signals that short disturbances, heavy-vehicle platoons, or an extended stop-go cycle can rapidly push the corridor into unstable operation."
    )
  if "analysis parameters" in title_key or "input" in title_key:
    return (
      "This parameter set becomes critical when the field operating conditions differ from the assumed road mode, growth rate, or lane availability. "
      "Any departure between assumed inputs and actual site conditions should be checked first because it can materially shift queue and V/C outcomes."
    )
  if "queue" in title_key and "hourly" in title_key:
    return (
      "Hourly queue stress is usually concentrated in the hours where arrival demand exceeds discharge opportunities during lane closure or stop-go control. "
      "The controlling condition often occurs when queue storage from one hour is not fully recovered before the next peak hour begins."
    )
  if "queue" in title_key:
    return (
      "Elevated queue conditions commonly occur when temporary traffic control phases, reduced lane capacity, or strong directional peaks limit discharge opportunities. "
      "This is the table to review when assessing whether queue storage may extend into upstream accesses, intersections, or sensitive frontages."
    )
  if "vcr" in title_key or "los" in title_key:
    return (
      "High V/C conditions occur when effective work-zone capacity is constrained by lane closure, heavy vehicles, turning friction, or peak directional loading. "
      "Values near or above practical capacity indicate the network has limited resilience, so relatively minor disturbances can trigger disproportionate delay and recovery time."
    )
  if "detour" in title_key or "diversion" in title_key:
    return (
      "Detour pressure becomes material when diverted traffic is reassigned to roads with limited spare capacity during the same peak period as the primary corridor impact. "
      "These conditions are most important where side-road intersections, local access, or school and freight activity are already operating close to their normal thresholds."
    )
  if "grouped directional summary" in title_key or "directional" in title_key:
    return (
      "Directional imbalance becomes important when one travel direction attracts a dominant commuter, freight, or school-period demand profile. "
      "That imbalance often explains why one carriageway controls queue and V/C risk even though the combined daily volume appears reasonable at corridor level."
    )
  return (
    "The conditions reflected in this table usually become important when multiple moderate effects combine within the same operating window. "
    "Review the controlling rows and periods together rather than in isolation so the mitigation response remains aligned with the actual operational trigger."
  )


def _build_fallback_table_analysis(table_data: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, str]:
  analysis = _analyze_table_data(table_data)
  title = analysis["title"]
  title_key = _normalize_title_key(title)
  ctx = _build_report_context(payload or {})
  site_details = ctx.get("selected_site_details", {}) if isinstance(ctx.get("selected_site_details"), dict) else {}
  peak = ctx.get("peak_diagnostics", {}) if isinstance(ctx.get("peak_diagnostics"), dict) else {}

  if "analysis parameters" in title_key:
    site_bits = []
    if site_details.get("site_id"):
      site_bits.append(f"Site ID {site_details['site_id']}")
    if site_details.get("road_name"):
      site_bits.append(site_details["road_name"])
    if site_details.get("count_year"):
      site_bits.append(f"count year {site_details['count_year']}")

    applied_hv_rt = []
    if site_details.get("applied_d1_hv_percent"):
      applied_hv_rt.append(f"D1 HV {site_details['applied_d1_hv_percent']}%")
    if site_details.get("applied_d2_hv_percent"):
      applied_hv_rt.append(f"D2 HV {site_details['applied_d2_hv_percent']}%")
    if site_details.get("applied_d1_rt_percent"):
      applied_hv_rt.append(f"D1 RT {site_details['applied_d1_rt_percent']}%")
    if site_details.get("applied_d2_rt_percent"):
      applied_hv_rt.append(f"D2 RT {site_details['applied_d2_rt_percent']}%")

    summary_parts = [
      f"Analysis parameters capture the selected site, growth, and directional demand assumptions used for the opening-year assessment."
    ]
    if site_bits:
      summary_parts.append(f"The assessment is anchored to {'; '.join(site_bits)}.")
    if site_details.get("site_hv_percent"):
      summary_parts.append(f"The selected site reports HV content of {site_details['site_hv_percent']}.")
    if applied_hv_rt:
      summary_parts.append(f"Applied heavy-vehicle and rigid-truck inputs are {', '.join(applied_hv_rt)}.")
    if site_details.get("google_maps_url"):
      summary_parts.append("A Google Maps reference is included in the Selected Site Details section for field verification.")

    return {
      "title": title,
      "summary": " ".join(summary_parts),
      "scenario": (
        "These inputs should be checked first during review because any change to the selected site source data, opening year, growth rate, HV share, or RT share will flow directly through the hourly profile, queue, and V/C outputs."
      ),
      "chart_caption": "Current selected-site hourly traffic profile showing the base directional pattern used for peak-hour interpretation.",
    }

  if "hourly queue" in title_key:
    wait_minutes = _format_number(peak.get("queue_wait_minutes"), 0, "2")
    peak_time = _safe_text(peak.get("queue_peak_time"), "the controlling hour")
    peak_direction = _safe_text(peak.get("queue_peak_direction"), "the controlling direction")
    peak_value = _format_number(peak.get("queue_peak_value_m"), 0, "-")
    return {
      "title": title,
      "summary": (
        f"{title} sets out the hourly queue result for the selected {wait_minutes}-minute wait criterion. "
        f"The controlling queue occurs around {peak_time} on {peak_direction}, where the peak queue reaches approximately {peak_value} m."
      ),
      "scenario": (
        "Review this section to confirm when queue storage becomes critical, whether the queue is isolated to one direction, and whether the selected wait-time assumption is appropriate for site operations and stakeholder expectations."
      ),
      "chart_caption": (
        f"Hourly queue plot using the selected {wait_minutes}-minute wait assumption. The controlling queue occurs around {peak_time} on {peak_direction}."
      ),
    }

  if "hourly vcr" in title_key or ("vcr" in title_key and "hourly" in title_key):
    peak_time = _safe_text(peak.get("los_peak_time"), "the controlling hour")
    peak_direction = _safe_text(peak.get("los_peak_direction"), "the controlling direction")
    peak_vcr = _format_number(peak.get("worst_hourly_vcr"), 2, "-")
    peak_los = _safe_text(peak.get("worst_hourly_los"), "-")
    return {
      "title": title,
      "summary": (
        f"{title} tracks the hourly work-zone V/C profile and associated LOS. "
        f"The controlling LOS condition occurs around {peak_time} on {peak_direction}, where the worst hourly VCR reaches {peak_vcr} (LOS {peak_los})."
      ),
      "scenario": (
        "This section shows when capacity stress becomes most acute through the day. Use it to identify whether mitigation is needed only in short peak windows or whether the corridor remains sensitive across multiple hours."
      ),
      "chart_caption": (
        f"Hourly V/C plot highlighting the controlling hour around {peak_time} on {peak_direction}, where the worst modeled condition reaches VCR {peak_vcr} (LOS {peak_los})."
      ),
    }

  summary_parts = []
  title_lower = title_key

  # Build a more readable, natural-language fallback summary
  if "peak hour" in title_lower and "hourly" in title_lower:
    # Hourly peak hour analysis tables
    if analysis["top_numeric"]:
      top_value, row_label, column_label = analysis["top_numeric"][0]
      summary_parts.append(
        f"{title} presents {analysis['row_count']} row(s) across {analysis['column_count']} column(s)."
      )
      if analysis["labels"]:
        summary_parts.append(f"The leading labels cover {', '.join(analysis['labels'][:4])}.")
      if analysis["numeric_count"]:
        summary_parts.append(
          f"Reported numeric values range from {_format_number(analysis['numeric_min'], 2)} to {_format_number(analysis['numeric_max'], 2)}."
        )
      summary_parts.append(
        f"The strongest reported value is {_format_number(top_value, 2)} for {row_label} in {column_label}."
      )
    else:
      summary_parts.append(f"{title} presents {analysis['row_count']} row(s) across {analysis['column_count']} column(s).")
  elif "grouped" in title_lower and "direction" in title_lower:
    summary_parts.append(
      f"{title} presents {analysis['row_count']} row(s) across {analysis['column_count']} column(s)."
    )
    if analysis["labels"]:
      summary_parts.append(f"The leading labels cover {', '.join(analysis['labels'][:4])}.")
    if analysis["numeric_count"]:
      summary_parts.append(
        f"Reported numeric values range from {_format_number(analysis['numeric_min'], 2)} to {_format_number(analysis['numeric_max'], 2)}."
      )
    if analysis["top_numeric"]:
      top_value, row_label, column_label = analysis["top_numeric"][0]
      summary_parts.append(
        f"The strongest reported value is {_format_number(top_value, 2)} for {row_label} in {column_label}."
      )
  else:
    summary_parts.append(
      f"{title} presents {analysis['row_count']} row(s) across {analysis['column_count']} column(s)."
    )
    if analysis["labels"]:
      summary_parts.append(f"The leading labels cover {', '.join(analysis['labels'][:4])}.")
    if analysis["numeric_count"]:
      summary_parts.append(
        f"Reported numeric values range from {_format_number(analysis['numeric_min'], 2)} to {_format_number(analysis['numeric_max'], 2)}."
      )
    if analysis["top_numeric"]:
      top_value, row_label, column_label = analysis["top_numeric"][0]
      summary_parts.append(
        f"The strongest reported value is {_format_number(top_value, 2)} for {row_label} in {column_label}."
      )

  chart_caption = (
    f"This chart highlights the key pattern for {title.lower()} so the controlling periods can be identified before reviewing the detailed table below."
  )

  return {
    "title": title,
    "summary": " ".join(summary_parts),
    "scenario": _build_table_scenario_text(title),
    "chart_caption": chart_caption,
  }


def _build_fallback_professional_commentary(
  payload: dict[str, Any],
  table_analyses: list[dict[str, str]],
) -> dict[str, list[str]]:
  ctx = _build_report_context(payload)
  commentary: list[str] = []

  traffic_statement = []
  if ctx["total_vadt"] is not None:
    traffic_statement.append(f"approximately {_format_number(ctx['total_vadt'])} vehicles per day")
  if ctx["growth_rate"] is not None:
    traffic_statement.append(f"a growth allowance of {_format_number(ctx['growth_rate'], 2)}% per annum")
  traffic_suffix = " with " + " and ".join(traffic_statement) if traffic_statement else ""

  commentary.append(
    f"The assessment for {ctx['project_name']} at {ctx['location']} indicates that the modeled road network should be interpreted as a constrained work-stage environment rather than a normal operating condition{traffic_suffix}. "
    "The reported outputs are therefore most useful as a screening tool for operational risk, queue storage exposure, and the timing of mitigation triggers."
  )

  performance_bits: list[str] = []
  if ctx["worst_vcr"] is not None:
    performance_bits.append(f"a worst V/C ratio of {_format_number(ctx['worst_vcr'], 2)} corresponding to LOS {ctx['los']}")
  if ctx["queue_peak"] is not None:
    performance_bits.append(f"a peak queue on the order of {_format_number(ctx['queue_peak'])} m")
  if performance_bits:
    commentary.append(
      "Operationally, the model points to " + " and ".join(performance_bits) + ". "
      "These values suggest that traffic performance is being controlled by a limited number of peak-period constraints, so site management should focus on those controlling periods rather than average daily conditions."
    )

  commentary.append(
    "From an engineering perspective, mitigation should prioritise maintaining discharge opportunities, reducing unnecessary blockage time, and protecting upstream intersections and accesses from queue spillback. "
    "Where detour planning is triggered, the diversion strategy should be treated as an operational management measure with active monitoring rather than a one-off desktop assumption."
  )

  conclusion_points = [
    "The controlling risk is concentrated in peak operating periods rather than uniformly across the day.",
    "Queue and V/C results should be used to set monitoring triggers, traffic controller response actions, and escalation points for temporary traffic management.",
    "Any material change in closure duration, lane availability, or directional demand should prompt the assessment to be refreshed before implementation.",
  ]

  if ctx["detour_recommended"]:
    conclusion_points.insert(
      0,
      "Detour planning should be retained as an active mitigation measure because the modeled condition indicates insufficient resilience under the controlling scenario.",
    )
  else:
    conclusion_points.insert(
      0,
      "A full detour response is not automatically required by the current model, but field observation and staged contingency planning remain appropriate.",
    )

  if table_analyses:
    conclusion_points.append(
      f"The detailed table set has been interpreted together with {len(table_analyses)} supporting result table(s) so that mitigation can be tied to the specific controlling scenario."
    )

  return {
    "professional_commentary_paragraphs": commentary,
    "conclusion_points": conclusion_points[:5],
  }


def _build_fallback_executive_paragraphs(payload: dict[str, Any]) -> list[str]:
  ctx = _build_report_context(payload)
  paragraphs: list[str] = []
  paragraphs.append(
    f"This Traffic Impact Assessment relates to {ctx['project_name']} at {ctx['location']}. The assessment has been prepared for {ctx['report_date']} using the current {ctx['road_mode']} operating configuration and focuses on queue performance, directional demand balance, and whether mitigation or diversion is warranted under the modeled condition."
  )

  traffic_bits: list[str] = []
  year_tag = f" at opening year ({ctx['opening_year']})" if ctx.get("opening_year") else " at opening year"
  if ctx["total_vadt"] is not None:
    traffic_bits.append(f"the modeled daily traffic volume is approximately {_format_number(ctx['total_vadt'])} vehicles per day{year_tag}")
  if ctx["d1_vadt"] is not None and ctx["d2_vadt"] is not None:
    d1_share = f", {ctx['d1_pct']:.1f}% of total" if ctx["d1_pct"] is not None else ""
    d2_share = f", {ctx['d2_pct']:.1f}% of total" if ctx["d2_pct"] is not None else ""
    traffic_bits.append(
      f"directional demand is split between D1 ({_format_number(ctx['d1_vadt'])} vpd{d1_share}) and D2 ({_format_number(ctx['d2_vadt'])} vpd{d2_share})"
    )
  if ctx["growth_rate"] is not None:
    base_yr = ctx.get("base_year", "")
    open_yr = ctx.get("opening_year", "")
    yr_range = f" ({base_yr} → {open_yr})" if base_yr and open_yr and base_yr != open_yr else ""
    traffic_bits.append(f"an annual growth rate of {_format_number(ctx['growth_rate'], 2)}% has been applied{yr_range}")
  if traffic_bits:
    paragraphs.append("Traffic demand context indicates that " + ", ".join(traffic_bits) + ".")

  operational_bits: list[str] = []
  if ctx["worst_vcr"] is not None:
    operational_bits.append(f"the worst modeled V/C ratio is {_format_number(ctx['worst_vcr'], 2)} (LOS {ctx['los']})")
  if ctx["queue_peak"] is not None:
    operational_bits.append(f"the peak queue demand is approximately {_format_number(ctx['queue_peak'])} m")
  if ctx["d1_queue_peak"] is not None or ctx["d2_queue_peak"] is not None:
    operational_bits.append(f"directional peak queues are D1 {_format_number(ctx['d1_queue_peak'])} m and D2 {_format_number(ctx['d2_queue_peak'])} m")
  if operational_bits:
    paragraphs.append("Operational performance outcomes show that " + "; ".join(operational_bits) + ".")

  implication = (
    "On the basis of the current queue and capacity outputs, detour planning should be treated as an active mitigation requirement."
    if ctx["detour_recommended"]
    else "On the basis of the current queue and capacity outputs, an immediate detour trigger is not indicated, although staged monitoring and field verification remain important during implementation."
  )
  notes_suffix = f" Key supporting observations include: {'; '.join(ctx['notes'])}." if ctx["notes"] else ""
  paragraphs.append(implication + notes_suffix)
  return paragraphs


def _build_fallback_explanation_notes(payload: dict[str, Any]) -> list[str]:
  ctx = _build_report_context(payload)
  notes: list[str] = [
    f"Assessment location and basis: {ctx['project_name']} at {ctx['location']} under {ctx['road_mode']} road operation settings."
  ]
  if ctx["total_vadt"] is not None:
    year_tag = f" at {ctx['opening_year']}" if ctx.get("opening_year") else " at opening year"
    notes.append(
      f"Demand profile: total modeled traffic{year_tag} is about {_format_number(ctx['total_vadt'])} vpd, with D1 at {_format_number(ctx['d1_vadt'])} vpd and D2 at {_format_number(ctx['d2_vadt'])} vpd."
    )
  if ctx["worst_vcr"] is not None:
    notes.append(
      f"Network performance: worst V/C ratio is {_format_number(ctx['worst_vcr'], 2)}, which places the controlling condition at LOS {ctx['los']}."
    )
  if ctx["queue_peak"] is not None:
    notes.append(
      f"Queue implications: the longest modeled queue is approximately {_format_number(ctx['queue_peak'])} m, with D1 peak queue {_format_number(ctx['d1_queue_peak'])} m and D2 peak queue {_format_number(ctx['d2_queue_peak'])} m."
    )
  notes.append(
    "Mitigation interpretation: detour planning is recommended because the modeled queue and/or capacity outcomes indicate material operational risk during the work stage."
    if ctx["detour_recommended"]
    else "Mitigation interpretation: no immediate detour trigger is indicated by the current model, but field validation and staged monitoring should still be undertaken during implementation."
  )
  notes.extend(ctx["notes"])
  return notes[:8]


def _request_gemini_report_notes(payload: dict[str, Any]) -> dict[str, Any] | None:
  api_key = os.environ.get("GEMINI_API_KEY", "").strip()
  if not api_key:
    return None

  report_tables = _build_report_tables(payload)

  prompt = {
    "task": "Write a professional, readable traffic engineering report with per-table summaries. Summaries must feel like they were written by a senior traffic engineer for a council submission — not auto-generated.",
    "instructions": [
      "Return strict JSON only.",
      "Use Australian traffic engineering wording and conventions.",
      "State where the assessment applies and what the modeled outputs mean operationally.",
      "Do not invent values beyond the supplied context.",
      "Provide 3 or 4 executive summary paragraphs and 4 to 6 explanation notes.",
      "For every supplied table title, return a 'summary' field that is a natural-language interpretation of what the table data shows — NOT a description of the table structure like row/column counts.",
      "Table summaries must explain the engineering significance: what the numbers mean for traffic operations, which values are controlling, and what the practitioner should focus on.",
      "For directional summaries, explain the demand split and what it means for queue management.",
      "For queue tables, describe when and where the worst queuing occurs, how long queues extend, and what that means for upstream intersections or access points.",
      "For VCR/LOS tables, explain which periods exceed capacity and what that signals for work-zone management.",
      "For peak hour tables, describe the dominant traffic pattern and when the critical operating windows occur.",
      "For detour tables, describe the impact of diversion on receiving roads and whether there is spare capacity.",
      "For pedestrian detour tables, explain the added travel time burden on pedestrians.",
      "Each table summary should be 2-4 sentences of professional engineering narrative.",
      "Each scenario field should explain when and why the condition in the table typically becomes critical.",
      "Use the exact supplied table titles in the response so they can be mapped deterministically.",
      "Provide 3 professional commentary paragraphs and 3 to 5 conclusion points.",
      "Write as though this will be read by a council traffic engineer or road authority reviewer."
    ],
    "response_schema": {
      "executive_paragraphs": ["string"],
      "explanation_notes": ["string"],
      "table_analyses": [
        {
          "title": "string",
          "summary": "string",
          "scenario": "string",
          "chart_caption": "string"
        }
      ],
      "professional_commentary_paragraphs": ["string"],
      "conclusion_points": ["string"]
    },
    "context": _build_report_context(payload),
    "tables": [_summarize_table_for_prompt(table) for table in report_tables[:24]],
  }

  request_body = json.dumps(
    {
      "contents": [{"parts": [{"text": json.dumps(prompt, ensure_ascii=True)}]}],
      "generationConfig": {
        "temperature": 0.35,
        "responseMimeType": "application/json",
      },
    }
  ).encode("utf-8")
  request = urllib.request.Request(
    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
    data=request_body,
    headers={"Content-Type": "application/json"},
    method="POST",
  )

  import time as _time

  body = None
  for attempt in range(3):
    try:
      with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
      break
    except urllib.error.HTTPError as http_err:
      if http_err.code == 429 and attempt < 2:
        _time.sleep(2 * (attempt + 1))
        continue
      return None
    except (urllib.error.URLError, TimeoutError):
      return None

  if not body:
    return None

  try:
    parsed = json.loads(body)
    text = (
      parsed.get("candidates", [{}])[0]
      .get("content", {})
      .get("parts", [{}])[0]
      .get("text", "")
    )
    if not text:
      return None
    data = json.loads(text)
    paragraphs = [str(item).strip() for item in data.get("executive_paragraphs", []) if str(item).strip()]
    notes = [str(item).strip() for item in data.get("explanation_notes", []) if str(item).strip()]
    table_analyses = []
    for item in data.get("table_analyses", []):
      if not isinstance(item, dict):
        continue
      title = str(item.get("title", "")).strip()
      summary = str(item.get("summary", "")).strip()
      scenario = str(item.get("scenario", "")).strip()
      chart_caption = str(item.get("chart_caption", "")).strip()
      if not title:
        continue
      table_analyses.append(
        {
          "title": title,
          "summary": summary,
          "scenario": scenario,
          "chart_caption": chart_caption,
        }
      )
    professional_commentary = [
      str(item).strip()
      for item in data.get("professional_commentary_paragraphs", [])
      if str(item).strip()
    ]
    conclusion_points = [str(item).strip() for item in data.get("conclusion_points", []) if str(item).strip()]
    if not paragraphs and not notes and not table_analyses and not professional_commentary and not conclusion_points:
      return None
    return {
      "executive_paragraphs": paragraphs,
      "explanation_notes": notes,
      "table_analyses": table_analyses,
      "professional_commentary_paragraphs": professional_commentary,
      "conclusion_points": conclusion_points,
    }
  except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
    return None


def _build_executive_content(payload: dict[str, Any]) -> dict[str, Any]:
  report_tables = _build_report_tables(payload)
  fallback_table_analyses = [_build_fallback_table_analysis(table, payload) for table in report_tables]
  fallback_commentary = _build_fallback_professional_commentary(payload, fallback_table_analyses)
  fallback = {
    "executive_paragraphs": _build_fallback_executive_paragraphs(payload),
    "explanation_notes": _build_fallback_explanation_notes(payload),
    "table_analyses": fallback_table_analyses,
    "professional_commentary_paragraphs": fallback_commentary["professional_commentary_paragraphs"],
    "conclusion_points": fallback_commentary["conclusion_points"],
  }
  generated = _request_gemini_report_notes(payload)
  if not generated:
    return fallback
  fallback_map = { _normalize_title_key(item["title"]): item for item in fallback_table_analyses }
  generated_map = {
    _normalize_title_key(item.get("title")): item
    for item in generated.get("table_analyses", [])
    if isinstance(item, dict) and _normalize_title_key(item.get("title"))
  }
  merged_table_analyses = []
  for table in report_tables:
    title_key = _normalize_title_key(table.get("title"))
    fallback_item = fallback_map.get(title_key, _build_fallback_table_analysis(table, payload))
    generated_item = generated_map.get(title_key, {})
    # For hourly queue and hourly vcr tables, keep fallback chart_caption only
    # (preserves formula-specific peak references) but use Gemini for summary/scenario
    force_fallback_caption = any(
      key in title_key
      for key in ["hourly queue", "hourly vcr"]
    )
    merged_table_analyses.append(
      {
        "title": fallback_item["title"],
        "summary": generated_item.get("summary") or fallback_item["summary"],
        "scenario": generated_item.get("scenario") or fallback_item["scenario"],
        "chart_caption": fallback_item["chart_caption"] if force_fallback_caption else (generated_item.get("chart_caption") or fallback_item["chart_caption"]),
      }
    )
  return {
    "executive_paragraphs": generated.get("executive_paragraphs") or fallback["executive_paragraphs"],
    "explanation_notes": generated.get("explanation_notes") or fallback["explanation_notes"],
    "table_analyses": merged_table_analyses,
    "professional_commentary_paragraphs": generated.get("professional_commentary_paragraphs") or fallback["professional_commentary_paragraphs"],
    "conclusion_points": generated.get("conclusion_points") or fallback["conclusion_points"],
  }


def _render_paragraph_block(paragraphs: list[str]) -> str:
  cleaned = [str(item).strip() for item in paragraphs if str(item).strip()]
  if not cleaned:
    cleaned = ["[Insert executive summary details here...]"]
  return "".join(f"<p>{_escape(item)}</p>" for item in cleaned)


def _collect_chart_items(payload: dict[str, Any]) -> list[dict[str, str]]:
  def _safe_data_image_url(value: Any) -> str:
    text = _safe_text(value, "")
    if not text:
      return ""
    if len(text) > 6_000_000:
      return ""
    if not re.match(r"^data:image\/(png|jpe?g|webp|gif);base64,[A-Za-z0-9+/=\s]+$", text, re.IGNORECASE):
      return ""
    return text

  raw_charts = payload.get("charts", []) if isinstance(payload.get("charts"), list) else []
  chart_items: list[dict[str, str]] = []

  for idx, chart in enumerate(raw_charts):
    if not isinstance(chart, dict):
      continue
    image_data_url = _safe_data_image_url(chart.get("image_data_url"))
    if not image_data_url:
      continue
    raw_table_ids = chart.get("table_ids", []) if isinstance(chart.get("table_ids"), list) else []
    table_ids = [_safe_text(item, "") for item in raw_table_ids if _safe_text(item, "")]
    chart_items.append(
      {
        "title": _safe_text(chart.get("title"), f"Chart {idx + 1}"),
        "image": image_data_url,
        "canvas_id": _safe_text(chart.get("canvas_id"), ""),
        "table_ids": table_ids,
      }
    )

  if not chart_items:
    fallback = _safe_data_image_url(payload.get("chart_image_data_url"))
    if fallback:
      chart_items.append(
        {
          "title": "Primary Chart",
          "image": fallback,
          "canvas_id": _safe_text(payload.get("chart_image_canvas_id"), ""),
        }
      )

  return chart_items


def _score_chart_match(table_data: dict[str, Any], chart_item: dict[str, str]) -> int:
  score = 0
  table_title = _safe_text(table_data.get("title"), "")
  table_id = _normalize_title_key(table_data.get("table_id"))
  title_key = _normalize_title_key(table_title)
  chart_key = _normalize_title_key(chart_item.get("title"))
  canvas_id = _normalize_title_key(chart_item.get("canvas_id"))

  table_tokens = _title_tokens(table_title)
  chart_tokens = _title_tokens(chart_key + " " + canvas_id)
  score += len(table_tokens & chart_tokens) * 3

  if table_id in {"analysis parameters", "analysis_parameters"} and "macrohourlychart" in canvas_id:
    score += 80
  if table_id in {"summary computed results", "summary_computed_results"} and "managementvizcanvas" in canvas_id:
    score += 80
  if table_id == "groupedtabled1" and "queuechartd1" in canvas_id:
    score += 100
  if table_id == "groupedtabled1" and "vcrchartd1" in canvas_id:
    score += 100
  if table_id == "groupedtabled2" and "queuechartd2" in canvas_id:
    score += 100
  if table_id == "groupedtabled2" and "vcrchartd2" in canvas_id:
    score += 100
  if table_id == "queuegroupedtabled1" and "queuechartd1" in canvas_id:
    score += 120
  if table_id == "queuegroupedtabled2" and "queuechartd2" in canvas_id:
    score += 120
  if table_id == "queueswtsummarytable" and "hourlyqueuechart" in canvas_id:
    score += 90
  if table_id in {"hourlyqueuetabled1", "hourlyqueuetabled2"} and "hourlyqueuechart" in canvas_id:
    score += 120
  if table_id == "vcrgroupedtabled1" and "vcrchartd1" in canvas_id:
    score += 120
  if table_id == "vcrgroupedtabled2" and "vcrchartd2" in canvas_id:
    score += 120
  if table_id in {"hourlyvcrtabled1", "hourlyvcrtabled2"} and "hourlyvcrchart" in canvas_id:
    score += 120
  if table_id == "detoursegmentdetailedtable" and ("managementvizcanvas" in canvas_id or "macrohourlychart" in canvas_id):
    score += 20

  if "queuechartd1" in canvas_id and "queue" in title_key and ("d1" in title_key or "direction 1" in title_key):
    score += 30
  if "queuechartd2" in canvas_id and "queue" in title_key and ("d2" in title_key or "direction 2" in title_key):
    score += 30
  if "vcrchartd1" in canvas_id and "vcr" in title_key and ("d1" in title_key or "direction 1" in title_key):
    score += 30
  if "vcrchartd2" in canvas_id and "vcr" in title_key and ("d2" in title_key or "direction 2" in title_key):
    score += 30
  if "hourlyqueuechart" in canvas_id and "hourly" in title_key and "queue" in title_key:
    score += 30
  if "hourlyvcrchart" in canvas_id and "hourly" in title_key and ("vcr" in title_key or "los" in title_key):
    score += 30
  if "macrohourlychart" in canvas_id and ("direction" in title_key or "analysis parameters" in title_key):
    score += 16
  if "managementvizcanvas" in canvas_id and "summary of computed results" in title_key:
    score += 24

  if "queue" in title_key and "queue" in chart_key:
    score += 8
  if "vcr" in title_key and "vcr" in chart_key:
    score += 8
  if "hourly" in title_key and "hourly" in chart_key:
    score += 5
  if "directional" in title_key and "traffic data visualization" in chart_key:
    score += 6

  return score


def _select_charts_for_table(
  table_data: dict[str, Any],
  chart_items: list[dict[str, str]],
) -> list[dict[str, str]]:
  table_id = _normalize_title_key(table_data.get("table_id"))
  explicit_matches: list[dict[str, str]] = []
  has_explicit_links = False

  for chart_item in chart_items:
    raw_table_ids = chart_item.get("table_ids") if isinstance(chart_item, dict) else None
    if not isinstance(raw_table_ids, list) or not raw_table_ids:
      continue
    normalized_table_ids = {
      _normalize_title_key(item)
      for item in raw_table_ids
      if _safe_text(item, "")
    }
    if not normalized_table_ids:
      continue
    has_explicit_links = True
    if table_id and table_id in normalized_table_ids:
      explicit_matches.append(chart_item)

  if explicit_matches:
    return explicit_matches[:2]
  if has_explicit_links:
    return []

  scored: list[tuple[int, dict[str, str]]] = []
  for chart_item in chart_items:
    score = _score_chart_match(table_data, chart_item)
    if score <= 0:
      continue
    scored.append((score, chart_item))

  if not scored:
    return []

  scored.sort(key=lambda item: item[0], reverse=True)
  top_score = scored[0][0]
  selected = [item for score, item in scored if score == top_score]
  return selected[:2]


def _render_embedded_chart(chart_item: dict[str, str] | None, title: str, caption: str) -> str:
  if not chart_item:
    return ""
  chart_title = _escape(_safe_text(chart_item.get("title"), title))
  chart_caption = _escape(caption or "This chart summarises the controlling pattern before the detailed table below.")
  image_title = _escape(chart_item.get("title"), title)
  return (
    "<figure class=\"embedded-chart avoid-break report-block\">"
    "<div class=\"section-controls no-print\">"
    "<button type=\"button\" class=\"mini-btn\" onclick=\"removeReportBlock(this)\">&#10005; Remove Chart</button>"
    "</div>"
    f"<h5 class=\"chart-title editable-text\" contenteditable=\"true\">{chart_title}</h5>"
    f"<img class=\"chart-img\" src=\"{chart_item.get('image', '')}\" alt=\"{image_title}\" />"
    f"<figcaption class=\"editable chart-caption editable-text\" contenteditable=\"true\">{chart_caption}</figcaption>"
    "</figure>"
  )


def _render_embedded_charts(chart_items: list[dict[str, str]] | None, title: str, caption: str) -> str:
  if not chart_items:
    return ""
  return "".join(_render_embedded_chart(item, title, caption) for item in chart_items)


def _render_key_value_table(
  title: str,
  data: dict[str, Any],
  analysis: dict[str, str] | None = None,
  chart_items: list[dict[str, str]] | None = None,
) -> str:
  if not isinstance(data, dict) or not data:
    return ""

  rows: list[str] = []
  for key, val in data.items():
    label = _escape(str(key).replace("_", " ").title())
    value = _escape(val)
    rows.append(
      f"<tr><th class=\"editable-text editable-cell\" contenteditable=\"true\">{label}</th>"
      f"<td class=\"editable-text editable-cell\" contenteditable=\"true\">{value}</td></tr>"
    )

  summary_text = _escape((analysis or {}).get("summary"), "This section summarises the key values used to interpret the modeled outcome.")
  scenario_text = _escape((analysis or {}).get("scenario"), "Review these values together with the detailed result tables to confirm when the controlling condition is expected to emerge.")
  chart_caption = (analysis or {}).get("chart_caption", "")
  chart_html = _render_embedded_charts(chart_items, title, chart_caption)

  return (
    f"<div class=\"report-section report-block avoid-break\">"
    f"<div class=\"section-controls no-print\">"
    f"<button type=\"button\" class=\"mini-btn\" onclick=\"addTableRow(this)\">➕ Row</button> "
    f"<button type=\"button\" class=\"mini-btn\" onclick=\"removeTableLastRow(this)\">➖ Row</button> "
    f"<button type=\"button\" class=\"mini-btn\" onclick=\"removeReportBlock(this)\">✕ Remove</button>"
    f"</div>"
    f"<h3 class=\"editable-text\" contenteditable=\"true\">{_escape(title)}</h3>"
    f"<div class=\"editable table-note table-note-top\" contenteditable=\"true\"><p>{summary_text}</p></div>"
    f"{chart_html}"
    "<div class=\"table-detail-lead editable-text\" contenteditable=\"true\">Detailed table below sets out the supporting values used for the engineering interpretation.</div>"
    f"<table class=\"kv-table\"><tbody>{''.join(rows)}</tbody></table>"
    f"<div class=\"editable table-note table-note-bottom\" contenteditable=\"true\"><p>{scenario_text}</p></div>"
    f"</div>"
  )


def _render_selected_site_details_section(site_details: dict[str, Any]) -> str:
  if not isinstance(site_details, dict) or not site_details:
    return ""

  ordered_fields = [
    ("Site ID", "site_id"),
    ("Source", "source"),
    ("Road Name", "road_name"),
    ("Description", "description"),
    ("Coordinates", "coordinates"),
    ("Google Maps", "google_maps_url"),
    ("Count Year", "count_year"),
    ("Applied Growth Rate", "growth_rate"),
    ("Selected Site HV%", "site_hv_percent"),
    ("Data Quality", "data_quality"),
    ("D1 Direction", "d1_direction"),
    ("D2 Direction", "d2_direction"),
    ("D1 VADT", "d1_vadt"),
    ("D2 VADT", "d2_vadt"),
    ("Total VADT", "total_vadt"),
    ("Applied D1 HV%", "applied_d1_hv_percent"),
    ("Applied D2 HV%", "applied_d2_hv_percent"),
    ("Applied D1 RT%", "applied_d1_rt_percent"),
    ("Applied D2 RT%", "applied_d2_rt_percent"),
  ]

  rows: list[str] = []
  for label, key in ordered_fields:
    value = _safe_text(site_details.get(key), "")
    if not value:
      continue
    if key == "google_maps_url" and value.startswith(("http://", "https://")):
      safe_href = escape(value, quote=True)
      value_html = f'<a href="{safe_href}" target="_blank" rel="noopener noreferrer">View on Google Maps</a>'
    else:
      value_html = _escape(value)
    rows.append(
      f"<tr><th class=\"editable-text editable-cell\" contenteditable=\"true\">{_escape(label)}</th>"
      f"<td class=\"editable-cell\">{value_html}</td></tr>"
    )

  if not rows:
    return ""

  summary_text = (
    "Selected site details record the source counter, field description, mapping reference, and the heavy-vehicle / rigid-truck assumptions that feed the assessment."
  )
  scenario_text = (
    "Use this section to verify that the adopted counter, mapped location, and HV / RT settings match the corridor actually being assessed before relying on the downstream queue or V/C outputs."
  )

  return (
    "<div class=\"report-section report-block avoid-break\">"
    "<div class=\"section-controls no-print\">"
    "<button type=\"button\" class=\"mini-btn\" onclick=\"addTableRow(this)\">➕ Row</button> "
    "<button type=\"button\" class=\"mini-btn\" onclick=\"removeTableLastRow(this)\">➖ Row</button> "
    "<button type=\"button\" class=\"mini-btn\" onclick=\"removeReportBlock(this)\">✕ Remove</button>"
    "</div>"
    "<h3 class=\"editable-text\" contenteditable=\"true\">Selected Site Details</h3>"
    f"<div class=\"editable table-note table-note-top\" contenteditable=\"true\"><p>{_escape(summary_text)}</p></div>"
    "<div class=\"table-detail-lead editable-text\" contenteditable=\"true\">Detailed table below confirms the selected counter, map reference, and applied vehicle-mix inputs used in the report.</div>"
    f"<table class=\"kv-table\"><tbody>{''.join(rows)}</tbody></table>"
    f"<div class=\"editable table-note table-note-bottom\" contenteditable=\"true\"><p>{_escape(scenario_text)}</p></div>"
    "</div>"
  )


def _render_notes(notes: Any) -> str:
  if not isinstance(notes, list) or not notes:
    return "<li class=\"editable-text\" contenteditable=\"true\">No supplementary notes provided.</li>"
  return "".join(f"<li class=\"editable-text\" contenteditable=\"true\">{_escape(item)}</li>" for item in notes)


def _infer_result_context(key: str, value: str) -> str:
  """Infer when/context description for a computed result metric."""
  key_lc = key.replace("_", " ").lower()
  if "am" in key_lc and ("peak" in key_lc or "hour" in key_lc):
    return "AM Peak Period (typically 7–9 am)"
  if "pm" in key_lc and ("peak" in key_lc or "hour" in key_lc):
    return "PM Peak Period (typically 4–6 pm)"
  if ("ev" in key_lc or "evening" in key_lc) and ("peak" in key_lc or "hour" in key_lc):
    return "Evening Peak Period (typically 6–8 pm)"
  if "off peak" in key_lc or "off-peak" in key_lc or " op " in f" {key_lc} ":
    return "Off-Peak / Inter-Peak Period"
  if "vcr" in key_lc or "v/c" in key_lc or "volume capacity" in key_lc:
    try:
      raw = re.sub(r"[^\d.]", "", str(value or "").split()[0])
      v = float(raw) if raw else None
      if v is not None:
        if v >= 0.9:
          return "Near or at capacity — mitigation likely required"
        if v >= 0.75:
          return "Approaching capacity — monitor closely"
        return "Within acceptable capacity threshold"
    except Exception:
      pass
    return "Volume-to-Capacity Ratio — compare against LOS thresholds"
  if "los" in key_lc or "level of service" in key_lc:
    return "Level of Service — A=free-flow, F=breakdown"
  if "queue" in key_lc:
    return "Maximum queue length during study period"
  if "delay" in key_lc:
    return "Average per-vehicle delay at the intersection"
  if "growth" in key_lc:
    return "Annual traffic growth rate applied to base volumes"
  if "peak" in key_lc and "hour" in key_lc:
    return "Controlling peak-hour period for this analysis"
  if "total" in key_lc and ("vpd" in key_lc or "vadt" in key_lc or "volume" in key_lc):
    return "Total daily two-way traffic volume"
  if "d1" in key_lc or "direction 1" in key_lc:
    return "Direction 1 (primary / approach direction)"
  if "d2" in key_lc or "direction 2" in key_lc:
    return "Direction 2 (secondary / opposing direction)"
  if "hv" in key_lc or "heavy vehicle" in key_lc:
    return "Heavy vehicle percentage adopted for analysis"
  return "—"


def _render_computed_results_section(
  title: str,
  results: dict[str, Any],
  analysis: dict[str, str] | None = None,
  chart_items: list[dict[str, str]] | None = None,
) -> str:
  """Render the Summary of Computed Results as a 3-column table (Metric | Value | Context/When)."""
  if not isinstance(results, dict) or not results:
    return ""

  rows: list[str] = []
  for key, val in results.items():
    label = _escape(str(key).replace("_", " ").title())
    value = _escape(val)
    context = _escape(_infer_result_context(key, _safe_text(val, "")))
    rows.append(
      f"<tr>"
      f"<th class=\"editable-text editable-cell\" contenteditable=\"true\">{label}</th>"
      f"<td class=\"editable-text editable-cell\" contenteditable=\"true\">{value}</td>"
      f"<td class=\"editable-text editable-cell context-col\" contenteditable=\"true\">{context}</td>"
      f"</tr>"
    )

  summary_text = _escape(
    (analysis or {}).get("summary",
      "This table summarises the key computed traffic metrics. The third column provides context on when each metric is expected to be controlling.")
  )
  scenario_text = _escape(
    (analysis or {}).get("scenario",
      "Review these computed results together with the detailed tables and VCR/queue charts to confirm the controlling period and identify any required mitigation.")
  )
  chart_caption = (analysis or {}).get("chart_caption", "")
  chart_html = _render_embedded_charts(chart_items, title, chart_caption)

  header_row = (
    "<thead><tr>"
    "<th class=\"editable-text editable-cell\" contenteditable=\"true\">Metric</th>"
    "<th class=\"editable-text editable-cell\" contenteditable=\"true\">Value</th>"
    "<th class=\"editable-text editable-cell\" contenteditable=\"true\">Context / When</th>"
    "</tr></thead>"
  )

  return (
    f"<div class=\"report-section report-block avoid-break\">"
    f"<div class=\"section-controls no-print\">"
    f"<button type=\"button\" class=\"mini-btn\" onclick=\"addTableRow(this)\">➕ Row</button> "
    f"<button type=\"button\" class=\"mini-btn\" onclick=\"removeTableLastRow(this)\">➖ Row</button> "
    f"<button type=\"button\" class=\"mini-btn\" onclick=\"removeReportBlock(this)\">✕ Remove</button>"
    f"</div>"
    f"<h3 class=\"editable-text\" contenteditable=\"true\">{_escape(title)}</h3>"
    f"<div class=\"editable table-note table-note-top\" contenteditable=\"true\"><p>{summary_text}</p></div>"
    f"{chart_html}"
    "<div class=\"table-detail-lead editable-text\" contenteditable=\"true\">Detailed table below sets out the computed metrics with context on when each controlling condition occurs.</div>"
    f"<table class=\"kv-table results-3col\">{header_row}<tbody>{''.join(rows)}</tbody></table>"
    f"<div class=\"editable table-note table-note-bottom\" contenteditable=\"true\"><p>{scenario_text}</p></div>"
    f"</div>"
  )


def _render_short_detour_route_block(route_label: str, route_tables: list[dict], analysis_map: dict, chart_items: list[dict] = None) -> str:
  """Render a 6-subsection detour block per route, in the required order:
  1. VPD Calculated  2. Detour Road Directional Capacity Summary
  3. Detour Road Capacity Summary  4. Existing Road Status After Diversion
  5. Estimated Delay – Detour route  6. Pedestrian Detour Impact – Delay calculation
  """
  if chart_items is None:
    chart_items = []
  label_esc = _escape(route_label)

  # Classify each table into one of the 6 ordered slots.
  # Fixed keywords to match exact titles coming from the HTML front-end.
  vpd_tables: list[dict] = []
  dir_capacity_tables: list[dict] = []
  road_capacity_tables: list[dict] = []
  road_status_tables: list[dict] = []
  delay_tables: list[dict] = []
  pedestrian_tables: list[dict] = []
  other_tables: list[dict] = []

  for table in route_tables:
    title_lc = _safe_text(table.get("title"), "").lower()
    if "pedestrian" in title_lc:
      pedestrian_tables.append(table)
    elif "delay" in title_lc:
      delay_tables.append(table)
    elif "status" in title_lc or "diversion" in title_lc or "existing road" in title_lc:
      road_status_tables.append(table)
    elif "directional capacity" in title_lc:
      dir_capacity_tables.append(table)
    elif "capacity" in title_lc:
      road_capacity_tables.append(table)
    elif "vpd" in title_lc or "segment detailed" in title_lc:
      vpd_tables.append(table)
    else:
      other_tables.append(table)

  def _render_group(tables: list[dict], fallback_html: str) -> str:
    if tables:
      return "".join(
        _render_data_table(t, analysis_map.get(_normalize_title_key(t.get("title"))), _select_charts_for_table(t, chart_items))
        for t in tables
      )
    return fallback_html

  other_html = "".join(
    _render_data_table(t, analysis_map.get(_normalize_title_key(t.get("title"))), _select_charts_for_table(t, chart_items))
    for t in other_tables
  )

  # --- HARDCODED EDITABLE TABLE FALLBACKS ---
  fallback_vpd = (
    "<table><thead><tr>"
    "<th class=\"editable-text\" contenteditable=\"true\">Direction</th><th class=\"editable-text\" contenteditable=\"true\">Base VADT (vpd)</th><th class=\"editable-text\" contenteditable=\"true\">Growth Factor</th><th class=\"editable-text\" contenteditable=\"true\">Design Year VADT (vpd)</th><th class=\"editable-text\" contenteditable=\"true\">Diverted Traffic (vpd)</th><th class=\"editable-text\" contenteditable=\"true\">Total VPD on Detour</th>"
    "</tr></thead><tbody><tr><td class=\"editable-text\" contenteditable=\"true\">D1</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td></tr><tr><td class=\"editable-text\" contenteditable=\"true\">D2</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td></tr></tbody></table><p class=\"editable-text\" contenteditable=\"true\">Edit to insert calculated VPD values for the detour route under full diversion conditions.</p>"
  )

  fallback_dir_capacity = (
    "<table><thead><tr>"
    "<th class=\"editable-text\" contenteditable=\"true\">Direction</th><th class=\"editable-text\" contenteditable=\"true\">Lane Count</th><th class=\"editable-text\" contenteditable=\"true\">Per-Lane Capacity (veh/h)</th><th class=\"editable-text\" contenteditable=\"true\">Total Capacity (veh/h)</th><th class=\"editable-text\" contenteditable=\"true\">Peak Hour Volume (veh/h)</th><th class=\"editable-text\" contenteditable=\"true\">VCR</th><th class=\"editable-text\" contenteditable=\"true\">LOS</th>"
    "</tr></thead><tbody><tr><td class=\"editable-text\" contenteditable=\"true\">D1</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td></tr><tr><td class=\"editable-text\" contenteditable=\"true\">D2</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td></tr></tbody></table><p class=\"editable-text\" contenteditable=\"true\">Edit to confirm directional capacity and VCR under diversion conditions for the detour road.</p>"
  )

  fallback_road_capacity = (
    "<table><thead><tr><th class=\"editable-text\" contenteditable=\"true\">Parameter</th><th class=\"editable-text\" contenteditable=\"true\">Value</th><th class=\"editable-text\" contenteditable=\"true\">Notes</th></tr></thead><tbody>"
    "<tr><td class=\"editable-text\" contenteditable=\"true\">Road Classification</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td></tr><tr><td class=\"editable-text\" contenteditable=\"true\">Posted Speed (km/h)</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td></tr><tr><td class=\"editable-text\" contenteditable=\"true\">Total Two-Way Capacity (vpd)</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td></tr><tr><td class=\"editable-text\" contenteditable=\"true\">Remaining Surplus Capacity (vpd)</td><td class=\"editable-text\" contenteditable=\"true\">—</td><td class=\"editable-text\" contenteditable=\"true\">—</td></tr></tbody></table><p class=\"editable-text\" contenteditable=\"true\">Edit to confirm overall road capacity relative to diverted demand.</p>"
  )

  fallback_road_status = (
    "<div class=\"editable\" contenteditable=\"true\"><p>Following full diversion of traffic to this route, confirm that the detour road remains within acceptable operating conditions. Key considerations include: pavement condition, geometric constraints (narrow lanes, sharp curves, limited sight-distance), intersection control adequacy, and pedestrian / cyclist conflicts. Edit this section to record findings.</p></div>"
  )

  fallback_delay = (
    "<table><thead><tr><th class=\"editable-text\" contenteditable=\"true\">Parameter</th><th class=\"editable-text\" contenteditable=\"true\">Value</th></tr></thead><tbody><tr><td class=\"editable-text\" contenteditable=\"true\">Detour Route Length (km)</td><td class=\"editable-text\" contenteditable=\"true\">—</td></tr><tr><td class=\"editable-text\" contenteditable=\"true\">Original Route Length (km)</td><td class=\"editable-text\" contenteditable=\"true\">—</td></tr><tr><td class=\"editable-text\" contenteditable=\"true\">Extra Distance (km)</td><td class=\"editable-text\" contenteditable=\"true\">—</td></tr><tr><td class=\"editable-text\" contenteditable=\"true\">Average Travel Speed on Detour (km/h)</td><td class=\"editable-text\" contenteditable=\"true\">—</td></tr><tr><td class=\"editable-text\" contenteditable=\"true\">Estimated Additional Travel Time (min)</td><td class=\"editable-text\" contenteditable=\"true\">—</td></tr><tr><td class=\"editable-text\" contenteditable=\"true\">Delay Classification</td><td class=\"editable-text\" contenteditable=\"true\">—</td></tr></tbody></table><p class=\"editable-text\" contenteditable=\"true\">Edit to calculate and record the additional delay imposed on motorists by the detour route.</p>"
  )

  fallback_pedestrian = (
    "<div class=\"editable\" contenteditable=\"true\"><p>Assess whether the detour route provides safe and accessible pedestrian connectivity. Key items to address: availability of footpath / shared path; suitable crossing facilities at intersections; WCAG / DDA compliance; additional walking distance for pedestrians and approximate delay. Edit this section to record pedestrian impact findings.</p></div>"
    "<table><thead><tr><th class=\"editable-text\" contenteditable=\"true\">Parameter</th><th class=\"editable-text\" contenteditable=\"true\">Value</th></tr></thead><tbody><tr><td class=\"editable-text\" contenteditable=\"true\">Pedestrian Detour Distance (m)</td><td class=\"editable-text\" contenteditable=\"true\">—</td></tr><tr><td class=\"editable-text\" contenteditable=\"true\">Additional Walking Time (min)</td><td class=\"editable-text\" contenteditable=\"true\">—</td></tr><tr><td class=\"editable-text\" contenteditable=\"true\">Pedestrian Delay Classification</td><td class=\"editable-text\" contenteditable=\"true\">—</td></tr><tr><td class=\"editable-text\" contenteditable=\"true\">Mitigation Recommended</td><td class=\"editable-text\" contenteditable=\"true\">—</td></tr></tbody></table>"
  )

  return (
    f"<div class=\"report-section report-block detour-route-block\">"
    f"<div class=\"section-controls no-print\"><button type=\"button\" class=\"mini-btn\" onclick=\"removeReportBlock(this)\">✕ Remove</button></div>"
    f"<h3 class=\"editable-text\" contenteditable=\"true\">{label_esc}</h3>"
    f"{other_html}"
    "<div class=\"detour-sub-block avoid-break\"><h4 class=\"editable-text\" contenteditable=\"true\">1. VPD Calculated table</h4>" + _render_group(vpd_tables, fallback_vpd) + "</div>"
    "<div class=\"detour-sub-block avoid-break\"><h4 class=\"editable-text\" contenteditable=\"true\">2. Detour Road Directional Capacity Summary</h4>" + _render_group(dir_capacity_tables, fallback_dir_capacity) + "</div>"
    "<div class=\"detour-sub-block avoid-break\"><h4 class=\"editable-text\" contenteditable=\"true\">3. Detour Road Capacity Summary</h4>" + _render_group(road_capacity_tables, fallback_road_capacity) + "</div>"
    "<div class=\"detour-sub-block avoid-break\"><h4 class=\"editable-text\" contenteditable=\"true\">4. Existing Road Status After Diversion</h4>" + _render_group(road_status_tables, fallback_road_status) + "</div>"
    "<div class=\"detour-sub-block avoid-break\"><h4 class=\"editable-text\" contenteditable=\"true\">5. Estimated Delay - Detour route</h4>" + _render_group(delay_tables, fallback_delay) + "</div>"
    "<div class=\"detour-sub-block avoid-break\"><h4 class=\"editable-text\" contenteditable=\"true\">6. Pedestrian Detour Impact &#8211; Delay calculation</h4>" + _render_group(pedestrian_tables, fallback_pedestrian) + "</div>"
    "</div>"
  )


def _build_short_detour_section(detour_tables: list[dict], route_count: int, analysis_map: dict, chart_items: list[dict] = None) -> str:
  """Build the complete Section 5 Detour Analysis HTML."""
  if chart_items is None:
    chart_items = []

  if not detour_tables and route_count < 1:
    return ""

  from collections import defaultdict
  route_groups: dict[str, list[dict]] = defaultdict(list)

  # Group by exact route number.
  for table in detour_tables:
    title = _safe_text(table.get("title"), "")
    m = re.search(r"Detour Route\s*(\d+)", title, re.IGNORECASE)
    key = m.group(1) if m else "1"
    route_groups[key].append(table)

  effective_count = max(route_count, len(route_groups), 1)
  rendered: list[str] = []

  for i in range(1, effective_count + 1):
    key = str(i)
    route_label_str = f"Detour Route {i}"
    block = _render_short_detour_route_block(route_label_str, route_groups.get(key, []), analysis_map, chart_items)
    rendered.append(block)

  if not rendered:
    return ""

  return (
    "<div class=\"page-break\"></div>"
    "<h2 contenteditable=\"true\">5. Detour Analysis</h2>"
    "<div class=\"editable table-note\" contenteditable=\"true\">"
    "<p>This section summarises the detour route analysis. For each alternative route, the road capacity, estimated motorist delay, and pedestrian impact are assessed.</p>"
    "</div>"
    + "".join(rendered)
  )


def _render_detour_subsections(route_label: str = "") -> str:
  """Render expanded detour analysis subsections for a route."""
  label = _escape(route_label) if route_label else "Detour Route"
  return (
    f"<div class=\"report-section report-block detour-subsections avoid-break\">"
    f"<div class=\"section-controls no-print\"><button type=\"button\" class=\"mini-btn\" onclick=\"removeReportBlock(this)\">✕ Remove</button></div>"
    f"<h4 class=\"editable-text\" contenteditable=\"true\">{label} — Detailed Analysis</h4>"

    # 1. VPD Calculated
    "<div class=\"detour-sub-block avoid-break\">"
    "<h5 class=\"editable-text\" contenteditable=\"true\">i. VPD Calculated</h5>"
    "<table><thead><tr>"
    "<th contenteditable=\"true\">Direction</th>"
    "<th contenteditable=\"true\">Base VADT (vpd)</th>"
    "<th contenteditable=\"true\">Growth Factor</th>"
    "<th contenteditable=\"true\">Design Year VADT (vpd)</th>"
    "<th contenteditable=\"true\">Diverted Traffic (vpd)</th>"
    "<th contenteditable=\"true\">Total VPD on Detour</th>"
    "</tr></thead><tbody>"
    "<tr><td contenteditable=\"true\">D1</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td></tr>"
    "<tr><td contenteditable=\"true\">D2</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td></tr>"
    "</tbody></table>"
    "<p class=\"editable-text\" contenteditable=\"true\">Edit to insert calculated VPD values for the detour route under full diversion conditions.</p>"
    "</div>"

    # 2. Detour Road Directional Capacity Summary
    "<div class=\"detour-sub-block avoid-break\">"
    "<h5 class=\"editable-text\" contenteditable=\"true\">ii. Detour Road Directional Capacity Summary</h5>"
    "<table><thead><tr>"
    "<th contenteditable=\"true\">Direction</th>"
    "<th contenteditable=\"true\">Lane Count</th>"
    "<th contenteditable=\"true\">Per-Lane Capacity (veh/h)</th>"
    "<th contenteditable=\"true\">Total Capacity (veh/h)</th>"
    "<th contenteditable=\"true\">Peak Hour Volume (veh/h)</th>"
    "<th contenteditable=\"true\">VCR</th>"
    "<th contenteditable=\"true\">LOS</th>"
    "</tr></thead><tbody>"
    "<tr><td contenteditable=\"true\">D1</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td></tr>"
    "<tr><td contenteditable=\"true\">D2</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td></tr>"
    "</tbody></table>"
    "<p class=\"editable-text\" contenteditable=\"true\">Edit to confirm directional capacity and VCR under diversion conditions for the detour road.</p>"
    "</div>"

    # 3. Detour Road Capacity Summary
    "<div class=\"detour-sub-block avoid-break\">"
    "<h5 class=\"editable-text\" contenteditable=\"true\">iii. Detour Road Capacity Summary</h5>"
    "<table><thead><tr>"
    "<th contenteditable=\"true\">Parameter</th>"
    "<th contenteditable=\"true\">Value</th>"
    "<th contenteditable=\"true\">Notes</th>"
    "</tr></thead><tbody>"
    "<tr><td contenteditable=\"true\">Road Classification</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td></tr>"
    "<tr><td contenteditable=\"true\">Posted Speed (km/h)</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td></tr>"
    "<tr><td contenteditable=\"true\">Total Two-Way Capacity (vpd)</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td></tr>"
    "<tr><td contenteditable=\"true\">Remaining Surplus Capacity (vpd)</td><td contenteditable=\"true\">—</td><td contenteditable=\"true\">—</td></tr>"
    "</tbody></table>"
    "<p class=\"editable-text\" contenteditable=\"true\">Edit to confirm overall road capacity relative to diverted demand.</p>"
    "</div>"

    # 4. Existing Road Status After Diversion
    "<div class=\"detour-sub-block avoid-break\">"
    "<h5 class=\"editable-text\" contenteditable=\"true\">iv. Existing Road Status After Diversion</h5>"
    "<div class=\"editable\" contenteditable=\"true\">"
    "<p>Following full diversion of traffic to this route, confirm that the detour road remains within acceptable operating conditions. "
    "Key considerations include: pavement condition, geometric constraints (narrow lanes, sharp curves, limited sight-distance), "
    "intersection control adequacy, and pedestrian / cyclist conflicts. Edit this section to record findings.</p>"
    "</div>"
    "</div>"

    # 5. Estimated Delay
    "<div class=\"detour-sub-block avoid-break\">"
    "<h5 class=\"editable-text\" contenteditable=\"true\">v. Estimated Delay — Detour Route</h5>"
    "<table><thead><tr>"
    "<th contenteditable=\"true\">Parameter</th>"
    "<th contenteditable=\"true\">Value</th>"
    "</tr></thead><tbody>"
    "<tr><td contenteditable=\"true\">Detour Route Length (km)</td><td contenteditable=\"true\">—</td></tr>"
    "<tr><td contenteditable=\"true\">Original Route Length (km)</td><td contenteditable=\"true\">—</td></tr>"
    "<tr><td contenteditable=\"true\">Extra Distance (km)</td><td contenteditable=\"true\">—</td></tr>"
    "<tr><td contenteditable=\"true\">Average Travel Speed on Detour (km/h)</td><td contenteditable=\"true\">—</td></tr>"
    "<tr><td contenteditable=\"true\">Estimated Additional Travel Time (min)</td><td contenteditable=\"true\">—</td></tr>"
    "<tr><td contenteditable=\"true\">Delay Classification</td><td contenteditable=\"true\">—</td></tr>"
    "</tbody></table>"
    "<p class=\"editable-text\" contenteditable=\"true\">Edit to calculate and record the additional delay imposed on motorists by the detour route.</p>"
    "</div>"

    # 6. Pedestrian Detour Impact
    "<div class=\"detour-sub-block avoid-break\">"
    "<h5 class=\"editable-text\" contenteditable=\"true\">vi. Pedestrian Detour Impact — Delay Calculation</h5>"
    "<div class=\"editable\" contenteditable=\"true\">"
    "<p>Assess whether the detour route provides safe and accessible pedestrian connectivity. Key items to address: "
    "availability of footpath / shared path; suitable crossing facilities at intersections; WCAG / DDA compliance; "
    "additional walking distance for pedestrians and approximate delay. Edit this section to record pedestrian impact findings.</p>"
    "</div>"
    "<table><thead><tr>"
    "<th contenteditable=\"true\">Parameter</th>"
    "<th contenteditable=\"true\">Value</th>"
    "</tr></thead><tbody>"
    "<tr><td contenteditable=\"true\">Pedestrian Detour Distance (m)</td><td contenteditable=\"true\">—</td></tr>"
    "<tr><td contenteditable=\"true\">Additional Walking Time (min)</td><td contenteditable=\"true\">—</td></tr>"
    "<tr><td contenteditable=\"true\">Pedestrian Delay Classification</td><td contenteditable=\"true\">—</td></tr>"
    "<tr><td contenteditable=\"true\">Mitigation Recommended</td><td contenteditable=\"true\">—</td></tr>"
    "</tbody></table>"
    "</div>"

    "</div>"
  )


def _render_engineering_observations(notes: Any) -> str:
  """Render engineering observations as structured, categorised subsections."""
  raw_notes: list[str] = []
  if isinstance(notes, list):
    raw_notes = [str(n).strip() for n in notes if str(n).strip()]

  traffic_ops: list[str] = []
  intersection: list[str] = []
  detour_notes: list[str] = []
  general: list[str] = []

  for note in raw_notes:
    note_lc = note.lower()
    if any(k in note_lc for k in ["queue", "vcr", "volume", "v/c", "capacity", "los", "level of service"]):
      traffic_ops.append(note)
    elif any(k in note_lc for k in ["intersection", "signal", "turning", "movement", "approach"]):
      intersection.append(note)
    elif any(k in note_lc for k in ["detour", "diversion", "alternate route", "closure"]):
      detour_notes.append(note)
    else:
      general.append(note)

  def _note_list(items: list[str], placeholder: str) -> str:
    if not items:
      return (
        f"<li class=\"editable-text\" contenteditable=\"true\">{_escape(placeholder)}</li>"
      )
    return "".join(
      f"<li class=\"editable-text\" contenteditable=\"true\">{_escape(n)}</li>"
      for n in items
    )

  return (
    "<div class=\"report-section report-block avoid-break\">"
    "<div class=\"section-controls no-print\"><button type=\"button\" class=\"mini-btn\" onclick=\"removeReportBlock(this)\">✕ Remove</button></div>"

    "<div class=\"obs-subsection\">"
    "<h3 class=\"editable-text\" contenteditable=\"true\">5.1 Traffic Operations</h3>"
    "<div class=\"editable obs-lead\" contenteditable=\"true\">"
    "<p>The following observations relate to traffic volume, speed, VCR, and queue performance on the subject road network during the study period.</p>"
    "</div>"
    f"<ul>{_note_list(traffic_ops, 'No specific traffic operations observations noted. Edit to add findings on VCR, queue length, and LOS.')}</ul>"
    "</div>"

    "<div class=\"obs-subsection\">"
    "<h3 class=\"editable-text\" contenteditable=\"true\">5.2 Intersection Performance</h3>"
    "<div class=\"editable obs-lead\" contenteditable=\"true\">"
    "<p>Intersection performance observations cover turning movement adequacy, signal phasing, and approach-lane capacity during peak periods.</p>"
    "</div>"
    f"<ul>{_note_list(intersection, 'No specific intersection observations noted. Edit to add findings on signal operation, turning movements, and geometric constraints.')}</ul>"
    "</div>"

    "<div class=\"obs-subsection\">"
    "<h3 class=\"editable-text\" contenteditable=\"true\">5.3 Detour Route Observations</h3>"
    "<div class=\"editable obs-lead\" contenteditable=\"true\">"
    "<p>Observations relating to the proposed detour route, including adequacy of alternative roads, capacity headroom, and pedestrian / cyclist impacts.</p>"
    "</div>"
    f"<ul>{_note_list(detour_notes, 'No detour-specific observations noted. Edit to add findings on detour route capacity, geometric suitability, and delay impacts.')}</ul>"
    "</div>"

    "<div class=\"obs-subsection\">"
    "<h3 class=\"editable-text\" contenteditable=\"true\">5.4 General Engineering Notes</h3>"
    "<div class=\"editable obs-lead\" contenteditable=\"true\">"
    "<p>Additional engineering observations that do not fall within the above categories, including data quality notes, assumptions, and recommendations.</p>"
    "</div>"
    f"<ul>{_note_list(general, 'No additional observations. Edit to add general engineering notes, data assumptions, or recommended follow-up actions.')}</ul>"
    "</div>"

    "</div>"
  )


def _render_data_table(
    table_data: Any,
    analysis: dict[str, str] | None = None,
  chart_items: list[dict[str, str]] | None = None,
) -> str:
    if not isinstance(table_data, dict):
        return ""

    title = _escape(table_data.get("title", "Untitled Table"))
    columns = table_data.get("columns", [])
    rows = table_data.get("rows", [])

    if not isinstance(columns, list):
        columns = []
    if not isinstance(rows, list):
        rows = []
    if not columns and not rows:
        return ""

    def _clean_cell_value(cell: Any) -> str:
      txt = _safe_text(cell, "")
      if not txt:
        return "-"
      # Keep exported table values as-is (except whitespace normalization).
      txt = re.sub(r"\s+", " ", txt).strip()
      return txt or "-"

    def _normalize_columns(raw_columns: list[Any], sample_rows: list[Any], table_title: str) -> list[str]:
      cols = [_safe_text(col, "").strip() for col in (raw_columns or [])]
      cols = [c if c else "-" for c in cols]
      row_width = 0
      for row in sample_rows:
        if isinstance(row, list):
          row_width = max(row_width, len(row))

      title_lc = _safe_text(table_title, "").lower()
      if "grouped directional summary" in title_lc and row_width == 14:
        return [
          "Year",
          "AM LV", "AM HV", "AM RT",
          "OP LV", "OP HV", "OP RT",
          "PM LV", "PM HV", "PM RT",
          "EV LV", "EV HV", "EV RT",
          "Total",
        ]

      if row_width and len(cols) > row_width:
        # Prefer right-most leaf headers when the source included multi-row header groups.
        return cols[-row_width:]
      if row_width and len(cols) < row_width:
        return cols + [f"Column {idx + 1}" for idx in range(len(cols), row_width)]
      return cols

    def _parse_numeric(cell_text: str) -> float | None:
      raw = str(cell_text or "").replace(",", "").strip()
      m = re.search(r"[-+]?\d+(?:\.\d+)?", raw)
      if not m:
        return None
      try:
        return float(m.group(0))
      except Exception:
        return None

    head_html = ""
    normalized_columns = _normalize_columns(columns, rows, title)
    if normalized_columns:
      head_html = "<thead><tr>" + "".join(f"<th class=\"editable-text editable-cell\" contenteditable=\"true\">{_escape(col)}</th>" for col in normalized_columns) + "</tr></thead>"

    body_html_parts: list[str] = []
    cleaned_rows: list[list[str]] = []

    # We no longer drop rows based on density. Render everything.
    for row in rows:
        if not isinstance(row, list) or not row:
            continue
        cleaned_row = [_clean_cell_value(cell) for cell in row]
        cleaned_rows.append(cleaned_row)
        rendered_cells = [f"<td class=\"editable-text editable-cell\" contenteditable=\"true\">{_escape(cell)}</td>" for cell in cleaned_row]
        body_html_parts.append(f"<tr>{''.join(rendered_cells)}</tr>")

    body_html = "<tbody>" + "".join(body_html_parts) + "</tbody>"

    row_count = len(cleaned_rows)
    col_count = len(normalized_columns) if normalized_columns else max((len(r) for r in cleaned_rows), default=0)

    informative_default = (analysis or {}).get(
      "summary",
      f"This table presents {_safe_text(title)} with {row_count} row(s) and {col_count} column(s). "
      "Edit this text to add assumptions, methodology, or interpretation for stakeholders."
    )

    numeric_values: list[float] = []
    for row in cleaned_rows:
      for idx, cell in enumerate(row):
        if idx == 0:
          continue
        value = _parse_numeric(cell)
        if value is not None:
          numeric_values.append(value)

    if numeric_values:
      n_min = min(numeric_values)
      n_max = max(numeric_values)
      summary_default = (analysis or {}).get(
          "scenario",
          f"Summary: {row_count} row(s) reviewed. Numeric values range from {n_min:,.2f} to {n_max:,.2f}. "
          "Edit this summary to capture key implications and recommended actions."
      )
    else:
      summary_default = (analysis or {}).get(
          "scenario",
          f"Summary: {row_count} row(s) reviewed for {_safe_text(title)}. "
          "Edit this summary to record key findings and decisions."
      )

    table_classes = "wide-table" if col_count >= 10 else ""
    chart_html = _render_embedded_charts(chart_items, title, (analysis or {}).get("chart_caption", ""))

    detail_lead = "Detailed table below sets out the supporting values used for the engineering interpretation."
    if "queue" in _normalize_title_key(title):
      detail_lead = "Detailed table below provides the supporting values behind the narrative and chart summary."
    elif "vcr" in _normalize_title_key(title) or "los" in _normalize_title_key(title):
      detail_lead = "Detailed table below provides the supporting values behind the narrative and chart summary."
    elif "detour" in _normalize_title_key(title) or "pedestrian" in _normalize_title_key(title):
      detail_lead = "Detailed table below provides the supporting values behind the narrative and chart summary."
    elif "peak hour" in _normalize_title_key(title):
      detail_lead = "Detailed table below provides the supporting values behind the narrative and chart summary."

    return (
        f"<div class=\"report-section report-block avoid-break\">"
        f"<div class=\"section-controls no-print\">"
        f"<button type=\"button\" class=\"mini-btn\" onclick=\"addTableRow(this)\">➕ Row</button> "
        f"<button type=\"button\" class=\"mini-btn\" onclick=\"removeTableLastRow(this)\">➖ Row</button> "
        f"<button type=\"button\" class=\"mini-btn\" onclick=\"addTableColumn(this)\">➕ Col</button> "
        f"<button type=\"button\" class=\"mini-btn\" onclick=\"removeTableLastColumn(this)\">➖ Col</button> "
        f"<button type=\"button\" class=\"mini-btn\" onclick=\"removeReportBlock(this)\">✕ Remove</button>"
        f"</div>"
        f"<h4 class=\"editable-text\" contenteditable=\"true\">{title}</h4>"
        f"<div class=\"editable table-note table-note-top\" contenteditable=\"true\"><p>{_escape(informative_default)}</p></div>"
        f"{chart_html}"
        f"<div class=\"table-detail-lead editable-text\" contenteditable=\"true\">{detail_lead}</div>"
      f"<table class=\"{table_classes}\">{head_html}{body_html}</table>"
        f"<div class=\"editable table-note table-note-bottom\" contenteditable=\"true\"><p>{_escape(summary_default)}</p></div>"
        f"</div>"
    )


def _render_additional_chart_blocks(
  chart_items: list[dict[str, str]],
  embedded_chart_keys: set[str],
) -> str:
  # Section 6 shows ALL charts for easy reference, regardless of whether they
  # are also embedded inline with their associated tables in Section 4.
  if not chart_items:
    return ""

  blocks: list[str] = []
  for idx, item in enumerate(chart_items):
    blocks.append(
      "<figure class=\"report-section report-block chart-block avoid-break\">"
      "<div class=\"section-controls no-print\"><button type=\"button\" class=\"mini-btn\" onclick=\"removeReportBlock(this)\">✕ Remove</button></div>"
      f"<h4 class=\"chart-title editable-text\" contenteditable=\"true\">{_escape(item['title'], f'Chart {idx + 1}')}</h4>"
      f"<img class=\"chart-img\" src=\"{item['image']}\" alt=\"{_escape(item['title'], f'Chart {idx + 1}')}\" />"
      "<figcaption class=\"editable chart-caption editable-text\" contenteditable=\"true\">"
      "Visual reference for this chart. Edit this caption to describe key findings, trends, and engineering interpretation."
      "</figcaption>"
      "</figure>"
    )

  return "".join(blocks)


def _render_commentary_block(paragraphs: list[str], conclusion_points: list[str]) -> str:
  paragraph_html = _render_paragraph_block(paragraphs)
  bullet_items = [str(item).strip() for item in conclusion_points if str(item).strip()]
  if not bullet_items:
    bullet_items = ["Insert conclusion points here."]
  conclusion_html = "".join(
    f"<li class=\"editable-text\" contenteditable=\"true\">{_escape(item)}</li>"
    for item in bullet_items
  )
  return (
    f"<div class=\"editable commentary-block\" contenteditable=\"true\">{paragraph_html}</div>"
    "<h3 class=\"editable-text\" contenteditable=\"true\">Conclusion</h3>"
    f"<ul class=\"conclusion-list\">{conclusion_html}</ul>"
  )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/report/draft")
def create_draft(req: DraftRequest) -> dict[str, str]:
    _prune_drafts()
    safe_title = str(req.title or "").strip()
    if not safe_title:
      safe_title = "TIA Report"
    if len(safe_title) > 160:
      raise HTTPException(status_code=400, detail="Title too long")

    try:
      payload_raw = json.dumps(req.payload, ensure_ascii=False)
    except (TypeError, ValueError):
      raise HTTPException(status_code=400, detail="Invalid payload")
    payload_size = len(payload_raw.encode("utf-8"))
    if payload_size > MAX_REQUEST_BODY_BYTES:
      max_mb = MAX_REQUEST_BODY_BYTES / (1024 * 1024)
      actual_mb = payload_size / (1024 * 1024)
      raise HTTPException(
        status_code=413,
        detail=(
          f"Payload too large ({actual_mb:.2f} MB). "
          f"Server limit is {max_mb:.2f} MB. "
          "Set REPORT_MAX_REQUEST_BYTES to increase the limit if needed."
        ),
      )

    now = datetime.utcnow()
    draft_id = uuid.uuid4().hex
    DRAFTS[draft_id] = {
        "title": safe_title,
        "payload": req.payload,
        "created_at": now.isoformat(timespec="seconds") + "Z",
        "created_epoch": now.timestamp(),
    }
    return {"editor_url": f"/report/editor/{draft_id}"}


@app.get("/report/editor/{draft_id}", response_class=HTMLResponse)
def editor_page(draft_id: str) -> str:
  _prune_drafts()
  if True:
    if not re.fullmatch(r"[a-f0-9]{32}", str(draft_id or "")):
      raise HTTPException(status_code=400, detail="Invalid draft id")

    draft = DRAFTS.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    title = _escape(draft.get("title", "Traffic Impact Assessment"))
    payload = draft.get("payload", {}) if isinstance(draft.get("payload"), dict) else {}
    project = payload.get("project", {}) if isinstance(payload.get("project"), dict) else {}
    inputs = payload.get("inputs", {}) if isinstance(payload.get("inputs"), dict) else {}
    results = payload.get("results", {}) if isinstance(payload.get("results"), dict) else {}
    notes = payload.get("notes", [])
    executive_content = _build_executive_content(payload)
    executive_summary_html = _render_paragraph_block(executive_content.get("executive_paragraphs", []))
    executive_notes_html = _render_notes(executive_content.get("explanation_notes", []))
    commentary_html = _render_commentary_block(
      executive_content.get("professional_commentary_paragraphs", []),
      executive_content.get("conclusion_points", []),
    )
    logo_data_url = _load_logo_data_url()
    cover_logo_html = f'<img class="cover-logo" src="{logo_data_url}" alt="Company Logo" />' if logo_data_url else ""

    variant_raw = _safe_text(payload.get("report_variant"), "").lower()
    title_hint = _safe_text(draft.get("title", "")).lower()
    is_short = variant_raw == "short" or "short report" in title_hint
    if is_short:
      report_mode_label = "Python Short Report"
    elif variant_raw == "detailed" or "detailed report" in title_hint:
      report_mode_label = "Detailed Python Report"
    else:
      report_mode_label = "Python Report"
    report_mode_label_escaped = _escape(report_mode_label)

    ctx = _build_report_context(payload)
    project_name = _escape(project.get("name", title))
    location = _escape(project.get("location", "Location Not Specified"))
    report_date = _escape(ctx.get("report_date"), datetime.now().strftime("%d/%m/%Y"))
    prepared_by = _escape(ctx.get("prepared_by"), "Planner's Name")
    cc_number = _escape(ctx.get("cc_number"), "CC0000")
    selected_site_details = ctx.get("selected_site_details", {}) if isinstance(ctx.get("selected_site_details"), dict) else {}

    notes_html = _render_notes(notes)
    engineering_obs_html = _render_engineering_observations(notes)
    raw_tables = payload.get("tables", []) if isinstance(payload.get("tables"), list) else []
    tables = [table for table in raw_tables if isinstance(table, dict)]
    table_analysis_map = {
      _normalize_title_key(item.get("title")): item
      for item in executive_content.get("table_analyses", [])
      if isinstance(item, dict) and _normalize_title_key(item.get("title"))
    }
    chart_items = _collect_chart_items(payload)
    chart_items_to_render = [] if is_short else chart_items  # short report: no charts
    hydrate_js_call = '' if is_short else 'hydrateChartsFromPayload();'
    embedded_chart_keys: set[str] = set()

    def _table_priority(table_obj: Any) -> int:
        if not isinstance(table_obj, dict):
            return 999
        title_lc = _safe_text(table_obj.get("title", "")).lower()
        if any(k in title_lc for k in ["queue", "vcr", "summary", "peak"]):
            return 0
        if any(k in title_lc for k in ["table", "results", "analysis"]):
            return 1
        return 2

    prioritized_tables = sorted(tables, key=_table_priority)
    analysis_parameters_table = {
      "table_id": "analysis_parameters",
      "title": "Analysis Parameters",
      "columns": ["Parameter", "Value"],
      "rows": [[str(key).replace("_", " ").title(), _safe_text(value)] for key, value in inputs.items()],
    }
    computed_results_table = {
      "table_id": "summary_computed_results",
      "title": "Summary of Computed Results",
      "columns": ["Metric", "Value"],
      "rows": [[str(key).replace("_", " ").title(), _safe_text(value)] for key, value in results.items()],
    }
    input_charts = _select_charts_for_table(analysis_parameters_table, chart_items_to_render)
    results_charts = _select_charts_for_table(computed_results_table, chart_items_to_render)
    embedded_chart_keys.update(_normalize_title_key(item.get("canvas_id") or item.get("title")) for item in input_charts)
    embedded_chart_keys.update(_normalize_title_key(item.get("canvas_id") or item.get("title")) for item in results_charts)
    inputs_section_html = _render_key_value_table(
      'Analysis Parameters',
      inputs,
      table_analysis_map.get(_normalize_title_key('Analysis Parameters')),
      input_charts,
    )
    results_section_html = _render_computed_results_section(
      'Summary of Computed Results',
      results,
      table_analysis_map.get(_normalize_title_key('Summary of Computed Results')),
      results_charts,
    )
    selected_site_section_html = _render_selected_site_details_section(selected_site_details)

    # Separate hourly peak-hour tables and detour tables.
    # Detour tables MUST be isolated here so they don't randomly mix into the main report.
    hourly_peak_tables: list[Any] = []
    detour_tables: list[Any] = []
    other_tables: list[Any] = []

    for table in prioritized_tables:
      title_lc = _safe_text(table.get("title", "")).lower()
      if "hourly" in title_lc and "peak hour" in title_lc:
        hourly_peak_tables.append(table)
      elif any(k in title_lc for k in ("detour", "diversion", "pedestrian detour")):
        detour_tables.append(table)
      else:
        other_tables.append(table)

    hourly_peak_blocks: list[str] = []
    for table in hourly_peak_tables:
      matched_charts = _select_charts_for_table(table, chart_items_to_render)
      embedded_chart_keys.update(_normalize_title_key(item.get("canvas_id") or item.get("title")) for item in matched_charts)
      hourly_peak_blocks.append(
        _render_data_table(
          table,
          table_analysis_map.get(_normalize_title_key(table.get('title'))),
          matched_charts,
        )
      )
    hourly_peak_section_html = "".join(hourly_peak_blocks)

    table_blocks: list[str] = []
    for table in other_tables:
      matched_charts = _select_charts_for_table(table, chart_items_to_render)
      embedded_chart_keys.update(_normalize_title_key(item.get("canvas_id") or item.get("title")) for item in matched_charts)
      table_blocks.append(
        _render_data_table(
          table,
          table_analysis_map.get(_normalize_title_key(table.get('title'))),
          matched_charts,
        )
      )
    table_sections = "".join(table_blocks)
    chart_sections = _render_additional_chart_blocks(chart_items_to_render, embedded_chart_keys)

    # Build Section 5 Detour Analysis for ALL reports using the isolated tables.
    raw_js = payload.get("raw_js_results", {}) if isinstance(payload.get("raw_js_results"), dict) else {}
    detour_route_count = int(raw_js.get("detour_route_count") or 0)

    # Notice we now pass detour_tables and chart_items_to_render explicitly
    short_detour_section_html = _build_short_detour_section(
        detour_tables, detour_route_count, table_analysis_map, chart_items_to_render
    )

    # Adjust section numbers dynamically based on whether detour data exists.
    # MUST be computed before chart_section_block which references sec_chart_num.
    sec_eng_num   = "6" if short_detour_section_html else "5"
    if is_short:
      sec_chart_num = ""  # No charts section in short report
      sec_comm_num  = "7" if short_detour_section_html else "6"
    else:
      sec_chart_num = "7" if short_detour_section_html else "6"
      sec_comm_num  = "8" if short_detour_section_html else "7"

    chart_section_block = (
      f'<h2 contenteditable="true">{sec_chart_num}. Charts</h2>\n    <div id="chartSectionContent">{chart_sections}</div>'
      if not is_short else ''
    )
    payload_json = escape(json.dumps(payload))

    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>{project_name}</title>
  <style>
    /* Professional Engineering Document Variables */
    :root {{
      --ink: #111827;
      --muted: #4b5563;
      --brand: #0f2f32;
      --accent: #1f5e63;
      --border: #d1d5db;
      --bg-light: #f9fafb;
    }}

    /* Global Styles */
    body {{
      font-family: \"Helvetica Neue\", Helvetica, Arial, sans-serif;
      margin: 0;
      background: #e5e7eb;
      color: var(--ink);
      line-height: 1.6;
    }}

    /* Print Layout Configuration */
    @page {{
      size: A4;
      margin: 20mm;
    }}

    .document-wrapper {{
      max-width: 210mm;
      margin: 20px auto;
      background: #ffffff;
      box-shadow: 0 4px 6px rgba(0,0,0,0.1);
      padding: 30px 40px;
    }}

    /* Typography */
    h1, h2, h3, h4 {{ color: var(--brand); font-family: \"Georgia\", serif; }}
    h1 {{ font-size: 2.2rem; margin-bottom: 0.5rem; text-transform: uppercase; letter-spacing: 1px; }}
    h2 {{ font-size: 1.6rem; border-bottom: 2px solid var(--accent); padding-bottom: 5px; margin-top: 2rem; page-break-after: avoid; }}
    h3 {{ font-size: 1.2rem; margin-top: 1.5rem; color: var(--accent); }}
    p {{ margin-bottom: 1rem; text-align: justify; }}
    .meta {{ color: var(--muted); font-size: 0.9rem; font-style: italic; }}

    /* Layout Components */
    .cover-page {{
      height: 90vh;
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;
      text-align: center;
    }}
    .cover-logo {{ max-width: 200px; margin-bottom: 2rem; }}
    .cover-subtitle {{ font-size: 1.4rem; color: var(--muted); margin-bottom: 3rem; }}
    .cover-details table {{ width: 60%; margin: 0 auto; border: none; }}
    .cover-details th, .cover-details td {{ border: none; padding: 8px; text-align: left; font-size: 1.1rem; }}

    .page-break {{ page-break-before: always; }}
    .avoid-break {{ page-break-inside: avoid; }}

    /* Tables */
    table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: 0.95rem; }}
    th, td {{ border: 1px solid var(--border); padding: 10px 12px; text-align: left; vertical-align: top; }}
    th {{ background-color: var(--bg-light); font-weight: 600; color: var(--brand); border-bottom: 2px solid var(--accent); }}
    .kv-table th {{ width: 35%; background-color: var(--bg-light); }}
    .wide-table {{ table-layout: fixed; font-size: 0.84rem; }}
    .wide-table th, .wide-table td {{ padding: 7px 6px; word-break: break-word; }}

    /* Interactive Elements & Editor Styles */
    .toolbar {{ display: flex; justify-content: flex-end; gap: 8px; margin-bottom: 20px; flex-wrap: wrap; }}
    .btn {{ background: var(--accent); color: white; border: none; padding: 10px 20px; font-size: 1rem; border-radius: 4px; cursor: pointer; font-weight: bold; }}
    .btn.secondary {{ background: #0f766e; }}
    .btn.ghost {{ background: #475569; }}
    .no-print {{ display: block; }}
    .section-controls {{ display: flex; justify-content: flex-end; margin: 4px 0 8px; }}
    .mini-btn {{ background: #9a3412; color: #fff; border: none; border-radius: 6px; padding: 6px 10px; font-size: 0.78rem; font-weight: 700; cursor: pointer; }}
    .mini-btn:hover {{ filter: brightness(1.06); }}
    .editable {{ padding: 10px; border: 1px dashed var(--border); background: #fafafa; min-height: 80px; transition: border 0.3s; }}
    .editable:focus {{ border: 1px solid var(--accent); outline: none; background: #fff; }}
    .editable-text, [contenteditable="true"] {{ cursor: text; user-select: text; -webkit-user-modify: read-write; }}
    th[contenteditable="true"], td[contenteditable="true"] {{ min-width: 48px; background-clip: padding-box; }}
    th[contenteditable="true"]:focus, td[contenteditable="true"]:focus, .editable-text:focus {{ outline: 2px solid rgba(31, 94, 99, 0.22); outline-offset: -2px; background: #fffef7; }}
    .table-note {{ min-height: 48px; margin: 8px 0; }}
    .table-note p {{ margin: 0; text-align: left; }}
    .table-detail-lead {{ margin: 10px 0 8px; padding: 8px 12px; border-left: 4px solid var(--accent); background: #f3f8f9; color: #244448; font-style: italic; }}
    .commentary-block {{ min-height: 140px; }}
    .conclusion-list {{ margin-top: 8px; }}

    /* Charts */
    .chart-block {{ width: 100%; max-width: 100%; margin: 0 0 16px; }}
    .chart-title {{ margin-bottom: 8px; }}
    .embedded-chart {{ margin: 12px 0 14px; padding: 12px; border: 1px solid #bfd3d8; border-radius: 8px; background: linear-gradient(180deg, #ffffff 0%, #f6fbfc 100%); box-shadow: inset 0 1px 0 rgba(255,255,255,0.9); }}
    .chart-img {{ width: auto; max-width: 100%; height: auto; border: 1px solid var(--border); display: block; margin: 10px auto; object-fit: contain; image-rendering: auto; background: #ffffff; }}
    .chart-caption {{ min-height: 48px; }}

    /* Table of Contents Styles */
    .toc-container {{ margin: 2rem 0; padding: 20px; background: #ffffff; border: 1px solid var(--border); border-radius: 4px; }}
    .toc-title {{ margin-top: 0; border-bottom: none; }}
    .toc-item {{ display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 1rem; }}
    .toc-h2 {{ font-weight: 600; color: var(--brand); margin-top: 15px; }}
    .toc-h3 {{ margin-left: 20px; color: var(--muted); font-size: 0.95rem; }}
    .toc-link {{ text-decoration: none; color: inherit; border-bottom: 1px dotted var(--muted); flex-grow: 1; margin-right: 10px; }}
    .toc-link:hover {{ color: var(--accent); border-bottom-color: var(--accent); }}

    /* Print Overrides */
    @media print {{
      * {{ -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }}
      body {{ background: #fff; }}
      .document-wrapper {{ box-shadow: none; margin: 0; padding: 0; max-width: 100%; }}
      .toolbar {{ display: none; }}
      .no-print {{ display: none !important; }}
      .editable {{ border: none; background: transparent; padding: 0; }}
      .toc-container {{ border: none; padding: 0; }}
      .toc-link {{ border-bottom: none; }}
      .embedded-chart {{ border: 1px solid #bfd3d8; background: #fff; }}
      .detour-sub-block {{ border: 1px solid var(--border); background: #fff; }}
    }}

    /* Three-column results table */
    .results-3col .context-col {{ color: var(--muted); font-size: 0.88rem; font-style: italic; min-width: 140px; }}

    /* Detour subsection blocks */
    .detour-subsections {{ margin-top: 8px; }}
    .detour-sub-block {{ margin: 16px 0; padding: 12px 16px; border-left: 4px solid var(--accent); background: #f3f8f9; border-radius: 4px; }}
    .detour-sub-block h5 {{ margin: 0 0 8px; color: var(--brand); font-size: 1rem; }}

    /* Engineering observations */
    .obs-subsection {{ margin-bottom: 16px; }}
    .obs-lead {{ min-height: 48px; margin-bottom: 8px; }}

    /* Table add/remove controls */
    .section-controls .mini-btn {{ margin-right: 4px; }}
    .section-controls .mini-btn:last-child {{ margin-right: 0; }}
  </style>
</head>
<body>
  <div class=\"toolbar\" style=\"max-width: 210mm; margin: 20px auto 0;\">
    <button class=\"btn\" onclick=\"window.print()\">🖨️ Print to PDF</button>
    <button class=\"btn secondary\" onclick=\"downloadEditsProfile()\">💾 Download Edits JSON</button>
    <label for=\"editsProfileInput\" class=\"btn ghost\" style=\"display:inline-flex; align-items:center;\">📂 Load Edits JSON</label>
    <input id=\"editsProfileInput\" type=\"file\" accept=\".json,application/json\" style=\"display:none;\" onchange=\"loadEditsProfileFromInput(event)\" />
  </div>

  <main class=\"document-wrapper\">

    <div class=\"cover-page\">
      {cover_logo_html}
      <h1 contenteditable=\"true\">{project_name}</h1>
        <div class="cover-subtitle" contenteditable="true">Traffic Impact Assessment Report</div>

      <div class=\"cover-details\">
        <table>
          <tr><th contenteditable=\"true\">Location:</th><td contenteditable=\"true\">{location}</td></tr>
          <tr><th contenteditable=\"true\">Date Prepared:</th><td contenteditable=\"true\">{report_date}</td></tr>
          <tr><th contenteditable=\"true\">Prepared By:</th><td contenteditable=\"true\">{prepared_by}</td></tr>
          <tr><th contenteditable=\"true\">CC Number:</th><td contenteditable=\"true\" style=\"font-family: monospace; font-size: 0.8rem;\">{cc_number}</td></tr>
        </table>
      </div>
    </div>

    <div class=\"page-break\"></div>

    <div class=\"toc-container avoid-break\">
      <h2 class=\"toc-title\">Table of Contents</h2>
      <div id=\"toc-content\"></div>
    </div>

    <div class=\"page-break\"></div>

    <h2 contenteditable=\"true\">1. Executive Summary</h2>
    <div class=\"editable\" contenteditable=\"true\">
      {executive_summary_html}
      <p><em>Click here to refine the executive narrative, project-specific implications, and stakeholder-facing conclusions.</em></p>
    </div>

    <h2 class=\"avoid-break\" contenteditable=\"true\">2. Executive Explanation Notes</h2>
    <ul>{executive_notes_html}</ul>

    <h2 contenteditable=\"true\">3. Design &amp; Traffic Inputs</h2>
    {inputs_section_html}
    {selected_site_section_html}

    <div class=\"page-break\"></div>

    <h2 contenteditable=\"true\">4. Traffic Analysis &amp; Results</h2>
    {results_section_html}
    {hourly_peak_section_html}

    {table_sections}

    {short_detour_section_html}

    <div class=\"page-break\"></div>

    <h2 contenteditable=\"true\">{sec_eng_num}. Engineering Observations &amp; Notes</h2>
    {engineering_obs_html}

    {chart_section_block}

    <h2 contenteditable=\"true\">{sec_comm_num}. Professional Commentary &amp; Conclusion</h2>
    {commentary_html}

  </main>

  <script id=\"reportPayloadData\" type=\"application/json\">{payload_json}</script>
  <script>
    function getEmbeddedReportPayload() {{
      const el = document.getElementById('reportPayloadData');
      if (!el) return null;
      try {{
        return JSON.parse(el.textContent || '{{}}');
      }} catch (_err) {{
        return null;
      }}
    }}

    function buildChartMarkup(title, imageDataUrl, index) {{
      const safeTitle = String(title || ('Chart ' + (index + 1)));
      const figure = document.createElement('figure');
      figure.className = 'report-section report-block chart-block avoid-break';
      figure.innerHTML =
        '<div class="section-controls no-print"><button type="button" class="mini-btn" onclick="removeReportBlock(this)">Remove Chart</button></div>' +
        '<h4 class="chart-title editable-text" contenteditable="true"></h4>' +
        '<img class="chart-img" alt="" />' +
        '<figcaption class="editable chart-caption editable-text" contenteditable="true">Describe what this chart shows, assumptions, and interpretation for stakeholders.</figcaption>';
      const titleEl = figure.querySelector('h4');
      const imgEl = figure.querySelector('img');
      if (titleEl) titleEl.textContent = safeTitle;
      if (imgEl) {{
        imgEl.src = imageDataUrl;
        imgEl.alt = safeTitle;
      }}
      return figure;
    }}

    function hydrateChartsFromPayload() {{
      const chartWrap = document.getElementById('chartSectionContent');
      if (!chartWrap) return;

      const existingImages = chartWrap.querySelectorAll('img.chart-img');
      if (existingImages.length > 0) return;

      const payload = getEmbeddedReportPayload();
      if (!payload || typeof payload !== 'object') return;

      const charts = Array.isArray(payload.charts)
        ? payload.charts.filter((item) => item && String(item.image_data_url || '').startsWith('data:image/'))
        : [];
      const fallback = String(payload.chart_image_data_url || '');

      if (charts.length === 0 && !fallback.startsWith('data:image/')) return;

      chartWrap.innerHTML = '';
      if (charts.length > 0) {{
        charts.forEach((chart, index) => {{
          chartWrap.appendChild(buildChartMarkup(chart.title || ('Chart ' + (index + 1)), chart.image_data_url, index));
        }});
      }} else {{
        chartWrap.appendChild(buildChartMarkup('Primary Chart', fallback, 0));
      }}
    }}

    function enableStrongEditability() {{
      const editableSelectors = [
        'main h1', 'main h2:not(.toc-title)', 'main h3', 'main h4',
        'main p', 'main li', 'main span',
        'main th', 'main td', 'main figcaption', 'main .cover-subtitle'
      ];

      document.querySelectorAll(editableSelectors.join(',')).forEach((el) => {{
        if (!el || el.closest('.section-controls') || el.closest('.toolbar')) return;
        if (el.tagName && el.tagName.toLowerCase() === 'img') return;
        el.setAttribute('contenteditable', 'true');
        el.classList.add('editable-text');
      }});
    }}

    function refreshToc() {{
      const tocContent = document.getElementById("toc-content");
      if (!tocContent) return;

      const headers = Array.from(document.querySelectorAll("main h2:not(.toc-title), main h3, main h4.chart-title"))
        .filter((header) => header && header.isConnected)
        .filter((header) => String(header.innerText || '').trim().length > 0);

      if (headers.length === 0) {{
        tocContent.innerHTML = '<div class="toc-item toc-h3">No sections available.</div>';
        return;
      }}

      let tocHTML = "";
      headers.forEach((header, index) => {{
        if (!header.id) {{
          const safeText = header.innerText.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
          header.id = "sec-" + index + "-" + safeText;
        }}

        let levelClass = 'toc-h3';
        if (header.tagName.toLowerCase() === 'h2') levelClass = 'toc-h2';
        tocHTML += '<div class="toc-item ' + levelClass + '">' +
                     '<a href="#' + header.id + '" class="toc-link">' + header.innerText + '</a>' +
                   '</div>';
      }});

      tocContent.innerHTML = tocHTML;
    }}

    function bindTocAutoRefresh() {{
      const reportMain = document.querySelector('main.document-wrapper');
      if (!reportMain) return;

      let refreshHandle = null;
      const scheduleRefresh = () => {{
        if (refreshHandle) return;
        refreshHandle = window.setTimeout(() => {{
          refreshHandle = null;
          refreshToc();
        }}, 80);
      }};

      const observer = new MutationObserver((mutations) => {{
        for (const mutation of mutations) {{
          if (mutation.type === 'childList') {{
            scheduleRefresh();
            return;
          }}
          if (mutation.type === 'characterData') {{
            const parent = mutation.target && mutation.target.parentElement;
            if (parent && parent.matches && parent.matches('h2, h3, h4.chart-title')) {{
              scheduleRefresh();
              return;
            }}
          }}
        }}
      }});

      observer.observe(reportMain, {{
        childList: true,
        subtree: true,
        characterData: true
      }});

      document.addEventListener('input', (event) => {{
        const target = event && event.target;
        if (target && target.matches && target.matches('h2, h3, h4.chart-title')) {{
          scheduleRefresh();
        }}
      }});
    }}

    function _buildNodePath(node, stopNode) {{
      if (!node || !(node instanceof Element)) return '';
      const segments = [];
      let current = node;
      while (current && current !== stopNode) {{
        const parent = current.parentElement;
        if (!parent) break;
        const siblings = Array.from(parent.children).filter((child) => child.tagName === current.tagName);
        const idx = Math.max(0, siblings.indexOf(current));
        segments.push(current.tagName.toLowerCase() + ':' + idx);
        current = parent;
      }}
      return segments.reverse().join('/');
    }}

    function _resolveNodePath(path, stopNode) {{
      if (!path || !stopNode) return null;
      const segments = String(path).split('/').filter(Boolean);
      let cursor = stopNode;
      for (const segment of segments) {{
        const parts = segment.split(':');
        if (parts.length !== 2) return null;
        const tag = parts[0].toUpperCase();
        const index = Number(parts[1]);
        if (!Number.isFinite(index) || index < 0) return null;
        const matches = Array.from(cursor.children).filter((child) => child.tagName === tag);
        if (!matches[index]) return null;
        cursor = matches[index];
      }}
      return cursor;
    }}

    function _isNarrativeEditableTarget(el) {{
      if (!(el instanceof HTMLElement)) return false;
      if (!el.isContentEditable) return false;
      if (el.closest('.toolbar') || el.closest('.section-controls')) return false;
      if (el.closest('#toc-content')) return false;
      if (el.matches('td, th')) return false;
      return true;
    }}

    function _collectNarrativeEditEntries() {{
      const root = document.querySelector('main.document-wrapper');
      if (!root) return [];
      const candidates = Array.from(root.querySelectorAll('[contenteditable="true"]')).filter(_isNarrativeEditableTarget);
      return candidates.map((el) => {{
        const path = _buildNodePath(el, root);
        return {{
          path,
          html: el.innerHTML,
          text: el.textContent || ''
        }};
      }}).filter((item) => item.path);
    }}

    function downloadEditsProfile() {{
      const payload = getEmbeddedReportPayload() || {{}};
      const profile = {{
        schema: 'tia-python-edit-profile-v1',
        exported_at: new Date().toISOString(),
        project_name: (payload.project && payload.project.name) || '',
        report_variant: payload.report_variant || '',
        entries: _collectNarrativeEditEntries()
      }};

      const blob = new Blob([JSON.stringify(profile, null, 2)], {{ type: 'application/json' }});
      const link = document.createElement('a');
      const stamp = new Date().toISOString().replace(/[:.]/g, '-');
      const objectUrl = URL.createObjectURL(blob);
      link.href = objectUrl;
      link.download = `tia-python-edits-${{stamp}}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(objectUrl);
    }}

    function applyEditsProfile(profile) {{
      if (!profile || profile.schema !== 'tia-python-edit-profile-v1' || !Array.isArray(profile.entries)) {{
        throw new Error('Invalid edits profile format.');
      }}
      const root = document.querySelector('main.document-wrapper');
      if (!root) throw new Error('Report root not found.');

      let applied = 0;
      profile.entries.forEach((entry) => {{
        if (!entry || !entry.path) return;
        const target = _resolveNodePath(entry.path, root);
        if (!target || !_isNarrativeEditableTarget(target)) return;
        target.innerHTML = String(entry.html || '');
        applied += 1;
      }});
      refreshToc();
      return applied;
    }}

    function loadEditsProfileFromInput(event) {{
      const input = event && event.target;
      const file = input && input.files && input.files[0];
      if (!file) return;

      const reader = new FileReader();
      reader.onload = function() {{
        try {{
          const parsed = JSON.parse(String(reader.result || '{{}}'));
          const appliedCount = applyEditsProfile(parsed);
          window.alert(`Applied ${{appliedCount}} text edit(s). Table values remain from the current recalculated data.`);
        }} catch (err) {{
          window.alert(`Could not load edits JSON: ${{(err && err.message) || 'Unknown error'}}`);
        }} finally {{
          input.value = '';
        }}
      }};
      reader.readAsText(file);
    }}

    // Track the last focused cell so row/col operations act at the selection point.
    let _lastFocusedCell = null;
    document.addEventListener('focusin', function(e) {{
      const cell = e.target && e.target.closest('td, th');
      if (cell && cell.closest('table')) _lastFocusedCell = cell;
    }}, true);

    function _getTargetTable(btn) {{
      const block = btn && btn.closest('.report-block');
      if (!block) return {{ table: null, block: null }};
      // Prefer the table that contains the focused cell, if it's inside this block.
      if (_lastFocusedCell && block.contains(_lastFocusedCell)) {{
        const t = _lastFocusedCell.closest('table');
        if (t) return {{ table: t, block }};
      }}
      return {{ table: block.querySelector('table'), block }};
    }}

    function addTableRow(btn) {{
      const {{ table }} = _getTargetTable(btn);
      if (!table) return;

      // Insert after the row containing the focused cell, else append to tbody.
      const targetRow = _lastFocusedCell && table.contains(_lastFocusedCell)
        ? _lastFocusedCell.closest('tr')
        : null;

      const tbody = table.querySelector('tbody') || table;
      const refRow = targetRow || tbody.querySelector('tr:last-child');
      const cellCount = refRow ? refRow.querySelectorAll('td, th').length : 2;

      const newRow = document.createElement('tr');
      for (let i = 0; i < cellCount; i++) {{
        const td = document.createElement('td');
        td.className = 'editable-text editable-cell';
        td.contentEditable = 'true';
        td.textContent = 'Edit';
        newRow.appendChild(td);
      }}

      if (targetRow && targetRow.parentNode) {{
        targetRow.parentNode.insertBefore(newRow, targetRow.nextSibling);
      }} else {{
        tbody.appendChild(newRow);
      }}
      // Focus first cell of new row.
      const firstCell = newRow.querySelector('td');
      if (firstCell) {{ firstCell.focus(); _lastFocusedCell = firstCell; }}
    }}

    function removeTableLastRow(btn) {{
      const {{ table }} = _getTargetTable(btn);
      if (!table) return;

      // Remove the row containing the focused cell if it's a tbody row; else last tbody row.
      const tbody = table.querySelector('tbody') || table;
      const tbodyRows = tbody.querySelectorAll('tr');
      if (tbodyRows.length <= 1) return; // keep at least one row

      const targetRow = _lastFocusedCell && tbody.contains(_lastFocusedCell)
        ? _lastFocusedCell.closest('tr')
        : null;

      const rowToRemove = (targetRow && tbody.contains(targetRow))
        ? targetRow
        : tbodyRows[tbodyRows.length - 1];

      if (rowToRemove) {{
        _pushUndo({{ type: 'row', element: rowToRemove, parent: rowToRemove.parentNode, nextSibling: rowToRemove.nextSibling }});
        rowToRemove.remove();
      }}
      _lastFocusedCell = null;
    }}

    function addTableColumn(btn) {{
      const {{ table }} = _getTargetTable(btn);
      if (!table) return;

      // Determine insertion index from focused cell; default to end.
      let insertAfterIndex = -1; // -1 means append
      if (_lastFocusedCell && table.contains(_lastFocusedCell)) {{
        const cells = Array.from(_lastFocusedCell.closest('tr').querySelectorAll('td, th'));
        insertAfterIndex = cells.indexOf(_lastFocusedCell);
      }}

      const allRows = table.querySelectorAll('tr');
      allRows.forEach((row, rowIdx) => {{
        const cells = row.querySelectorAll('td, th');
        const isHeaderRow = row.closest('thead') != null;
        const cell = isHeaderRow ? document.createElement('th') : document.createElement('td');
        cell.className = 'editable-text editable-cell';
        cell.contentEditable = 'true';
        cell.textContent = isHeaderRow ? 'New Column' : 'Edit';

        if (insertAfterIndex >= 0 && insertAfterIndex < cells.length) {{
          cells[insertAfterIndex].insertAdjacentElement('afterend', cell);
        }} else {{
          row.appendChild(cell);
        }}
      }});
    }}

    function removeTableLastColumn(btn) {{
      const {{ table }} = _getTargetTable(btn);
      if (!table) return;

      // Find column index from focused cell; default to last column.
      let removeIndex = -1; // -1 means last
      if (_lastFocusedCell && table.contains(_lastFocusedCell)) {{
        const cells = Array.from(_lastFocusedCell.closest('tr').querySelectorAll('td, th'));
        removeIndex = cells.indexOf(_lastFocusedCell);
      }}

      const allRows = table.querySelectorAll('tr');
      const removedCells = [];
      allRows.forEach(row => {{
        const cells = row.querySelectorAll('td, th');
        if (cells.length <= 1) return; // keep at least one column
        const idx = (removeIndex >= 0 && removeIndex < cells.length) ? removeIndex : cells.length - 1;
        const cell = cells[idx];
        removedCells.push({{ element: cell, parent: row, nextSibling: cell.nextSibling || null }});
      }});
      if (removedCells.length > 0) {{
        _pushUndo({{ type: 'column', cells: removedCells }});
        removedCells.forEach(function(item) {{ item.element.remove(); }});
      }}
      _lastFocusedCell = null;
    }}

    const _undoStack = [];
    const _MAX_UNDO = 50;

    function _pushUndo(entry) {{
      _undoStack.push(entry);
      if (_undoStack.length > _MAX_UNDO) _undoStack.shift();
    }}

    function removeReportBlock(btn) {{
      const block = btn && btn.closest('.report-block');
      if (!block) return;
      _pushUndo({{ type: 'block', element: block, parent: block.parentNode, nextSibling: block.nextSibling }});
      block.remove();
      refreshToc();
    }}

    document.addEventListener('keydown', function(e) {{
      if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {{
        const active = document.activeElement;
        if (active && active.isContentEditable) return;
        if (!_undoStack.length) return;
        e.preventDefault();
        const last = _undoStack.pop();
        if (last.type === 'block' || last.type === 'row') {{
          if (last.parent && last.parent.isConnected) {{
            last.parent.insertBefore(last.element, last.nextSibling || null);
          }} else if (last.parent) {{
            last.parent.appendChild(last.element);
          }}
          refreshToc();
        }} else if (last.type === 'column') {{
          last.cells.forEach(function(item) {{
            if (item.parent && item.parent.isConnected) {{
              item.parent.insertBefore(item.element, item.nextSibling || null);
            }} else if (item.parent) {{
              item.parent.appendChild(item.element);
            }}
          }});
        }}
      }}
    }});

    document.addEventListener("DOMContentLoaded", function() {{
      {hydrate_js_call}
      enableStrongEditability();
      const payload = getEmbeddedReportPayload();
      if (payload && payload.editor_edits_profile) {{
        try {{
          applyEditsProfile(payload.editor_edits_profile);
        }} catch (_err) {{
          // Ignore invalid profile payloads and continue loading the report.
        }}
      }}
      refreshToc();
      bindTocAutoRefresh();
    }});
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    
    # Run the server on localhost:8060 by default
    # Set environment variables to customize:
    # - HOST: defaults to 127.0.0.1 (localhost)
    # - PORT: defaults to 8060 (matches frontend REPORT_SERVICE_BASE_URL)
    # - RELOAD: set to "true" for auto-reload in development
    
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8060"))
    reload = os.environ.get("RELOAD", "").lower() == "true"
    
    print(f"Starting TIA Report Service at http://{host}:{port}")
    print(f"   Health check: http://{host}:{port}/health")
    print(f"   API docs: http://{host}:{port}/docs")
    
    uvicorn.run(
        "report_service:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info"
    )
