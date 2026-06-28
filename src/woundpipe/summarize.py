"""Per-patient AI summary — a short, biller-facing overview of one claim.

Two generation paths, same output contract (a 2–3 sentence string):

* :func:`llm_summary` — real Claude (haiku) when an API key is configured. Reuses
  the one cached Anthropic client from :mod:`woundpipe.extract.llm_lane`.
* :func:`template_summary` — deterministic, data-driven text assembled from the
  patient's structured fields. Always available (no key, no network), so it is the
  floor when the LLM is unavailable.

:func:`backfill` persists the result on ``pcc_patient.ai_summary`` (migration 006)
so a summary is computed once and survives every re-publish.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from woundpipe.config import Settings

__all__ = ["template_summary", "llm_summary", "generate", "backfill"]

TEMPLATE_VERSION = "deterministic-v1"

# wound_type enum -> reader-friendly phrase
_WOUND_LABEL = {
    "pressure_ulcer": "pressure ulcer",
    "diabetic_foot_ulcer": "diabetic foot ulcer",
    "venous_leg_ulcer": "venous leg ulcer",
    "arterial_ulcer": "arterial ulcer",
    "surgical_wound": "surgical wound",
    "trauma_wound": "trauma wound",
    "other": "wound",
}
_PAYER_NAME = {
    "MCB": "Medicare Part B",
    "MCA": "Medicare Advantage",
    "MCD": "Medicaid",
    "HMO": "an HMO / commercial plan",
}


def _num(x: Any) -> str:
    """Trim trailing zeros: 2.90 -> '2.9', 3.0 -> '3'."""
    return f"{float(x):g}"


def _measure(length: Any, width: Any, depth: Any) -> str | None:
    if length is None and width is None and depth is None:
        return None
    parts = [(_num(v) if v is not None else "—") for v in (length, width, depth)]
    return " × ".join(parts) + " cm"


def template_summary(row: dict[str, Any]) -> str:
    """Deterministic 2–3 sentence summary from a patient's structured fields.

    ``row`` is a ``v_patient_eligibility`` mapping (first_name, last_name,
    wound_type, stage, location, length_cm, width_cm, depth_cm, drainage,
    payer_code, route, reason, n_sources, n_conflict, ...). Null-safe.
    """
    name = " ".join(x for x in (row.get("first_name"), row.get("last_name")) if x).strip()
    subject = name or "This patient"

    # --- wound clause -------------------------------------------------------
    wtype = row.get("wound_type")
    if wtype:
        label = _WOUND_LABEL.get(wtype, wtype.replace("_", " "))
        stage = row.get("stage")
        stage_part = f"stage {stage} " if stage and stage != "N/A" else ""
        loc = row.get("location")
        loc_part = f" on the {loc.lower()}" if loc else ""
        clause = f"{subject} has a {stage_part}{label}{loc_part}"
        meas = _measure(row.get("length_cm"), row.get("width_cm"), row.get("depth_cm"))
        if meas:
            clause += f", measuring {meas}"
        drainage = row.get("drainage")
        if drainage:
            clause += f", with {drainage} drainage"
        wound_sentence = clause + "."
    else:
        wound_sentence = f"{subject} has no extractable wound documented in the chart."

    # --- coverage / decision clause ----------------------------------------
    payer = _PAYER_NAME.get(row.get("payer_code") or "", row.get("payer_code") or "no payer on file")
    reason = (row.get("reason") or "").strip()
    reason_l = reason[0].lower() + reason[1:] if reason else "documentation is incomplete"
    route = row.get("route")
    if route == "auto_accept":
        decision = (
            f"Covered by {payer} with an active wound diagnosis and complete measurements "
            f"that agree across sources, so it is ready to bill."
        )
    elif route == "reject":
        decision = f"This claim is not billable — {reason_l}."
    else:  # flag_for_review
        if row.get("payer_code") == "MCB":
            decision = f"Covered by {payer}, but {reason_l}, so a biller should review it before submitting."
        else:
            decision = f"{reason_l.capitalize()}, so a biller should review it before submitting."

    return f"{wound_sentence} {decision}"


# --------------------------------------------------------------------------- LLM
_LLM_SYSTEM = (
    "You write a short, plain-language summary of one wound-care claim for a "
    "non-technical medical biller who decides whether to bill it. 2–3 sentences. "
    "Say what the wound is, the insurance, and why it is ready to bill / needs "
    "review / is not billable. No jargon, no bullet points, no preamble."
)


def llm_summary(row: dict[str, Any], settings: Settings) -> str | None:
    """Real Claude (haiku) summary; ``None`` on no-key / no-SDK / any error."""
    if not getattr(settings, "anthropic_api_key", None):
        return None  # no key → deterministic floor (avoid the SDK raising)
    from woundpipe.extract import llm_lane

    try:
        client = llm_lane._get_client(settings)
    except Exception:  # noqa: BLE001 — client construction must never break the run
        return None
    if client is None:
        return None
    facts = {
        k: row.get(k)
        for k in (
            "first_name", "last_name", "payer_code", "wound_type", "stage", "location",
            "length_cm", "width_cm", "depth_cm", "drainage", "route", "reason",
            "n_sources", "n_conflict",
        )
    }
    try:
        resp = client.messages.create(
            model=settings.model_bulk,
            max_tokens=220,
            temperature=0.2,
            system=_LLM_SYSTEM,
            messages=[{"role": "user", "content": f"Claim facts (JSON): {facts}\nWrite the summary."}],
        )
        text = "".join(
            getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text"
        ).strip()
        return text or None
    except Exception:  # noqa: BLE001 — never let summarization break the pipeline
        return None


def generate(row: dict[str, Any], settings: Settings, *, use_llm: bool) -> tuple[str, str]:
    """Return ``(summary_text, model_label)`` — LLM when available, else template."""
    if use_llm:
        text = llm_summary(row, settings)
        if text:
            return text, settings.model_bulk
    return template_summary(row), TEMPLATE_VERSION


def backfill(
    con: sqlite3.Connection, settings: Settings, *, force: bool = False, use_llm: bool | None = None
) -> dict[str, int]:
    """Fill ``pcc_patient.ai_summary`` for patients missing one (all, if ``force``).

    Reads facts from ``v_patient_eligibility``; writes summary + provenance back to
    ``pcc_patient``. Idempotent without ``force``. Returns counts.
    """
    from woundpipe.ingest.checkpoint import now_iso

    # ai_summary lands in migration 006; degrade gracefully on an un-migrated DB.
    if not con.execute(
        "SELECT 1 FROM pragma_table_info('pcc_patient') WHERE name = 'ai_summary'"
    ).fetchone():
        return {"generated": 0, "llm": 0, "template": 0, "skipped": "no ai_summary column (run migrate)"}

    use_llm = settings.use_llm if use_llm is None else use_llm
    where = "" if force else "WHERE p.ai_summary IS NULL"
    rows = con.execute(
        f"""
        SELECT e.*
        FROM v_patient_eligibility e
        JOIN pcc_patient p ON p.patient_id = e.patient_id
        {where}
        ORDER BY e.patient_id
        """
    ).fetchall()

    now = now_iso()
    n_llm = n_template = 0
    for r in rows:
        row = dict(r)
        text, model = generate(row, settings, use_llm=bool(use_llm))
        if model == TEMPLATE_VERSION:
            n_template += 1
        else:
            n_llm += 1
        con.execute(
            "UPDATE pcc_patient SET ai_summary = ?, ai_summary_model = ?, ai_summary_at = ? "
            "WHERE patient_id = ?",
            (text, model, now, row["patient_id"]),
        )
    con.commit()
    return {"generated": len(rows), "llm": n_llm, "template": n_template}
