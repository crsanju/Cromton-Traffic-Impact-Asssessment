from __future__ import annotations

import json
import uuid
from datetime import datetime
from html import escape
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

    title = escape(str(draft.get("title", "TIA Report")))
    payload = draft.get("payload", {})
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    notes = payload.get("notes", []) if isinstance(payload, dict) else []
    notes_html = "".join(f"<li>{escape(str(n))}</li>" for n in notes) or "<li>No notes available.</li>"
    raw_json = escape(json.dumps(payload, indent=2, ensure_ascii=True))

    queue_peak = summary.get("queue_peak_m", "-")
    worst_vcr = summary.get("worst_vcr", "-")
    detour = summary.get("detour_recommended", "-")

    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>{title}</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 0; background: #f5f7fa; color: #1f2937; }}
    .wrap {{ max-width: 1100px; margin: 0 auto; padding: 24px; }}
    .card {{ background: #fff; border: 1px solid #dbe2ea; border-radius: 10px; padding: 16px; margin-bottom: 14px; }}
    h1, h2 {{ margin: 0 0 10px; color: #0f2f32; }}
    .meta {{ color: #475569; font-size: 0.9rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }}
    .pill {{ border: 1px solid #dbe2ea; border-radius: 8px; background: #f8fafc; padding: 10px; }}
    pre {{ white-space: pre-wrap; word-break: break-word; background: #0f172a; color: #e2e8f0; padding: 12px; border-radius: 8px; overflow: auto; }}
    .editable {{ min-height: 110px; border: 1px solid #dbe2ea; border-radius: 8px; padding: 10px; background: #fff; }}
  </style>
</head>
<body>
  <main class=\"wrap\">
    <section class=\"card\">
      <h1 contenteditable=\"true\">{title}</h1>
      <p class=\"meta\">Draft ID: {escape(draft_id)} | Created: {escape(str(draft.get('created_at', '-')))}</p>
    </section>

    <section class=\"card\">
      <h2>Quick Summary</h2>
      <div class=\"grid\">
        <div class=\"pill\"><strong>Worst VCR</strong><br>{escape(str(worst_vcr))}</div>
        <div class=\"pill\"><strong>Peak Queue (m)</strong><br>{escape(str(queue_peak))}</div>
        <div class=\"pill\"><strong>Detour Recommended</strong><br>{escape(str(detour))}</div>
      </div>
    </section>

    <section class=\"card\">
      <h2>Editable Commentary</h2>
      <div class=\"editable\" contenteditable=\"true\">Enter your final engineering commentary here.</div>
    </section>

    <section class=\"card\">
      <h2>Notes</h2>
      <ul>{notes_html}</ul>
    </section>

    <section class=\"card\">
      <h2>Raw Payload (JSON)</h2>
      <pre>{raw_json}</pre>
    </section>
  </main>
</body>
</html>
"""
