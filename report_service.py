from __future__ import annotations

import json
import uuid
import base64
import re
import os
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


def _render_key_value_table(title: str, data: dict[str, Any]) -> str:
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

    return (
        f"<div class=\"report-section report-block avoid-break\">"
        f"<div class=\"section-controls no-print\"><button type=\"button\" class=\"mini-btn\" onclick=\"removeReportBlock(this)\">Remove Table</button></div>"
        f"<h3 class=\"editable-text\" contenteditable=\"true\">{_escape(title)}</h3>"
        f"<table class=\"kv-table\"><tbody>{''.join(rows)}</tbody></table>"
        f"</div>"
    )


def _render_notes(notes: Any) -> str:
  if not isinstance(notes, list) or not notes:
    return "<li class=\"editable-text\" contenteditable=\"true\">No supplementary notes provided.</li>"
  return "".join(f"<li class=\"editable-text\" contenteditable=\"true\">{_escape(item)}</li>" for item in notes)


def _render_data_table(table_data: Any) -> str:
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

    informative_default = (
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
      summary_default = (
          f"Summary: {row_count} row(s) reviewed. Numeric values range from {n_min:,.2f} to {n_max:,.2f}. "
          "Edit this summary to capture key implications and recommended actions."
      )
    else:
      summary_default = (
          f"Summary: {row_count} row(s) reviewed for {_safe_text(title)}. "
          "Edit this summary to record key findings and decisions."
      )

    table_classes = "wide-table" if col_count >= 10 else ""

    return (
        f"<div class=\"report-section report-block avoid-break\">"
        f"<div class=\"section-controls no-print\"><button type=\"button\" class=\"mini-btn\" onclick=\"removeReportBlock(this)\">Remove Table</button></div>"
        f"<h4 class=\"editable-text\" contenteditable=\"true\">{title}</h4>"
        f"<div class=\"editable table-note table-note-top\" contenteditable=\"true\"><p>{_escape(informative_default)}</p></div>"
      f"<table class=\"{table_classes}\">{head_html}{body_html}</table>"
        f"<div class=\"editable table-note table-note-bottom\" contenteditable=\"true\"><p>{_escape(summary_default)}</p></div>"
        f"</div>"
    )


def _render_chart_blocks(payload: dict[str, Any]) -> str:
    raw_charts = payload.get("charts", []) if isinstance(payload.get("charts"), list) else []
    chart_items: list[dict[str, str]] = []

    for idx, chart in enumerate(raw_charts):
        if not isinstance(chart, dict):
            continue
        image_data_url = _safe_text(chart.get("image_data_url"), "")
        if not image_data_url.startswith("data:image/"):
            continue
        title = _safe_text(chart.get("title"), f"Chart {idx + 1}")
        chart_items.append({"title": title, "image": image_data_url})

    if not chart_items:
        fallback = _safe_text(payload.get("chart_image_data_url"), "")
        if fallback.startswith("data:image/"):
            chart_items.append({"title": "Primary Chart", "image": fallback})

    if not chart_items:
        return (
            "<div class=\"report-section avoid-break\">"
            "<div class=\"editable\" contenteditable=\"true\"><p>No chart snapshots were available for this draft. "
            "You can add commentary here or remove this section.</p></div>"
            "</div>"
        )

    blocks: list[str] = []
    for idx, item in enumerate(chart_items):
        blocks.append(
            "<figure class=\"report-section report-block chart-block avoid-break\">"
            "<div class=\"section-controls no-print\"><button type=\"button\" class=\"mini-btn\" onclick=\"removeReportBlock(this)\">Remove Chart</button></div>"
            f"<h4 class=\"chart-title editable-text\" contenteditable=\"true\">{_escape(item['title'], f'Chart {idx + 1}')}</h4>"
            f"<img class=\"chart-img\" src=\"{item['image']}\" alt=\"{_escape(item['title'], f'Chart {idx + 1}')}\" />"
            "<figcaption class=\"editable chart-caption editable-text\" contenteditable=\"true\">"
            "Describe what this chart shows, assumptions, and interpretation for stakeholders."
            "</figcaption>"
            "</figure>"
        )

    return "".join(blocks)


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
    summary_text = _safe_text(payload.get("auto_summary"), "[Insert executive summary details here...]")
    logo_data_url = _load_logo_data_url()

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

    queue_peak = _escape(results.get("queue_peak_m"))
    worst_vcr = _escape(results.get("worst_vcr"))
    los = _escape(results.get("los"))
    detour = _escape(results.get("detour_recommended"))

    notes_html = _render_notes(notes)
    tables = payload.get("tables", []) if isinstance(payload.get("tables"), list) else []

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
    table_sections = "".join(_render_data_table(t) for t in prioritized_tables)
    chart_sections = _render_chart_blocks(payload)
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

    /* KPIs Grid */
    .kpi-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin: 1.5rem 0; }}
    .kpi-box {{ border: 1px solid var(--border); border-left: 4px solid var(--accent); padding: 15px; background: var(--bg-light); }}
    .kpi-title {{ font-size: 0.85rem; text-transform: uppercase; color: var(--muted); letter-spacing: 0.5px; margin-bottom: 5px; }}
    .kpi-value {{ font-size: 1.4rem; font-weight: bold; color: var(--brand); }}

    /* Charts */
    .chart-block {{ width: 100%; max-width: 100%; margin: 0 0 16px; }}
    .chart-title {{ margin-bottom: 8px; }}
    .chart-img {{ width: auto; max-width: 100%; height: auto; border: 1px solid var(--border); display: block; margin: 10px auto; object-fit: contain; image-rendering: auto; }}
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
      body {{ background: #fff; }}
      .document-wrapper {{ box-shadow: none; margin: 0; padding: 0; max-width: 100%; }}
      .toolbar {{ display: none; }}
      .no-print {{ display: none !important; }}
      .editable {{ border: none; background: transparent; padding: 0; }}
      .toc-container {{ border: none; padding: 0; }}
      .toc-link {{ border-bottom: none; }}
    }}
  </style>
</head>
<body>
  <div class=\"toolbar\" style=\"max-width: 210mm; margin: 20px auto 0;\">
    <button class=\"btn\" onclick=\"window.print()\">🖨️ Print to PDF</button>
  </div>

  <main class=\"document-wrapper\">

    <div class=\"cover-page\">
      {f'<img class="cover-logo" src="{logo_data_url}" alt="Company Logo" />' if logo_data_url else ''}
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
      <p>{_escape(summary_text)}</p>
      <p><em>Click here to edit and provide high-level context regarding the site impact, network performance, and mitigation requirements.</em></p>
    </div>

    <h2 class=\"avoid-break\" contenteditable=\"true\">2. Critical Performance Outcomes</h2>
    <div class=\"kpi-grid avoid-break\">
      <div class=\"kpi-box\"><div class=\"kpi-title\" contenteditable=\"true\">Worst VCR</div><div class=\"kpi-value\" contenteditable=\"true\">{worst_vcr}</div></div>
      <div class=\"kpi-box\"><div class=\"kpi-title\" contenteditable=\"true\">Peak Queue Length</div><div class=\"kpi-value\" contenteditable=\"true\">{queue_peak}</div></div>
      <div class=\"kpi-box\"><div class=\"kpi-title\" contenteditable=\"true\">Level of Service (LOS)</div><div class=\"kpi-value\" contenteditable=\"true\">{los}</div></div>
      <div class=\"kpi-box\"><div class=\"kpi-title\" contenteditable=\"true\">Detour Recommended</div><div class=\"kpi-value\" contenteditable=\"true\">{detour}</div></div>
    </div>

    <h2 contenteditable=\"true\">3. Design & Traffic Inputs</h2>
    {_render_key_value_table('Analysis Parameters', inputs)}

    <div class=\"page-break\"></div>

    <h2 contenteditable=\"true\">4. Traffic Analysis & Results</h2>
    {_render_key_value_table('Summary of Computed Results', results)}

    {table_sections}

    <div class=\"page-break\"></div>

    <h2 contenteditable=\"true\">5. Engineering Observations & Notes</h2>
    <ul>{notes_html}</ul>

    <h2 contenteditable=\"true\">6. Charts</h2>
    <div id=\"chartSectionContent\">{chart_sections}</div>

    <h2 contenteditable=\"true\">7. Professional Commentary & Conclusion</h2>
    <div class=\"editable\" contenteditable=\"true\">
      <p>Enter your final engineering commentary, summary of impact, and mitigation recommendations here.</p>
    </div>

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
        'main p', 'main li', 'main span', 'main div.kpi-title', 'main div.kpi-value',
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
