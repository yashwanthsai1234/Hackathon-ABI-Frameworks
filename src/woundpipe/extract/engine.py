"""S4 — extraction orchestrator.

Per patient: pull active wound diagnoses + notes + assessments, run the lanes
(sniff -> regex -> optional LLM -> reconcile), choose the primary wound, and
persist wound_extraction rows (+ a synthetic 'diagnosis' evidence row so the
diagnosis participates in the corroboration graph) + per-field evidence.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from woundpipe.config import Settings
from woundpipe.extract import llm_lane, reconcile, regex_lane
from woundpipe.extract.sniff import detect_format, unwrap_assessment
from woundpipe.models import NoteFormat

_FMT_METHOD = {
    NoteFormat.ENVIVE: "regex_envive",
    NoteFormat.SOAP: "soap",
    NoteFormat.PROSE: "regex_prose",
    NoteFormat.SPN: "regex_spn",
    NoteFormat.ASSESS_FLAT: "json",
    NoteFormat.ASSESS_NARRATIVE: "json",
    NoteFormat.UNKNOWN: "regex_prose",
}
_WOUND_ICD_PREFIXES = ("L89", "L97", "L98.4", "E11.62", "E10.62", "E08.62",
                       "E09.62", "E13.62", "I83.0", "I83.2", "I70.23", "I70.24", "I70.25")


def _is_wound_dx(code: str | None) -> bool:
    if not code:
        return False
    c = code.upper().replace(".", "")
    return any(c.startswith(p.replace(".", "")) for p in _WOUND_ICD_PREFIXES)


def _wound_from_text(text: str) -> dict | None:
    ws = regex_lane.find_wounds(regex_lane.collapse_dups(text or ""))
    return ws[0] if ws else None


def _area(w: dict) -> float:
    return (w.get("length_cm") or 0) * (w.get("width_cm") or 0)


def _completeness_count(w: dict) -> int:
    return sum(1 for k in ("wound_type", "location", "length_cm", "width_cm", "depth_cm", "drainage")
               if w.get(k) is not None)


def choose_primary(candidates: list[dict], dx_wounds: list[dict]) -> dict | None:
    if not candidates:
        return None
    # 1) dx-site match
    for w in candidates:
        for d in dx_wounds:
            if (w.get("location") and d.get("location") and
                    w["location"].lower() == d["location"].lower()):
                return w
    # 2) most-documented, then 3) largest area
    return sorted(candidates, key=lambda w: (_completeness_count(w), _area(w)), reverse=True)[0]


_INSERT = """INSERT INTO wound_extraction
 (patient_id, source_kind, source_note_id, source_assessment_id, is_primary, extraction_method,
  wound_type, wound_type_conf, stage, stage_conf, location, location_conf,
  length_cm, width_cm, depth_cm, measure_conf, drainage, drainage_conf, overall_conf,
  evidence_span_start, evidence_span_end, evidence_quote, extracted_at)
 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""


def _persist(con, patient_id, source_kind, w, *, method, is_primary, note_id=None,
             assess_id=None, note_text=None, now=""):
    fc = w.get("field_confidence", {})
    span = w.get("measure_span")
    quote = note_text[span[0]:span[1]] if (span and note_text) else None
    cur = con.execute(_INSERT, (
        patient_id, source_kind, note_id, assess_id, 1 if is_primary else 0, method,
        w.get("wound_type"), fc.get("wound_type"), w.get("stage"), fc.get("stage"),
        w.get("location"), fc.get("location"),
        w.get("length_cm"), w.get("width_cm"), w.get("depth_cm"), fc.get("length_cm"),
        w.get("drainage"), fc.get("drainage"), w.get("overall_conf"),
        span[0] if span else None, span[1] if span else None, quote, now,
    ))
    eid = cur.lastrowid
    # per-field evidence for the highlight UI (R1)
    if is_primary and note_text:
        for field, span_key in (("wound_type", None), ("location", "location_span"),
                                ("measure", "measure_span"), ("stage", "stage_span"),
                                ("drainage", "drainage_span")):
            sp = w.get(span_key) if span_key else None
            if sp:
                con.execute(
                    "INSERT INTO wound_field_evidence (extraction_id, field, char_start, char_end, quote, method, confidence)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (eid, field, sp[0], sp[1], note_text[sp[0]:sp[1]], method, fc.get(field if field != "measure" else "length_cm")),
                )
    return eid


def _source_fingerprint(con, pid, iid) -> str:
    """Hash of the patient's source rows (notes+assessments+diagnoses, each as
    id+sync_version). sync_version bumps only on a real content change (unchanged
    payloads short-circuit on the ingest content-hash), so an identical
    fingerprint => nothing to re-extract."""
    rows = con.execute(
        "SELECT 'n' AS k, id, sync_version FROM progress_note  WHERE patient_id=? AND is_current=1 "
        "UNION ALL SELECT 'a', id, sync_version FROM pcc_assessment WHERE patient_id=? AND is_current=1 "
        "UNION ALL SELECT 'd', id, sync_version FROM pcc_diagnosis  WHERE patient_id=? AND is_current=1",
        (iid, iid, pid),
    ).fetchall()
    return hashlib.sha256(
        json.dumps(sorted((r[0], r[1], r[2]) for r in rows)).encode("utf-8")
    ).hexdigest()


_GAP_FIELDS = ("wound_type", "location", "stage", "drainage",
               "length_cm", "width_cm", "depth_cm")


def _gap_fill_from_llm(con, primary, candidates, settings) -> None:
    """Fill the primary wound's NULL fields from the cached LLM extraction of its
    own note. Measurements were span-gated when cached, so anything filled is a
    verbatim value present in the note — never invented, never an override."""
    src = next((c for c in candidates if c[0] is primary and c[1] == "note"), None)
    if src is None:
        return
    llm_ws = llm_lane.cached_wounds(con, src[5], settings)
    if not llm_ws:
        return
    lw = next((x for x in llm_ws if x.get("is_primary")), llm_ws[0])
    for f in _GAP_FIELDS:
        if primary.get(f) is None and lw.get(f) is not None:
            primary[f] = lw[f]


def _dirty_notes(con, iids):
    """(text, fmt) for every current note of the given patients — the LLM pre-pass
    work set (deduped downstream by cache key)."""
    out = []
    for iid in iids:
        for (ntext,) in con.execute(
            "SELECT note_text FROM progress_note WHERE patient_id=? AND is_current=1", (iid,)
        ).fetchall():
            if ntext:
                fmt, _ = detect_format(ntext)
                out.append((ntext, fmt.value))
    return out


def _extract_one(con, pid, iid, settings, now, by_format) -> bool:
    """Extract one patient's wounds into wound_extraction (+ per-field evidence).

    Returns True iff anything was extracted. The caller owns clearing the
    patient's prior rows and committing the surrounding transaction."""
    # --- diagnosis evidence wounds ---
    dx_wounds = []
    for code, desc in con.execute(
        "SELECT icd10_code, icd10_description FROM pcc_diagnosis "
        "WHERE patient_id=? AND clinical_status='active'", (pid,)
    ).fetchall():
        if _is_wound_dx(code):
            dw = regex_lane.extract_attributes(desc or "")
            if dw:
                dx_wounds.append(dw)

    candidates = []   # (wound, source_kind, note_id, assess_id, method, note_text)
    # --- notes ---
    for nid, ntext in con.execute(
        "SELECT id, note_text FROM progress_note WHERE patient_id=? AND is_current=1", (iid,)
    ).fetchall():
        fmt, fconf = detect_format(ntext or "")
        by_format[fmt.value] = by_format.get(fmt.value, 0) + 1
        text = regex_lane.collapse_dups(ntext or "")
        ws = regex_lane.find_wounds(text)
        for w in ws:
            candidates.append((w, "note", nid, None, _FMT_METHOD[fmt], ntext))
    # --- assessments ---
    for aid, rawj in con.execute(
        "SELECT id, raw_json FROM pcc_assessment WHERE patient_id=? AND is_current=1", (iid,)
    ).fetchall():
        body = unwrap_assessment(rawj or "")
        fmt, _ = detect_format(rawj or "", is_assessment=True)
        ws = regex_lane.find_wounds(regex_lane.collapse_dups(body))
        for w in ws:
            candidates.append((w, "assessment", None, aid, "json", body))

    if not candidates and not dx_wounds:
        return False

    cand_wounds = [c[0] for c in candidates]
    primary = choose_primary(cand_wounds, dx_wounds)
    # LLM gap-fill: the Claude lane (cached/pre-fetched) fills ONLY fields the
    # regex left null, through the verbatim-span gate — regex still owns every
    # number it found; the LLM can never override or fabricate one.
    if primary and settings.use_llm and settings.anthropic_api_key:
        _gap_fill_from_llm(con, primary, candidates, settings)
    # corroboration sources = all candidate wounds + dx wounds
    sources = cand_wounds + dx_wounds
    if primary:
        method = next((c[4] for c in candidates if c[0] is primary), "regex_prose")
        reconcile.reconcile(primary, sources, method="regex")

    # One row per SOURCE RECORD (each source = one evidence node). Multi-wound
    # notes collapse to that record's best wound (the primary if present here).
    best_by_record: dict[tuple, tuple] = {}
    for w, sk, nid, aid, method, ntext in candidates:
        key = (sk, nid, aid)
        cur = best_by_record.get(key)
        better = (w is primary) or (cur is None) or (
            _completeness_count(w) > _completeness_count(cur[0]))
        if better:
            best_by_record[key] = (w, sk, nid, aid, method, ntext)
    for w, sk, nid, aid, method, ntext in best_by_record.values():
        _persist(con, pid, sk, w, method=method, is_primary=(w is primary),
                 note_id=nid, assess_id=aid, note_text=ntext, now=now)
    # one diagnosis evidence row (best dx, matching primary location if any)
    if dx_wounds:
        dw = next((d for d in dx_wounds if primary and d.get("location")
                   and primary.get("location")
                   and d["location"].lower() == primary["location"].lower()), dx_wounds[0])
        _persist(con, pid, "diagnosis", dw, method="manual", is_primary=False, now=now)
    return True


def extract_all(con: sqlite3.Connection, settings: Settings, manifest=None,
                *, full: bool = False, llm_caller=None) -> dict:
    """Incremental extract: only re-process patients whose source fingerprint
    changed since their last extraction (or everyone when ``full``). Each dirty
    patient is cleared + re-extracted + watermarked in its own transaction, so
    memory is bounded and an interrupted run resumes cheaply.

    The optional Claude lane runs as a bounded CONCURRENT, content-cached
    pre-pass over the dirty patients' notes BEFORE the per-patient loop, so the
    loop only reads cached results (Fix 2). ``llm_caller`` is injectable for
    tests; defaults to the real backoff-wrapped Claude call."""
    from woundpipe.ingest.checkpoint import now_iso
    now = now_iso()
    seen = {r[0]: r[1] for r in con.execute("SELECT patient_id, fingerprint FROM extract_state")}
    patients = con.execute("SELECT patient_id, id FROM pcc_patient WHERE is_current=1").fetchall()

    # 1) resolve the dirty set up front (cheap fingerprint compare).
    dirty: list[tuple] = []
    n_skipped = 0
    for pid, iid in patients:
        fp = _source_fingerprint(con, pid, iid)
        if not full and seen.get(pid) == fp:
            n_skipped += 1
        else:
            dirty.append((pid, iid, fp))

    # 2) LLM pre-pass: concurrently cache distinct notes for the dirty patients
    #    (no-op when the lane is off — the regex floor needs no LLM).
    llm_summary = None
    if settings.use_llm and settings.anthropic_api_key and dirty:
        notes = _dirty_notes(con, [iid for _, iid, _ in dirty])
        llm_summary = llm_lane.prefill_cache(con, notes, settings, caller=llm_caller)

    # 3) per-patient extract (reads the cache; never calls the API inline).
    by_format: dict[str, int] = {}
    n_extracted = 0
    for pid, iid, fp in dirty:
        con.execute(
            "DELETE FROM wound_field_evidence WHERE extraction_id IN "
            "(SELECT id FROM wound_extraction WHERE patient_id=?)", (pid,))
        con.execute("DELETE FROM wound_extraction WHERE patient_id=?", (pid,))
        if _extract_one(con, pid, iid, settings, now, by_format):
            n_extracted += 1
        con.execute(
            "INSERT INTO extract_state (patient_id, fingerprint, extracted_at) VALUES (?,?,?) "
            "ON CONFLICT(patient_id) DO UPDATE SET "
            "fingerprint=excluded.fingerprint, extracted_at=excluded.extracted_at",
            (pid, fp, now))
        con.commit()

    res = {"patients_with_wounds": n_extracted, "by_format": by_format,
           "dirty": len(dirty), "skipped": n_skipped}
    if llm_summary is not None:
        res["llm"] = llm_summary
    return res
