"""S3 — format sniffer. Classify a note/assessment from its TEXT, not note_type.

SPEC spec-extraction §2.1. Order is load-bearing (Envive precedes labeled_spn).
"""
from __future__ import annotations

import json
import re

from woundpipe.models import NoteFormat

_SOAP = re.compile(r"^\s*Subjective:", re.M)
_SOAP2 = re.compile(r"^\s*Objective:", re.M)
_SLASH_STAGE = re.compile(r"Measures\b.*?/\s*Stage:", re.I | re.S)
_MEAS = re.compile(r"\bMeas\b")
_DIM_HINT = re.compile(r"\b(measures|depth|\d+(?:\.\d+)?\s*(?:cm|mm)?\s*[x×])", re.I)


def detect_format(text: str, is_assessment: bool = False) -> tuple[NoteFormat, float]:
    """Return (format, confidence in [0,1])."""
    if text is None:
        return NoteFormat.UNKNOWN, 0.3
    if is_assessment:
        # try to parse raw_json shape
        try:
            obj = json.loads(text)
            if isinstance(obj, dict) and "sections" not in obj and any(
                k in obj for k in ("wound_type", "length_cm", "stage", "location")
            ):
                return NoteFormat.ASSESS_FLAT, 0.95
            if isinstance(obj, dict) and "sections" in obj:
                return NoteFormat.ASSESS_NARRATIVE, 0.9
        except (json.JSONDecodeError, TypeError):
            pass
        # assessment whose raw_json isn't flat -> treat body as narrative
        return NoteFormat.ASSESS_NARRATIVE, 0.8

    t = text.lstrip()
    if t.startswith("*Envive"):
        return NoteFormat.ENVIVE, 0.99
    if _SOAP.search(text) and _SOAP2.search(text):
        return NoteFormat.SOAP, 0.95
    if _SLASH_STAGE.search(text):
        return NoteFormat.SPN, 0.85
    if _MEAS.search(text):
        return NoteFormat.PROSE, 0.70
    if _DIM_HINT.search(text):
        return NoteFormat.PROSE, 0.60
    return NoteFormat.UNKNOWN, 0.30


def unwrap_assessment(raw_json: str) -> str:
    """Pull the narrative 'answer' out of a nested assessment, else return as-is."""
    try:
        obj = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return raw_json or ""
    if isinstance(obj, dict) and "sections" in obj:
        parts = []
        for sec in obj.get("sections", []):
            for q in sec.get("questions", []):
                ans = q.get("answer")
                if ans:
                    parts.append(str(ans))
        return " / ".join(parts) if parts else (raw_json or "")
    return raw_json or ""
