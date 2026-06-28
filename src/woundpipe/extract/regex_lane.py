"""Lane 1 — deterministic regex extraction (OWNS measurements).

SPEC spec-extraction §2.2/§2.5. Every pattern returns the literal matched
substring (for the verbatim-span / highlight) or None. Numbers are never invented.
Validated against the real archetypes (0 misses).
"""
from __future__ import annotations

import re

NUM = r"\d+(?:\.\d+)?"

DIM = re.compile(
    rf"(?P<l>{NUM})\s*(?P<lu>cm|mm)?\s*[x×]\s*"
    rf"(?P<w>{NUM})\s*(?P<wu>cm|mm)?"
    rf"(?:\s*[x×]\s*(?P<d>{NUM})\s*(?P<du>cm|mm)?)?",
    re.I,
)
DEPTH = re.compile(
    rf"(?:depth\s*(?P<d1>{NUM})\s*(?P<u1>cm|mm)?)|(?:(?P<d2>{NUM})\s*(?P<u2>cm|mm)?\s*deep)",
    re.I,
)
STAGE = re.compile(
    r"Stage:\s*(?P<stage>Stage\s*[1-4IV]+|N/?A|Unstageable|Deep Tissue(?:\s*Injury)?)",
    re.I,
)
DRAIN = re.compile(
    r"\b(min(?:imal)?|scant|slight|mod(?:erate)?|copious|heavy|light|none|no\s+drainage)\b",
    re.I,
)
DRAIN_MAP = {
    "min": "light", "minimal": "light", "scant": "light", "slight": "light", "light": "light",
    "mod": "moderate", "moderate": "moderate",
    "copious": "heavy", "heavy": "heavy",
    "none": "none", "no drainage": "none",
}
_LAT = r"(?:right|left|bilateral|\bR\b|\bL\b)"
_SITE = (r"(?:hip|buttock|sacrum|coccyx|heel|plantar|foot|ankle|trochanter|"
         r"ischium|toe|leg|knee|elbow|back|trunk|shoulder)")
LOC = re.compile(rf"(?P<lat>{_LAT})\s+(?P<site>{_SITE})", re.I)
_TYPE_HINTS = [
    (re.compile(r"pressure ulcer", re.I), "pressure_ulcer"),
    (re.compile(r"diabetic", re.I), "diabetic_foot_ulcer"),
    (re.compile(r"venous", re.I), "venous_leg_ulcer"),
    (re.compile(r"arterial", re.I), "arterial_ulcer"),
    (re.compile(r"surgical", re.I), "surgical_wound"),
]


def _norm_unit(val: str, unit: str | None) -> float:
    v = float(val)
    return v / 10.0 if (unit and unit.lower() == "mm") else v


def _norm_lat(s: str) -> str:
    return {"r": "Right", "l": "Left"}.get(s.lower(), s.title())


def collapse_dups(text: str) -> str:
    return re.sub(r"\b(\w+)\s+\1\b", r"\1", text, flags=re.I)


_LOOSE_STAGE = re.compile(r"\bStage\s*([1-4])\b|\b(Unstageable)\b|\b(Deep Tissue)", re.I)


def extract_attributes(text: str) -> dict | None:
    """Type / location / stage from text WITHOUT requiring a measurement.

    Used for ICD-10 diagnosis descriptions (e.g. 'Stage 3 Pressure Ulcer – Right hip'),
    which carry wound identity but no dimensions, so they can still be an evidence node.
    """
    if not text:
        return None
    out: dict = {}
    for rx, label in _TYPE_HINTS:
        if rx.search(text):
            out["wound_type"] = label
            break
    lm = LOC.search(text)
    if lm:
        out["location"] = f"{_norm_lat(lm.group('lat'))} {lm.group('site').lower()}"
    sm = _LOOSE_STAGE.search(text) or STAGE.search(text)
    if sm:
        raw = sm.group(0)
        digit = re.search(r"[1-4]", raw)
        if digit:
            out["stage"], out["stage_status"] = digit.group(0), "staged"
        elif re.search(r"unstageable", raw, re.I):
            out["stage"], out["stage_status"] = "unstageable", "unstageable"
        elif re.search(r"deep tissue", raw, re.I):
            out["stage"], out["stage_status"] = "DTI", "deep_tissue_injury"
    return out or None


def find_wounds(text: str) -> list[dict]:
    """Find every wound (one per DIM match) with fields + evidence spans.

    Returns list of dicts: {length_cm,width_cm,depth_cm,stage,stage_status,
    drainage,location,wound_type, *_span} — span tuples are (start,end) into text.
    """
    if not text:
        return []
    wounds: list[dict] = []
    for m in DIM.finditer(text):
        w: dict = {"_dim_pos": m.start()}
        w["length_cm"] = _norm_unit(m.group("l"), m.group("lu"))
        w["width_cm"] = _norm_unit(m.group("w"), m.group("wu"))
        w["measure_span"] = (m.start(), m.end())
        if m.group("d"):
            w["depth_cm"] = _norm_unit(m.group("d"), m.group("du"))
        else:
            w["depth_cm"] = None
        wounds.append(w)

    # split-clause depth -> attach to nearest preceding DIM
    for dm in DEPTH.finditer(text):
        dval = dm.group("d1") or dm.group("d2")
        dunit = dm.group("u1") or dm.group("u2")
        if dval is None:
            continue
        depth = _norm_unit(dval, dunit)
        prev = [w for w in wounds if w["_dim_pos"] <= dm.start()]
        target = prev[-1] if prev else (wounds[0] if wounds else None)
        if target and target.get("depth_cm") is None:
            target["depth_cm"] = depth
            target["depth_span"] = (dm.start(), dm.end())

    # locations (in order) -> assign to wounds positionally
    locs = [(lm.start(), f"{_norm_lat(lm.group('lat'))} {lm.group('site').lower()}", (lm.start(), lm.end()))
            for lm in LOC.finditer(text)]
    for i, w in enumerate(wounds):
        if i < len(locs):
            w["location"] = locs[i][1]
            w["location_span"] = locs[i][2]
        elif locs:
            w["location"] = locs[0][1]
            w["location_span"] = locs[0][2]

    # stage (first occurrence applied to primary; shared otherwise)
    sm = STAGE.search(text)
    stage_val, stage_status, stage_span = None, "missing", None
    if sm:
        raw = sm.group("stage")
        stage_span = (sm.start("stage"), sm.end("stage"))
        digit = re.search(r"[1-4]", raw)
        if digit:
            stage_val, stage_status = digit.group(0), "staged"
        elif re.search(r"n/?a", raw, re.I):
            stage_val, stage_status = "N/A", "not_applicable"
        elif re.search(r"unstageable", raw, re.I):
            stage_val, stage_status = "unstageable", "unstageable"
        elif re.search(r"deep tissue", raw, re.I):
            stage_val, stage_status = "DTI", "deep_tissue_injury"

    # drainage (amount enum)
    drainage, drain_span = None, None
    for dm in DRAIN.finditer(text):
        key = re.sub(r"\s+", " ", dm.group(1).lower())
        if key in DRAIN_MAP:
            drainage = DRAIN_MAP[key]
            drain_span = (dm.start(1), dm.end(1))
            break

    # wound type hint
    wtype = None
    for rx, label in _TYPE_HINTS:
        if rx.search(text):
            wtype = label
            break

    for w in wounds:
        w["stage"] = stage_val
        w["stage_status"] = stage_status
        w["stage_span"] = stage_span
        w["drainage"] = drainage
        w["drainage_span"] = drain_span
        w["wound_type"] = wtype
        # implausible guard
        if (w.get("length_cm") or 0) > 50 or (w.get("width_cm") or 0) > 50:
            w["quality_flag"] = "implausible_magnitude"
        elif w.get("depth_cm") and w.get("length_cm") and w["depth_cm"] > w["length_cm"]:
            w["quality_flag"] = "depth_gt_length"
        w.pop("_dim_pos", None)
    return wounds
