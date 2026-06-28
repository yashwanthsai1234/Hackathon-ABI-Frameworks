"""S6 — publish. Build the static export.json the frontend reads (no live backend)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from woundpipe import repository


def _duration_s(m: dict) -> float:
    try:
        t0 = datetime.fromisoformat(m["started_at"])
        t1 = datetime.fromisoformat(m["finished_at"])
        return round((t1 - t0).total_seconds(), 1)
    except Exception:
        return 0.0


def _frontend_manifest(raw: dict | None, funnel: dict) -> dict:
    """Reshape the flat RunManifest into the shape the dashboard consumes
    (total_patients · duration_s · rate_limit_hits · stages[] · routes)."""
    m = raw or {}
    total = funnel.get("total", m.get("n_patients", 0))
    fetched = m.get("n_patients", total)
    extracted = m.get("n_extracted", funnel.get("active_wound", total))
    routed = total
    retried = m.get("n_retries", m.get("n_429", 0))
    routes = {
        "auto_accept": funnel.get("auto_accept", 0),
        "flag_for_review": funnel.get("flag_for_review", 0),
        "reject": funnel.get("reject", 0),
    }
    return {
        "run_id": m.get("run_id", "local"),
        "generated_at": m.get("finished_at") or m.get("started_at", ""),
        "total_patients": total,
        "duration_s": _duration_s(m),
        "rate_limit_hits": m.get("n_429", 0),
        "stages": [
            {"id": "S0", "label": "Ingest (PCC API)", "in": total, "out": fetched,
             "retried": retried, "note": f"{m.get('n_429', 0)} × 429 retried w/ backoff"},
            {"id": "S1", "label": "Resolve identity", "in": fetched, "out": fetched,
             "retried": 0, "note": "patient_id ↔ id gate"},
            {"id": "S2", "label": "Normalize", "in": fetched, "out": fetched,
             "retried": 0, "note": "active MCB + wound dx"},
            {"id": "S3", "label": "Sniff format", "in": fetched, "out": extracted,
             "retried": 0, "note": "4 note formats from text"},
            {"id": "S4", "label": "Extract", "in": extracted, "out": extracted,
             "retried": 0, "note": "regex L1 + Claude L2 + reconcile"},
            {"id": "S5", "label": "Route", "in": extracted, "out": routed,
             "retried": 0, "note": "SQL eligibility view"},
            {"id": "S6", "label": "Publish", "in": routed, "out": routed,
             "retried": 0, "note": "export.json"},
        ],
        "routes": routes,
        # keep the raw counters too (harmless extras)
        "n_calls": m.get("n_calls", 0),
        "n_429": m.get("n_429", 0),
        "n_retries": m.get("n_retries", 0),
    }


_NODE_TYPE = {"diagnosis": "dx", "note": "note", "assessment": "assessment", "wound": "wound"}


def _adapt_patient(p: dict) -> dict:
    """Map the backend export shape to the frontend's types.ts contract."""
    g = p.get("evidence_graph") or {"nodes": [], "edges": []}
    nodes = [{"id": n["id"], "type": _NODE_TYPE.get(n.get("kind"), n.get("kind", "wound")),
              "label": n.get("label", "")} for n in g.get("nodes", [])]
    edges = [{"id": f'{e["source"]}__{e["target"]}', "source": e["source"], "target": e["target"],
              "relation": e.get("relation", "agree"), "field": e.get("field")} for e in g.get("edges", [])]
    checks = [{"label": c.get("label", ""), "code": c.get("code"),
               "status": "pass" if c.get("ok") else "fail", "detail": c.get("detail", "")}
              for c in (p.get("eligibility_checks") or [])]
    w = p.get("wound") or {}
    return {
        "patient_id": p.get("patient_id"),
        "id": p.get("internal_id"),
        "name": p.get("name", ""),
        "payer": p.get("payer_code"),
        "wound": {
            "type": w.get("wound_type"), "stage": w.get("stage"), "location": w.get("location"),
            "L": w.get("length_cm"), "W": w.get("width_cm"), "D": w.get("depth_cm"),
            "drainage": w.get("drainage"), "format": w.get("format", ""),
        },
        "route": p.get("route"),
        "confidence": p.get("confidence") or 0,
        "field_confidence": p.get("field_confidence") or {},
        "reason": p.get("reason", ""),
        "data_complete": p.get("data_complete", True),
        "failed_fetches": p.get("failed_fetches", 0),
        "note_text": p.get("note_text") or "",
        "highlights": p.get("highlights") or [],
        "eligibility_checks": checks,
        "evidence_graph": {"nodes": nodes, "edges": edges},
    }


def publish(con: sqlite3.Connection, out_path: str, frontend_public: str | None = None) -> dict:
    data = repository.export_json(con)
    data["manifest"] = _frontend_manifest(data.get("manifest"), data.get("funnel", {}))
    data["patients"] = [_adapt_patient(p) for p in data.get("patients", [])]
    payload = json.dumps(data, indent=2, default=str)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(payload)
    if frontend_public and Path(frontend_public).parent.exists():
        Path(frontend_public).write_text(json.dumps(data, default=str))
    return {"export_path": out_path, "patients": len(data.get("patients", []))}
