"""On-demand summary API — the only live backend in an otherwise static app.

The dashboard calls ``GET /api/summary`` when a biller opens a wound's row; the
summary is generated lazily (Claude when keyed, deterministic floor otherwise) and
cached in ``wound_summary`` so a second open is instant. Run with ``woundpipe serve``.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from woundpipe import summarize
from woundpipe.config import load_settings
from woundpipe.db.engine import connect
from woundpipe.ingest.checkpoint import now_iso

app = FastAPI(title="woundpipe summary API")
app.add_middleware(  # dev convenience; in prod the frontend is same-origin via proxy
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

_settings = load_settings()


def configure(db_path: str) -> None:
    """Point the API at a specific DB (called by `woundpipe serve`)."""
    _settings.db_path = db_path


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "db": _settings.db_path}


@app.get("/api/summary")
def summary(patient_id: str, wound_key: str) -> dict:
    """Return a wound's AI summary, generating + caching it on first request."""
    con = connect(_settings.db_path)
    try:
        cached = con.execute(
            "SELECT ai_summary FROM wound_summary WHERE patient_id = ? AND wound_key = ?",
            (patient_id, wound_key),
        ).fetchone()
        if cached and cached[0]:
            return {"ai_summary": cached[0], "cached": True}

        facts = con.execute(
            "SELECT * FROM v_wound_eligibility WHERE patient_id = ? AND wound_key = ?",
            (patient_id, wound_key),
        ).fetchone()
        if facts is None:
            raise HTTPException(status_code=404, detail="wound not found")

        text, model = summarize.generate(dict(facts), _settings, use_llm=_settings.use_llm)
        con.execute(
            "INSERT INTO wound_summary (patient_id, wound_key, ai_summary, ai_summary_model, ai_summary_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT(patient_id, wound_key) DO UPDATE SET "
            "ai_summary = excluded.ai_summary, ai_summary_model = excluded.ai_summary_model, "
            "ai_summary_at = excluded.ai_summary_at",
            (patient_id, wound_key, text, model, now_iso()),
        )
        con.commit()
        return {"ai_summary": text, "cached": False}
    finally:
        con.close()
