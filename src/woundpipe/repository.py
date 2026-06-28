"""Typed, parameterized read layer + the frontend JSON export.

Every function takes an open :class:`sqlite3.Connection` (from
``woundpipe.db.engine.connect``) and returns plain dicts/lists — these dict
shapes ARE the frontend contract atoms (SPEC §C.3 / architecture §G). All SQL is
parameterized; no string interpolation of user input.

The heavy assembler, :func:`export_json`, is deliberately N+1-free: it issues a
fixed, small number of bulk queries and groups in Python, so cost is independent
of patient count.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Any

__all__ = [
    "worklist",
    "patient_detail",
    "corroboration",
    "run_funnel",
    "note_search",
    "export_json",
]


# --------------------------------------------------------------------- helpers
def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


def _one(cur: sqlite3.Cursor) -> dict[str, Any] | None:
    r = cur.fetchone()
    return dict(r) if r is not None else None


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


# Map an extraction_method to a best-effort source note format label (the
# pipeline does not persist a separate format column; method is the closest
# proxy for the patient-detail "format" chip).
_METHOD_TO_FORMAT = {
    "regex_spn": "labeled_spn",
    "regex_envive": "envive",
    "regex_prose": "prose_shorthand",
    "soap": "soap_idt",
    "json": "assessment_flat_json",
    "llm": "unknown",
    "manual": "unknown",
}


# --------------------------------------------------------------------- queries
def worklist(con: sqlite3.Connection, route: str) -> list[dict[str, Any]]:
    """Patients on a given route, most-confident first (review/worklist table)."""
    cur = con.execute(
        """
        SELECT patient_id, internal_id, facility_id, first_name, last_name,
               payer_code, wound_type, stage, location,
               length_cm, width_cm, depth_cm, drainage,
               confidence, has_active_mcb, has_active_wound, has_active_wound_dx,
               n_sources, n_agree, all_agree, n_conflict, route, reason
        FROM v_patient_eligibility
        WHERE route = ?
        ORDER BY confidence DESC NULLS LAST, patient_id
        """,
        (route,),
    )
    return _rows(cur)


def patient_detail(con: sqlite3.Connection, patient_id: str) -> dict[str, Any]:
    """Full patient dossier: eligibility row + extractions + notes + corroboration."""
    elig = _one(
        con.execute(
            "SELECT * FROM v_patient_eligibility WHERE patient_id = ?", (patient_id,)
        )
    )
    extractions = _rows(
        con.execute(
            """
            SELECT * FROM wound_extraction
            WHERE patient_id = ?
            ORDER BY is_primary DESC, overall_conf DESC NULLS LAST, id
            """,
            (patient_id,),
        )
    )
    notes = _rows(
        con.execute(
            """
            SELECT n.id, n.note_type, n.effective_date, n.note_text, n.note_label
            FROM progress_note n
            JOIN pcc_patient p ON p.id = n.patient_id
            WHERE p.patient_id = ?
            ORDER BY n.effective_date DESC NULLS LAST, n.id
            """,
            (patient_id,),
        )
    )
    return {
        "eligibility": elig,
        "extractions": extractions,
        "notes": notes,
        "corroboration": corroboration(con, patient_id),
    }


def corroboration(con: sqlite3.Connection, patient_id: str) -> dict[str, Any]:
    """Corroboration edges + scalar summary for one patient (§4b)."""
    edges = _rows(
        con.execute(
            """
            SELECT patient_id, evidence_node, source_note_id, source_assessment_id,
                   extraction_id, type_agrees, location_agrees, stage_agrees,
                   corroborates, overall_conf, evidence_quote
            FROM v_wound_corroboration
            WHERE patient_id = ?
            ORDER BY extraction_id
            """,
            (patient_id,),
        )
    )
    summary = _one(
        con.execute(
            "SELECT * FROM v_corroboration_summary WHERE patient_id = ?", (patient_id,)
        )
    )
    return {"edges": edges, "summary": summary}


def run_funnel(con: sqlite3.Connection) -> dict[str, Any]:
    """Aggregate funnel: by_route, by_facility, and the headline totals."""
    by_route = {
        r["route"]: r["n"]
        for r in _rows(
            con.execute(
                "SELECT route, COUNT(*) AS n FROM v_patient_eligibility GROUP BY route"
            )
        )
    }
    by_facility = _rows(
        con.execute(
            """
            SELECT facility_id, route, COUNT(*) AS n
            FROM v_patient_eligibility
            GROUP BY facility_id, route
            ORDER BY facility_id, route
            """
        )
    )
    totals = _one(
        con.execute(
            """
            SELECT
              COUNT(*)                                                       AS total,
              SUM(has_active_mcb)                                            AS mcb_active,
              SUM(has_active_wound)                                          AS active_wound,
              SUM(length_cm IS NOT NULL AND width_cm IS NOT NULL
                  AND depth_cm IS NOT NULL)                                  AS has_measurements
            FROM v_patient_eligibility
            """
        )
    ) or {}
    return {
        "by_route": by_route,
        "by_facility": by_facility,
        "total": totals.get("total", 0),
        "mcb_active": totals.get("mcb_active", 0) or 0,
        "active_wound": totals.get("active_wound", 0) or 0,
        "has_measurements": totals.get("has_measurements", 0) or 0,
        "auto_accept": by_route.get("auto_accept", 0),
        "flag_for_review": by_route.get("flag_for_review", 0),
        "reject": by_route.get("reject", 0),
    }


def note_search(con: sqlite3.Connection, q: str) -> list[dict[str, Any]]:
    """FTS5 full-text search over note_text, ranked, with a highlighted snippet."""
    cur = con.execute(
        """
        SELECT n.id AS note_id, n.patient_id AS internal_id, n.note_type,
               n.effective_date,
               snippet(progress_note_fts, 0, '[', ']', ' … ', 12) AS snippet,
               bm25(progress_note_fts) AS rank
        FROM progress_note_fts
        JOIN progress_note n ON n.id = progress_note_fts.rowid
        WHERE progress_note_fts MATCH ?
        ORDER BY rank
        """,
        (q,),
    )
    return _rows(cur)


# ------------------------------------------------------------- JSON export (S6)
def _build_funnel(patients: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(patients)
    mcb = sum(1 for p in patients if p["has_active_mcb"])
    no_mcb = total - mcb
    wound_in_mcb = sum(1 for p in patients if p["has_active_mcb"] and p["has_active_wound"])
    no_wound_in_mcb = mcb - wound_in_mcb
    has_meas = sum(
        1
        for p in patients
        if p["length_cm"] is not None
        and p["width_cm"] is not None
        and p["depth_cm"] is not None
    )
    auto = sum(1 for p in patients if p["route"] == "auto_accept")
    flag = sum(1 for p in patients if p["route"] == "flag_for_review")
    reject = sum(1 for p in patients if p["route"] == "reject")
    # Causal payer -> eligibility -> route Sankey (flow-conserving, clamped >=0).
    # rejects among the has-wound band = wound_in_mcb minus those that cleared.
    reject_in_wound = max(0, wound_in_mcb - auto - flag)
    sankey = [
        {"source": "Cohort", "target": "MCB Active", "value": mcb},
        {"source": "Cohort", "target": "No MCB → Reject", "value": no_mcb},
        {"source": "MCB Active", "target": "Has Wound", "value": wound_in_mcb},
        {"source": "MCB Active", "target": "No Wound → Reject", "value": no_wound_in_mcb},
        {"source": "Has Wound", "target": "Auto Accept", "value": auto},
        {"source": "Has Wound", "target": "Flag for Review", "value": flag},
        {"source": "Has Wound", "target": "Reject (incomplete)", "value": reject_in_wound},
    ]
    sankey = [link for link in sankey if link["value"] > 0]
    return {
        "total": total,
        "mcb_active": mcb,
        "active_wound": sum(1 for p in patients if p["has_active_wound"]),
        "has_measurements": has_meas,
        "auto_accept": auto,
        "flag_for_review": flag,
        "reject": reject,
        "sankey": sankey,
    }


def _evidence_graph(
    elig: dict[str, Any], edges: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build the React-Flow evidence graph: wound node + one node per source."""
    wtype = elig.get("wound_type") or "wound"
    loc = elig.get("location") or ""
    wound_label = f"{wtype}" + (f" — {loc}" if loc else "")
    nodes = [{"id": "wound:primary", "kind": "wound", "label": wound_label or "primary wound"}]
    graph_edges: list[dict[str, Any]] = []
    seen: set[str] = {"wound:primary"}
    for e in edges:
        kind = e["evidence_node"]  # 'note' | 'assessment' | 'diagnosis'
        if e.get("source_note_id") is not None:
            nid, label = f"note:{e['source_note_id']}", f"note #{e['source_note_id']}"
        elif e.get("source_assessment_id") is not None:
            nid, label = (
                f"assess:{e['source_assessment_id']}",
                f"assessment #{e['source_assessment_id']}",
            )
        else:
            nid, label = f"dx:{e['extraction_id']}", "diagnosis"
        if nid not in seen:
            nodes.append({"id": nid, "kind": kind, "label": label})
            seen.add(nid)
        agree = bool(e.get("corroborates"))
        graph_edges.append(
            {
                "source": nid,
                "target": "wound:primary",
                "relation": "agree" if agree else "conflict",
                "color": "green" if agree else "red",
            }
        )
    return {
        "nodes": nodes,
        "edges": graph_edges,
        "agreeing_sources": int(elig.get("n_agree") or 0),
    }


def _eligibility_checks(elig: dict[str, Any]) -> list[dict[str, Any]]:
    meas_ok = (
        elig["length_cm"] is not None
        and elig["width_cm"] is not None
        and elig["depth_cm"] is not None
    )
    agree_ok = bool(elig.get("all_agree")) and (elig.get("n_sources") or 0) >= 2
    failed_fetches = elig.get("failed_fetches") or 0
    return [
        {
            "label": "Active Medicare Part B",
            "ok": bool(elig["has_active_mcb"]),
            "detail": elig.get("payer_code"),
        },
        {
            "label": "Source data complete",
            "ok": failed_fetches == 0,
            "detail": None if failed_fetches == 0 else f"{failed_fetches} fetch(es) failed",
        },
        {"label": "Active wound", "ok": bool(elig["has_active_wound"]), "detail": None},
        {
            "label": "Corroborating ICD-10 wound dx",
            "ok": bool(elig.get("has_active_wound_dx")),
            "detail": None,
        },
        {
            "label": "Measurements L×W×D",
            "ok": bool(meas_ok),
            "detail": None,
        },
        {
            "label": "Drainage documented",
            "ok": elig["drainage"] is not None,
            "detail": elig.get("drainage"),
        },
        {
            "label": "Cross-source agreement (≥2 sources)",
            "ok": agree_ok,
            "detail": f"{elig.get('n_agree') or 0}/{elig.get('n_sources') or 0} agree",
        },
    ]


def export_json(con: sqlite3.Connection) -> dict[str, Any]:
    """Assemble the full frontend contract (SPEC §C.3 / architecture §G).

    Returns a JSON-serializable dict:
    ``{generated_at, run_id, manifest, funnel{...,sankey}, patients[]}``.
    N+1-free: a fixed set of bulk queries, grouped in Python.
    """
    patients = _rows(
        con.execute(
            """
            SELECT patient_id, internal_id, facility_id, first_name, last_name,
                   payer_code, wound_type, stage, location,
                   length_cm, width_cm, depth_cm, drainage, confidence,
                   has_active_mcb, has_active_wound, has_active_wound_dx,
                   n_sources, n_agree, all_agree, n_conflict,
                   failed_fetches, data_complete, route, reason
            FROM v_patient_eligibility
            ORDER BY facility_id, patient_id
            """
        )
    )

    # --- bulk lookups (grouped in Python; no per-patient round trips) --------
    # primary extraction per patient (max overall_conf among is_primary rows).
    prim_by_patient: dict[str, dict[str, Any]] = {}
    for row in _rows(
        con.execute(
            """
            SELECT id, patient_id, source_note_id, source_assessment_id,
                   extraction_method, wound_type_conf, stage_conf, location_conf,
                   measure_conf, drainage_conf, overall_conf,
                   evidence_span_start, evidence_span_end, evidence_quote
            FROM wound_extraction
            WHERE is_primary = 1
            """
        )
    ):
        cur = prim_by_patient.get(row["patient_id"])
        if cur is None or (row["overall_conf"] or -1) > (cur["overall_conf"] or -1):
            prim_by_patient[row["patient_id"]] = row

    # corroboration edges grouped by patient.
    edges_by_patient: dict[str, list[dict[str, Any]]] = {}
    for e in _rows(
        con.execute(
            """
            SELECT patient_id, evidence_node, source_note_id, source_assessment_id,
                   extraction_id, corroborates
            FROM v_wound_corroboration
            """
        )
    ):
        edges_by_patient.setdefault(e["patient_id"], []).append(e)

    # per-field evidence grouped by extraction_id (migration 002; tolerate absence).
    fields_by_extraction: dict[int, list[dict[str, Any]]] = {}
    has_wfe = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='wound_field_evidence'"
    ).fetchone()
    if has_wfe:
        for f in _rows(
            con.execute(
                """
                SELECT extraction_id, field, char_start, char_end, quote,
                       method, confidence
                FROM wound_field_evidence
                """
            )
        ):
            fields_by_extraction.setdefault(f["extraction_id"], []).append(f)

    # note_text by note id.
    note_text_by_id = {
        r["id"]: r["note_text"]
        for r in _rows(
            con.execute("SELECT id, note_text FROM progress_note WHERE note_text IS NOT NULL")
        )
    }

    # --- assemble patients[] -------------------------------------------------
    out_patients: list[dict[str, Any]] = []
    for p in patients:
        prim = prim_by_patient.get(p["patient_id"])
        edges = edges_by_patient.get(p["patient_id"], [])

        note_text = ""
        highlights: list[dict[str, Any]] = []
        method = prim["extraction_method"] if prim else None
        if prim is not None and prim.get("source_note_id") is not None:
            note_text = note_text_by_id.get(prim["source_note_id"], "") or ""

        if prim is not None:
            field_rows = fields_by_extraction.get(prim["id"], [])
            for fr in field_rows:
                highlights.append(
                    {
                        "field": fr["field"],
                        "start": fr["char_start"],
                        "end": fr["char_end"],
                        "value": fr["quote"],
                    }
                )
            # Fall back to the summary span if no per-field evidence is present.
            if not highlights and prim.get("evidence_quote") is not None:
                highlights.append(
                    {
                        "field": "wound",
                        "start": prim.get("evidence_span_start"),
                        "end": prim.get("evidence_span_end"),
                        "value": prim.get("evidence_quote"),
                    }
                )

        field_confidence = {
            "wound_type": prim["wound_type_conf"] if prim else None,
            "stage": prim["stage_conf"] if prim else None,
            "location": prim["location_conf"] if prim else None,
            "length": prim["measure_conf"] if prim else None,
            "width": prim["measure_conf"] if prim else None,
            "depth": prim["measure_conf"] if prim else None,
            "drainage": prim["drainage_conf"] if prim else None,
        }

        out_patients.append(
            {
                "patient_id": p["patient_id"],
                "internal_id": p["internal_id"],
                "facility_id": p["facility_id"],
                "name": " ".join(
                    x for x in (p.get("first_name"), p.get("last_name")) if x
                )
                or None,
                "payer_code": p.get("payer_code"),
                "has_active_mcb": bool(p["has_active_mcb"]),
                "has_active_wound": bool(p["has_active_wound"]),
                "data_complete": bool(p["data_complete"]),
                "failed_fetches": p["failed_fetches"],
                "wound": {
                    "wound_type": p["wound_type"],
                    "stage": p["stage"],
                    "location": p["location"],
                    "length_cm": p["length_cm"],
                    "width_cm": p["width_cm"],
                    "depth_cm": p["depth_cm"],
                    "drainage": p["drainage"],
                    "format": _METHOD_TO_FORMAT.get(method) if method else None,
                },
                "route": p["route"],
                "reason": p["reason"],
                "confidence": p["confidence"],
                "field_confidence": field_confidence,
                "note_text": note_text,
                "highlights": highlights,
                "eligibility_checks": _eligibility_checks(p),
                "evidence_graph": _evidence_graph(p, edges),
            }
        )

    # --- manifest (latest run) ----------------------------------------------
    manifest = _one(
        con.execute("SELECT * FROM run_manifest ORDER BY started_at DESC NULLS LAST LIMIT 1")
    )
    run_id = manifest["run_id"] if manifest else None

    return {
        "generated_at": _now(),
        "run_id": run_id,
        "manifest": manifest,
        "funnel": _build_funnel(patients),
        "patients": out_patients,
    }
