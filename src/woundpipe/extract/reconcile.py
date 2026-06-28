"""Lane 3 — reconciler + confidence (SPEC spec-routing §2, §4).

Confidence comes from cross-source AGREEMENT, never from LLM self-report.
Produces the primary wound's overall_conf and per-field confidences.
"""
from __future__ import annotations

REQUIRED_BASE = ("wound_type", "location", "length_cm", "width_cm", "drainage")
DEPTH_REQUIRED_TYPES = {"pressure_ulcer", "diabetic_foot_ulcer", "venous_leg_ulcer", "arterial_ulcer"}

_METHOD_PRIOR = {"regex": 0.85, "soap": 1.0, "regex_spn": 1.0, "json": 1.0, "llm": 0.55, "manual": 1.0}
CORROB_MULT = {"agree": 1.00, "single_source": 0.90, "conflict": 0.75}


def _norm(v):
    return str(v).strip().lower() if v is not None else None


def _agreement_for(field, sources):
    """Fraction of asserting sources that agree on `field` (0.5 if only one)."""
    vals = [s.get(field) for s in sources if s.get(field) is not None]
    if not vals:
        return 0.0
    if len(vals) == 1:
        return 0.5
    first = _norm(vals[0])
    agree = sum(1 for v in vals if _norm(v) == first)
    return agree / len(vals)


def field_confidence(field, primary, sources, method="regex"):
    agreement = _agreement_for(field, sources)
    method_prior = _METHOD_PRIOR.get(method, 0.6)
    span_verified = 1.0 if primary.get(field) is not None else 0.0
    # local completeness: measurement companions / stage usability
    if field in ("length_cm", "width_cm", "depth_cm"):
        complete = 1.0 if (primary.get("length_cm") and primary.get("width_cm")) else 0.0
    elif field == "stage":
        complete = 1.0 if primary.get("stage_status") == "staged" else 0.0
    else:
        complete = 1.0 if primary.get(field) is not None else 0.0
    penalty = 0.05 if primary.get("quality_flag") else 0.0
    base = 0.40 * agreement + 0.20 * method_prior + 0.20 * span_verified + 0.20 * complete
    val = max(0.0, min(1.0, base - min(0.15, penalty)))
    # hard gate: unverified measurement caps low
    if field in ("length_cm", "width_cm", "depth_cm") and span_verified == 0.0:
        val = min(val, 0.30)
    return round(val, 3)


def corroboration(primary, sources):
    """agreement category + agree_sources over {note, assessment, dx} on type+location."""
    if not sources:
        return "single_source", 0, 0
    n_conflict = 0
    n_agree = 0
    for s in sources:
        if s is primary:
            continue
        asserts = s.get("wound_type") is not None or s.get("location") is not None
        if not asserts:
            continue
        agree = (_norm(s.get("wound_type")) == _norm(primary.get("wound_type")) and
                 _norm(s.get("location")) == _norm(primary.get("location")))
        if agree:
            n_agree += 1
        else:
            n_conflict += 1
    if n_conflict > 0:
        return "conflict", n_agree, n_conflict
    if n_agree >= 1:
        return "agree", n_agree, 0
    return "single_source", n_agree, 0


def reconcile(primary, sources, method="regex"):
    """Compute per-field confidences + overall_conf on the primary wound dict in place."""
    wtype = primary.get("wound_type")
    required = list(REQUIRED_BASE)
    if wtype in DEPTH_REQUIRED_TYPES:
        required.append("depth_cm")
    if wtype == "pressure_ulcer":
        required.append("stage")

    confs = {f: field_confidence(f, primary, sources, method) for f in required}
    primary["field_confidence"] = confs

    present = [confs[f] for f in required if primary.get(f if f != "stage" else "stage") is not None]
    field_mean = sum(present) / len(present) if present else 0.0

    category, n_agree, n_conflict = corroboration(primary, sources)
    mult = CORROB_MULT[category]
    primary["agreement"] = category
    primary["n_agree"] = n_agree
    primary["n_conflict"] = n_conflict
    primary["overall_conf"] = round(max(0.0, min(1.0, field_mean * mult)), 3)

    # completeness for routing (fraction of required present)
    have = sum(1 for f in required if primary.get(f if f != "stage" else "stage") is not None)
    primary["completeness"] = round(have / len(required), 3) if required else 0.0
    primary["required_fields"] = required
    return primary
