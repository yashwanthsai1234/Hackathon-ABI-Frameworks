"""Lane 1 — deterministic regex extraction (OWNS measurements).

SPEC spec-extraction §2.2/§2.5. Every pattern returns the literal matched
substring (for the verbatim-span / highlight) or None. Numbers are never invented.
Validated against the real archetypes (0 misses).
"""
from __future__ import annotations

import difflib
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
# longer multi-word sites first so "lower leg" wins over the bare "leg" alternative.
_SITE = (r"(?:lower\s+leg|lower\s+extremity|hip|buttock|sacrum|coccyx|heel|plantar|foot|ankle|"
         r"trochanter|ischium|toe|knee|elbow|back|trunk|shoulder|leg)")
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


def wound_identity(wound_type: str | None, location: str | None) -> str:
    """Stable per-patient wound key = normalized wound_type|location.

    Two extractions (note / assessment / diagnosis) with the same key ARE the same
    physical wound; different keys are different wounds. Null/blank location groups
    by type only (documented edge case — same-type wounds with no site collapse)."""
    t = (wound_type or "unknown").strip().lower()
    loc = re.sub(r"\s+", "_", (location or "").strip().lower())
    return f"{t}|{loc}"


# --- fuzzy wound clustering --------------------------------------------------
# Exact wound_identity over-splits when a source note garbles a site ("Rightlowerle"
# vs "Right lower leg") or leaves the type unknown ("other"). cluster_identities
# merges near-duplicate locations / unknown types into one canonical wound.
_UNKNOWN_TYPES = {None, "", "other", "unknown"}


def _norm_loc(location: str | None) -> str:
    """Compare-form of a location: lowercase, alphanumerics only (laterality is
    already canonical via _norm_lat). 'Right lower leg' -> 'rightlowerleg'."""
    return re.sub(r"[^a-z0-9]", "", (location or "").lower())


def _loc_similar(a: str, b: str) -> bool:
    if not a or not b:
        return a == b
    if (len(a) >= 6 and b.startswith(a)) or (len(b) >= 6 and a.startswith(b)):
        return True  # truncation: 'rightlowerle' ⊂ 'rightlowerleg'
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


def _type_compat(t1: str | None, t2: str | None) -> bool:
    """Same specific type, or one side is unknown/'other' (adopts the specific one)."""
    if t1 in _UNKNOWN_TYPES or t2 in _UNKNOWN_TYPES:
        return True
    return t1 == t2


def cluster_identities(pairs) -> dict[tuple, str]:
    """Map each (wound_type, location) pair to a CANONICAL wound_key, merging
    near-duplicate locations / unknown types into one wound. Deterministic: seed
    clusters from the most specific members (known type, longest location) so the
    canonical key comes from the best-described mention."""
    uniq = list({(p[0], p[1]) for p in pairs})
    norm = {p: (None if p[0] in _UNKNOWN_TYPES else p[0], _norm_loc(p[1])) for p in uniq}
    order = sorted(uniq, key=lambda p: (norm[p][0] is None, -len(norm[p][1]), str(p)))
    clusters: list[dict] = []  # {"ctype", "nlocs":[...], "key"}
    mapping: dict[tuple, str] = {}
    for p in order:
        ntype, nloc = norm[p]
        match = next(
            (c for c in clusters if _type_compat(ntype, c["ctype"]) and any(_loc_similar(nloc, x) for x in c["nlocs"])),
            None,
        )
        if match is None:
            clusters.append({"ctype": ntype, "nlocs": [nloc], "key": wound_identity(p[0], p[1])})
            mapping[p] = clusters[-1]["key"]
        else:
            match["nlocs"].append(nloc)
            if match["ctype"] is None and ntype is not None:
                match["ctype"] = ntype  # adopt the specific type (canonical key unchanged)
            mapping[p] = match["key"]
    return mapping


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

    # locations -> assign each to the NEAREST wound's measurement, globally-greedy:
    # pair (wound, location) by ascending text distance, within a window. A location
    # is claimed by at most one wound, and a wound with no nearby location is left
    # null (honestly incomplete) rather than borrowing another wound's site — this is
    # what stops two distinct wounds from being conflated into one.
    locs = [(lm.start(), lm.end(), f"{_norm_lat(lm.group('lat'))} {lm.group('site').lower()}")
            for lm in LOC.finditer(text)]
    LOC_WINDOW = 120
    pairs = sorted(
        (abs(ls - w["_dim_pos"]), wi, li)
        for wi, w in enumerate(wounds)
        for li, (ls, _le, _lv) in enumerate(locs)
    )
    w_claimed: set[int] = set()
    l_claimed: set[int] = set()
    for dist, wi, li in pairs:
        if dist > LOC_WINDOW:
            break
        if wi in w_claimed or li in l_claimed:
            continue
        ls, le, lv = locs[li]
        wounds[wi]["location"] = lv
        wounds[wi]["location_span"] = (ls, le)
        w_claimed.add(wi)
        l_claimed.add(li)

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
