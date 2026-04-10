from __future__ import annotations

import json
import uuid
import base64
import re
import os
import urllib.error
import urllib.request
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel


app = FastAPI(title="TIA Python Report Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DraftRequest(BaseModel):
    title: str = "TIA Report"
    payload: dict[str, Any]


DRAFTS: dict[str, dict[str, Any]] = {}


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


def _build_report_context(payload: dict[str, Any]) -> dict[str, Any]:
  project = payload.get("project", {}) if isinstance(payload.get("project"), dict) else {}
  inputs = payload.get("inputs", {}) if isinstance(payload.get("inputs"), dict) else {}
  results = payload.get("results", {}) if isinstance(payload.get("results"), dict) else {}
  raw = payload.get("raw_js_results", {}) if isinstance(payload.get("raw_js_results"), dict) else {}
  notes = payload.get("notes", []) if isinstance(payload.get("notes"), list) else []

  d1_vadt = _to_float(inputs.get("d1_vadt") or raw.get("d1_vadt"))
  d2_vadt = _to_float(inputs.get("d2_vadt") or raw.get("d2_vadt"))
  total_vadt = _to_float(inputs.get("aadt") or raw.get("vadt"))
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
    "report_date": _safe_text(project.get("report_date"), datetime.now().strftime("%Y-%m-%d")),
    "road_mode": _safe_text(inputs.get("road_operation_mode"), "TWO-WAY"),
    "base_year": _safe_text(raw.get("base_year"), "Current year"),
    "total_vadt": total_vadt,
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
    "columns": analysis["columns"][:12],
    "row_count": analysis["row_count"],
    "column_count": analysis["column_count"],
    "numeric_count": analysis["numeric_count"],
    "numeric_min": analysis["numeric_min"],
    "numeric_max": analysis["numeric_max"],
    "labels": analysis["labels"],
    "sample_rows": analysis["sample_rows"],
    "top_numeric_cells": top_cells,
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


def _build_fallback_table_analysis(table_data: dict[str, Any]) -> dict[str, str]:
  analysis = _analyze_table_data(table_data)
  title = analysis["title"]

  summary_parts = [
    f"{title} presents {analysis['row_count']} row(s) across {analysis['column_count']} column(s)."
  ]
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
  if ctx["total_vadt"] is not None:
    traffic_bits.append(f"the modeled daily traffic volume is approximately {_format_number(ctx['total_vadt'])} vehicles per day")
  if ctx["d1_vadt"] is not None and ctx["d2_vadt"] is not None:
    d1_share = f", {ctx['d1_pct']:.1f}% of total" if ctx["d1_pct"] is not None else ""
    d2_share = f", {ctx['d2_pct']:.1f}% of total" if ctx["d2_pct"] is not None else ""
    traffic_bits.append(
      f"directional demand is split between D1 ({_format_number(ctx['d1_vadt'])} vpd{d1_share}) and D2 ({_format_number(ctx['d2_vadt'])} vpd{d2_share})"
    )
  if ctx["growth_rate"] is not None:
    traffic_bits.append(f"an annual growth rate of {_format_number(ctx['growth_rate'], 2)}% has been applied")
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
    notes.append(
      f"Demand profile: total modeled traffic is about {_format_number(ctx['total_vadt'])} vpd, with D1 at {_format_number(ctx['d1_vadt'])} vpd and D2 at {_format_number(ctx['d2_vadt'])} vpd."
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
    "task": "Write a detailed traffic engineering report narrative, including per-table summaries and a final professional conclusion.",
    "instructions": [
      "Return strict JSON only.",
      "Use Australian traffic engineering wording.",
      "State where the assessment applies and what the modeled outputs mean operationally.",
      "Do not invent values beyond the supplied context.",
      "Provide 3 or 4 executive summary paragraphs and 4 to 6 explanation notes.",
      "For every supplied table title, return a human summary paragraph, a scenario paragraph describing when the condition typically occurs, and a short chart caption.",
      "Use the exact supplied table titles in the response so they can be mapped deterministically.",
      "Provide 3 professional commentary paragraphs and 3 to 5 conclusion points."
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
    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}",
    data=request_body,
    headers={"Content-Type": "application/json"},
    method="POST",
  )

  try:
    with urllib.request.urlopen(request, timeout=18) as response:
      body = response.read().decode("utf-8")
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
  fallback_table_analyses = [_build_fallback_table_analysis(table) for table in report_tables]
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
    fallback_item = fallback_map.get(title_key, _build_fallback_table_analysis(table))
    generated_item = generated_map.get(title_key, {})
    merged_table_analyses.append(
      {
        "title": fallback_item["title"],
        "summary": generated_item.get("summary") or fallback_item["summary"],
        "scenario": generated_item.get("scenario") or fallback_item["scenario"],
        "chart_caption": generated_item.get("chart_caption") or fallback_item["chart_caption"],
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
  raw_charts = payload.get("charts", []) if isinstance(payload.get("charts"), list) else []
  chart_items: list[dict[str, str]] = []

  for idx, chart in enumerate(raw_charts):
    if not isinstance(chart, dict):
      continue
    image_data_url = _safe_text(chart.get("image_data_url"), "")
    if not image_data_url.startswith("data:image/"):
      continue
    chart_items.append(
      {
        "title": _safe_text(chart.get("title"), f"Chart {idx + 1}"),
        "image": image_data_url,
        "canvas_id": _safe_text(chart.get("canvas_id"), ""),
      }
    )

  if not chart_items:
    fallback = _safe_text(payload.get("chart_image_data_url"), "")
    if fallback.startswith("data:image/"):
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

  if table_id in {"analysis_parameters"} and "macrohourlychart" in canvas_id:
    score += 80
  if table_id in {"summary_computed_results"} and "managementvizcanvas" in canvas_id:
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
  if "macrohourlychart" in canvas_id and ("directional" in title_key or "analysis parameters" in title_key):
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
    "<figure class=\"embedded-chart avoid-break\">"
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
    f"<div class=\"section-controls no-print\"><button type=\"button\" class=\"mini-btn\" onclick=\"removeReportBlock(this)\">Remove Block</button></div>"
    f"<h3 class=\"editable-text\" contenteditable=\"true\">{_escape(title)}</h3>"
    f"<div class=\"editable table-note table-note-top\" contenteditable=\"true\"><p>{summary_text}</p></div>"
    f"{chart_html}"
    "<div class=\"table-detail-lead editable-text\" contenteditable=\"true\">Detailed table below sets out the supporting values used for the engineering interpretation.</div>"
    f"<table class=\"kv-table\"><tbody>{''.join(rows)}</tbody></table>"
    f"<div class=\"editable table-note table-note-bottom\" contenteditable=\"true\"><p>{scenario_text}</p></div>"
    f"</div>"
  )


def _render_notes(notes: Any) -> str:
  if not isinstance(notes, list) or not notes:
    return "<li class=\"editable-text\" contenteditable=\"true\">No supplementary notes provided.</li>"
  return "".join(f"<li class=\"editable-text\" contenteditable=\"true\">{_escape(item)}</li>" for item in notes)


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

    return (
        f"<div class=\"report-section report-block avoid-break\">"
        f"<div class=\"section-controls no-print\"><button type=\"button\" class=\"mini-btn\" onclick=\"removeReportBlock(this)\">Remove Block</button></div>"
        f"<h4 class=\"editable-text\" contenteditable=\"true\">{title}</h4>"
        f"<div class=\"editable table-note table-note-top\" contenteditable=\"true\"><p>{_escape(informative_default)}</p></div>"
        f"{chart_html}"
        "<div class=\"table-detail-lead editable-text\" contenteditable=\"true\">Detailed table below provides the supporting values behind the narrative and chart summary.</div>"
      f"<table class=\"{table_classes}\">{head_html}{body_html}</table>"
        f"<div class=\"editable table-note table-note-bottom\" contenteditable=\"true\"><p>{_escape(summary_default)}</p></div>"
        f"</div>"
    )


def _render_additional_chart_blocks(
  chart_items: list[dict[str, str]],
  embedded_chart_keys: set[str],
) -> str:
  remaining = [
    item for item in chart_items
    if _normalize_title_key(item.get("canvas_id") or item.get("title")) not in embedded_chart_keys
  ]
  if not remaining:
    return (
      "<div class=\"report-section avoid-break\">"
      "<div class=\"editable\" contenteditable=\"true\"><p>All core charts have been embedded with their corresponding tables. No additional standalone charts are required for this draft.</p></div>"
      "</div>"
    )

  blocks: list[str] = []
  for idx, item in enumerate(remaining):
    blocks.append(
      "<figure class=\"report-section report-block chart-block avoid-break\">"
      "<div class=\"section-controls no-print\"><button type=\"button\" class=\"mini-btn\" onclick=\"removeReportBlock(this)\">Remove Chart</button></div>"
      f"<h4 class=\"chart-title editable-text\" contenteditable=\"true\">{_escape(item['title'], f'Chart {idx + 1}')}</h4>"
      f"<img class=\"chart-img\" src=\"{item['image']}\" alt=\"{_escape(item['title'], f'Chart {idx + 1}')}\" />"
      "<figcaption class=\"editable chart-caption editable-text\" contenteditable=\"true\">"
      "Additional visual reference retained separately from the main table narrative."
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
    draft_id = uuid.uuid4().hex
    DRAFTS[draft_id] = {
        "title": req.title,
        "payload": req.payload,
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    return {"editor_url": f"/report/editor/{draft_id}"}


@app.get("/report/editor/{draft_id}", response_class=HTMLResponse)
def editor_page(draft_id: str) -> str:
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
    if variant_raw == "short" or "short report" in title_hint:
      report_mode_label = "Python Short Report"
    elif variant_raw == "detailed" or "detailed report" in title_hint:
      report_mode_label = "Detailed Python Report"
    else:
      report_mode_label = "Python Report"
    report_mode_label_escaped = _escape(report_mode_label)

    project_name = _escape(project.get("name", title))
    location = _escape(project.get("location", "Location Not Specified"))
    report_date = _escape(project.get("report_date", datetime.now().strftime("%B %d, %Y")))
    prepared_by = _escape(project.get("prepared_by", "Engineering Team"))

    notes_html = _render_notes(notes)
    tables = payload.get("tables", []) if isinstance(payload.get("tables"), list) else []
    table_analysis_map = {
      _normalize_title_key(item.get("title")): item
      for item in executive_content.get("table_analyses", [])
      if isinstance(item, dict) and _normalize_title_key(item.get("title"))
    }
    chart_items = _collect_chart_items(payload)
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
    input_charts = _select_charts_for_table(analysis_parameters_table, chart_items)
    results_charts = _select_charts_for_table(computed_results_table, chart_items)
    embedded_chart_keys.update(_normalize_title_key(item.get("canvas_id") or item.get("title")) for item in input_charts)
    embedded_chart_keys.update(_normalize_title_key(item.get("canvas_id") or item.get("title")) for item in results_charts)
    inputs_section_html = _render_key_value_table(
      'Analysis Parameters',
      inputs,
      table_analysis_map.get(_normalize_title_key('Analysis Parameters')),
      input_charts,
    )
    results_section_html = _render_key_value_table(
      'Summary of Computed Results',
      results,
      table_analysis_map.get(_normalize_title_key('Summary of Computed Results')),
      results_charts,
    )
    table_blocks: list[str] = []
    for table in prioritized_tables:
      matched_charts = _select_charts_for_table(table, chart_items)
      embedded_chart_keys.update(_normalize_title_key(item.get("canvas_id") or item.get("title")) for item in matched_charts)
      table_blocks.append(
        _render_data_table(
          table,
          table_analysis_map.get(_normalize_title_key(table.get('title'))),
          matched_charts,
        )
      )
    table_sections = "".join(table_blocks)
    chart_sections = _render_additional_chart_blocks(chart_items, embedded_chart_keys)
    payload_json = escape(json.dumps(payload))

    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>{project_name} - {report_mode_label_escaped}</title>
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
    .toolbar {{ display: flex; justify-content: flex-end; margin-bottom: 20px; }}
    .btn {{ background: var(--accent); color: white; border: none; padding: 10px 20px; font-size: 1rem; border-radius: 4px; cursor: pointer; font-weight: bold; }}
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
    }}
  </style>
</head>
<body>
  <div class=\"toolbar\" style=\"max-width: 210mm; margin: 20px auto 0;\">
    <button class=\"btn\" onclick=\"window.print()\">🖨️ Print to PDF</button>
  </div>

  <main class=\"document-wrapper\">

    <div class=\"cover-page\">
      {cover_logo_html}
      <h1 contenteditable=\"true\">{project_name}</h1>
        <div class="cover-subtitle" contenteditable="true">Traffic Impact Assessment Report - {report_mode_label_escaped}</div>

      <div class=\"cover-details\">
        <table>
          <tr><th contenteditable=\"true\">Location:</th><td contenteditable=\"true\">{location}</td></tr>
          <tr><th contenteditable=\"true\">Date Prepared:</th><td contenteditable=\"true\">{report_date}</td></tr>
            <tr><th contenteditable="true">Report Mode:</th><td contenteditable="true">{report_mode_label_escaped}</td></tr>
          <tr><th contenteditable=\"true\">Prepared By:</th><td contenteditable=\"true\">{prepared_by}</td></tr>
          <tr><th contenteditable=\"true\">Draft Reference:</th><td contenteditable=\"true\" style=\"font-family: monospace; font-size: 0.8rem;\">{escape(draft_id)}</td></tr>
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

    <h2 contenteditable=\"true\">3. Design & Traffic Inputs</h2>
    {inputs_section_html}

    <div class=\"page-break\"></div>

    <h2 contenteditable=\"true\">4. Traffic Analysis & Results</h2>
    {results_section_html}

    {table_sections}

    <div class=\"page-break\"></div>

    <h2 contenteditable=\"true\">5. Engineering Observations & Notes</h2>
    <ul>{notes_html}</ul>

    <h2 contenteditable=\"true\">6. Charts</h2>
    <div id=\"chartSectionContent\">{chart_sections}</div>

    <h2 contenteditable=\"true\">7. Professional Commentary & Conclusion</h2>
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

    function removeReportBlock(btn) {{
      const block = btn && btn.closest('.report-block');
      if (!block) return;
      block.remove();
      refreshToc();
    }}

    document.addEventListener("DOMContentLoaded", function() {{
      hydrateChartsFromPayload();
      enableStrongEditability();
      refreshToc();
      bindTocAutoRefresh();
    }});
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    
    # Run the server on localhost:8000 by default
    # Set environment variables to customize:
    # - HOST: defaults to 127.0.0.1 (localhost)
    # - PORT: defaults to 8000
    # - RELOAD: set to "true" for auto-reload in development
    
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    reload = os.environ.get("RELOAD", "").lower() == "true"
    
    print(f"🚀 Starting TIA Report Service at http://{host}:{port}")
    print(f"   Health check: http://{host}:{port}/health")
    print(f"   API docs: http://{host}:{port}/docs")
    
    uvicorn.run(
        "report_service:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info"
    )
