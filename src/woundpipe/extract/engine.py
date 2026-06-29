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
  evidence_span_start, evidence_span_end, evidence_quote, extracted_at, wound_key)
 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""

# The DB stage CHECK accepts only {'1','2','3','4','unstageable','DTI','N/A'}, but
# the LLM tool schema speaks 'deep_tissue_injury'/'not_applicable'. Map every lane
# onto the DB vocabulary so a long-form value can never trip the CHECK constraint;
# anything unrecognized becomes NULL (fail-safe — never raise on persist).
_STAGE_ALLOWED = {"1", "2", "3", "4", "unstageable", "DTI", "N/A"}
_STAGE_ALIASES = {
    "deep_tissue_injury": "DTI", "deep tissue injury": "DTI", "dti": "DTI",
    "not_applicable": "N/A", "not applicable": "N/A", "na": "N/A", "n/a": "N/A",
    "none": "N/A", "unstageable": "unstageable", "unstageable/unspecified": "unstageable",
    "1": "1", "2": "2", "3": "3", "4": "4",
    "stage 1": "1", "stage 2": "2", "stage 3": "3", "stage 4": "4",
}


def _norm_stage(stage) -> str | None:
    """Coerce any lane's stage value to the DB-allowed set, else None."""
    if stage is None:
        return None
    s = str(stage).strip()
    if s in _STAGE_ALLOWED:
        return s
    return _STAGE_ALIASES.get(s.lower())


def _persist(con, patient_id, source_kind, w, *, method, is_primary, note_id=None,
             assess_id=None, note_text=None, now=""):
    fc = w.get("field_confidence", {})
    span = w.get("measure_span")
    quote = note_text[span[0]:span[1]] if (span and note_text) else None
    cur = con.execute(_INSERT, (
        patient_id, source_kind, note_id, assess_id, 1 if is_primary else 0, method,
        w.get("wound_type"), fc.get("wound_type"), _norm_stage(w.get("stage")), fc.get("stage"),
        w.get("location"), fc.get("location"),
        w.get("length_cm"), w.get("width_cm"), w.get("depth_cm"), fc.get("length_cm"),
        w.get("drainage"), fc.get("drainage"), w.get("overall_conf"),
        span[0] if span else None, span[1] if span else None, quote, now, w.get("wound_key"),
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


def _gap_fill_candidate(con, w, ntext, settings) -> None:
    """Fill a single note wound's NULL fields from its matching cached LLM wound,
    BEFORE clustering — so a location the regex couldn't read (e.g. "Rightlowerle")
    is present when wound identity is computed. Match by measurement (regex owns the
    numbers); fall back to the sole LLM wound. Measurements were span-gated on cache,
    so anything filled is a verbatim value from the note — never invented."""
    llm_ws = llm_lane.cached_wounds(con, ntext, settings)
    if not llm_ws:
        return

    def meas_match(lw) -> bool:
        return (
            w.get("length_cm") is not None
            and lw.get("length_cm") == w.get("length_cm")
            and lw.get("width_cm") == w.get("width_cm")
        )

    lw = next((x for x in llm_ws if meas_match(x)), None)
    if lw is None and len(llm_ws) == 1:
        lw = llm_ws[0]
    if lw is None:
        return
    for f in _GAP_FIELDS:
        if w.get(f) is None and lw.get(f) is not None:
            w[f] = lw[f]


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

    # LLM gap-fill happens BEFORE clustering, per note candidate, so a location the regex
    # missed is present when wound identity is computed. Regex still owns every number.
    if settings.use_llm and settings.anthropic_api_key:
        for w, sk, _nid, _aid, _method, ntext in candidates:
            if sk == "note" and ntext:
                _gap_fill_candidate(con, w, ntext, settings)

    # Identity: cluster candidate + dx wounds into canonical wound_keys. Fuzzy, so a
    # garbled site ("Rightlowerle") or unknown type merges into the same physical wound;
    # same key across sources = same wound, different keys = different wounds.
    pairs = [(it[0].get("wound_type"), it[0].get("location")) for it in candidates]
    pairs += [(dw.get("wound_type"), dw.get("location")) for dw in dx_wounds]
    keymap = regex_lane.cluster_identities(pairs)
    for it in candidates:
        it[0]["wound_key"] = keymap[(it[0].get("wound_type"), it[0].get("location"))]
    for dw in dx_wounds:
        dw["wound_key"] = keymap[(dw.get("wound_type"), dw.get("location"))]

    cand_wounds = [c[0] for c in candidates]
    # the patient's single primary wound — drives is_primary + the per-patient rollup view.
    primary = choose_primary(cand_wounds, dx_wounds)
    primary_key = primary.get("wound_key") if primary else None

    # Group candidate source-records by wound identity; each group is ONE billable wound.
    groups: dict[str, list] = {}
    for it in candidates:
        groups.setdefault(it[0]["wound_key"], []).append(it)

    persisted = False
    persisted_keys: set = set()  # (source_kind, note_id, assess_id, wound_key) dedup guard
    for wk, items in groups.items():
        group_wounds = [it[0] for it in items]
        rep = max(group_wounds, key=lambda w: (_completeness_count(w), _area(w)))
        # corroboration sources for THIS wound = its own source rows + matching dx.
        matching_dx = [d for d in dx_wounds if d.get("wound_key") == wk]
        sources = group_wounds + matching_dx
        # Display identity: when a garbled note merged into a cleaner wound, show the
        # canonical type/location (the member whose OWN identity is the cluster key —
        # e.g. the diagnosis "Venous leg ulcer / Right lower leg") instead of the note's
        # "Other / Rightlowerle", while keeping rep's measurements.
        canon = next((m for m in sources
                      if regex_lane.wound_identity(m.get("wound_type"), m.get("location")) == wk), None)
        if canon is not None and canon is not rep:
            if canon.get("wound_type"):
                rep["wound_type"] = canon["wound_type"]
            if canon.get("location"):
                rep["location"] = canon["location"]
        reconcile.reconcile(rep, sources, method="regex")
        # the canonical cluster key is the wound's identity (stable across gap-fill).
        final_key = wk
        is_prim_wound = (wk == primary_key)
        best_by_record: dict[tuple, tuple] = {}
        for w, sk, nid, aid, method, ntext in items:
            rkey = (sk, nid, aid)
            cur = best_by_record.get(rkey)
            if (w is rep) or (cur is None) or (_completeness_count(w) > _completeness_count(cur[0])):
                best_by_record[rkey] = (w, sk, nid, aid, method, ntext)
        for w, sk, nid, aid, method, ntext in best_by_record.values():
            w["wound_key"] = final_key
            dk = (sk, nid, aid, final_key)
            if dk in persisted_keys:
                continue
            persisted_keys.add(dk)
            _persist(con, pid, sk, w, method=method, is_primary=(is_prim_wound and w is rep),
                     note_id=nid, assess_id=aid, note_text=ntext, now=now)
            persisted = True

    # one diagnosis evidence row per distinct dx wound (corroborates its matching wound)
    seen_dx: set = set()
    for dw in dx_wounds:
        if dw["wound_key"] in seen_dx:
            continue
        seen_dx.add(dw["wound_key"])
        _persist(con, pid, "diagnosis", dw, method="manual", is_primary=False, now=now)
        persisted = True
    return persisted


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
