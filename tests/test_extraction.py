"""Extraction correctness — the algorithmic core (SPEC acceptance #5, #6)."""
import os
import tempfile

from woundpipe.config import load_settings
from woundpipe.db import migrate
from woundpipe.db.engine import connect
from woundpipe.extract import engine, llm_lane
from woundpipe.extract.sniff import detect_format, unwrap_assessment
from woundpipe.extract.regex_lane import find_wounds, collapse_dups
from woundpipe.models import NoteFormat


def test_envive_format_and_2d_measure():
    txt = ("*Envive Care Conference Review - V 4.0\nWound Status: Pressure Ulcer to Right hip / "
           "Measures 2.9 cm x 2.8 cm / Stage: Stage 3\nDrainage present - serosanguineous, heavy.")
    fmt, conf = detect_format(txt)
    assert fmt is NoteFormat.ENVIVE and conf >= 0.9
    w = find_wounds(txt)[0]
    assert w["length_cm"] == 2.9 and w["width_cm"] == 2.8 and w["depth_cm"] is None
    assert w["stage"] == "3" and w["drainage"] == "heavy" and w["location"] == "Right hip"


def test_soap_3d_and_dup_typo_collapse():
    txt = ("Subjective: pain 9/10.\nObjective: Diabetic diabetic Right plantar measures "
           "4.3 cm x 1.8 cm x 0.3 cm. Drainage: moderate.")
    fmt, _ = detect_format(txt)
    assert fmt is NoteFormat.SOAP
    w = find_wounds(collapse_dups(txt))[0]
    assert (w["length_cm"], w["width_cm"], w["depth_cm"]) == (4.3, 1.8, 0.3)
    assert w["drainage"] == "moderate" and w["wound_type"] == "diabetic_foot_ulcer"


def test_multi_wound_split():
    txt = ("Pressure Ulcer Left buttock measures aprx 5.9 x 4.5cm, depth 1.8cm. "
           "Heel wound also eval - L heel 3.5x2.7, 0.9cm deep.")
    wounds = find_wounds(collapse_dups(txt))
    assert len(wounds) == 2
    assert wounds[0]["location"] == "Left buttock" and wounds[0]["depth_cm"] == 1.8
    assert wounds[1]["location"] == "Left heel" and wounds[1]["depth_cm"] == 0.9


def test_stage_na_maps_to_not_applicable():
    txt = "Wound Status: Pressure Ulcer to Left buttock / Measures 5.9 cm x 4.5 cm / Stage: N/A"
    w = find_wounds(txt)[0]
    assert w["stage"] == "N/A" and w["stage_status"] == "not_applicable"


def test_no_fabricated_measurements_spans_are_literal():
    """Every measurement span must re-index to a real substring (acceptance #6)."""
    txt = "Right hip Measures 2.9 cm x 2.8 cm / Stage: Stage 3"
    w = find_wounds(txt)[0]
    s, e = w["measure_span"]
    assert "2.9" in txt[s:e] and "2.8" in txt[s:e]


def test_incremental_extract_skips_unchanged_reextracts_on_change():
    """Fix 1: extract is incremental — a patient is re-extracted only when its
    source fingerprint changes; unchanged patients are skipped."""
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        con = connect(path); migrate.migrate_up(con); con.commit()
        now = "2026-06-28T00:00:00"
        s = load_settings(use_llm=False)
        con.execute("INSERT INTO pcc_patient (patient_id,id,facility_id,fetched_at,raw_payload) "
                    "VALUES ('FA-001',1,101,?, '{}')", (now,))
        con.execute("INSERT INTO progress_note (id,patient_id,note_text,fetched_at,sync_version,raw_payload) "
                    "VALUES (1,1,?,?,1,'{}')",
                    ("Pressure Ulcer to Right hip / Measures 2.9 cm x 2.8 cm / Stage: Stage 3", now))
        con.commit()

        cold = engine.extract_all(con, s)
        assert (cold["dirty"], cold["skipped"]) == (1, 0)

        warm = engine.extract_all(con, s)
        assert (warm["dirty"], warm["skipped"]) == (0, 1)   # unchanged -> skipped

        # a content change bumps sync_version -> the patient goes dirty again
        con.execute("UPDATE progress_note SET sync_version=sync_version+1 WHERE id=1"); con.commit()
        changed = engine.extract_all(con, s)
        assert (changed["dirty"], changed["skipped"]) == (1, 0)

        # --full forces a rebuild regardless of the watermark
        forced = engine.extract_all(con, s, full=True)
        assert (forced["dirty"], forced["skipped"]) == (1, 0)
        con.close()
    finally:
        for ext in ("", "-wal", "-shm"):
            try: os.remove(path + ext)
            except OSError: pass


def test_llm_span_gate_drops_fabricated_measurement_keeps_verbatim():
    """Fix 2 safety: a measurement whose evidence span is not a literal substring
    of the note is dropped; a verbatim one survives. (anti-hallucination)"""
    note = "Right hip Measures 2.9 cm x 2.8 cm"
    gated = llm_lane.apply_llm_results(
        [{"length_cm": 2.9, "length_evidence_span": "2.9 cm",
          "depth_cm": 1.5, "depth_evidence_span": "depth 1.5 cm deep"}], note)[0]
    assert gated["length_cm"] == 2.9    # verbatim in note -> kept
    assert gated["depth_cm"] is None    # not in note -> dropped


def test_llm_prefill_dedups_caches_and_gap_fills():
    """Fix 2: the LLM pre-pass calls the (injected) model once per DISTINCT note,
    caches the result, and gap-fills only the regex-null fields of the primary."""
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        con = connect(path); migrate.migrate_up(con); con.commit()
        now = "2026-06-28T00:00:00"
        s = load_settings(use_llm=True, anthropic_api_key="test-key", llm_concurrency=2)
        # two patients share the SAME note text (regex finds L×W but NOT depth)
        note = "Pressure Ulcer to Right hip / Measures 2.9 cm x 2.8 cm / Stage: Stage 3"
        for pid, iid in (("FA-001", 1), ("FA-002", 2)):
            con.execute("INSERT INTO pcc_patient (patient_id,id,facility_id,fetched_at,raw_payload) "
                        "VALUES (?,?,101,?, '{}')", (pid, iid, now))
            con.execute("INSERT INTO progress_note (id,patient_id,note_text,fetched_at,sync_version,raw_payload) "
                        "VALUES (?,?,?,?,1,'{}')", (iid, iid, note, now))
        con.commit()

        calls = []
        def fake(text, fmt, settings):
            calls.append(text)
            return [{"is_primary": True, "wound_type": "pressure_ulcer",
                     "location": "Right hip", "depth_cm": 0.4}]   # LLM supplies the missing depth

        res = engine.extract_all(con, s, llm_caller=fake)
        assert len(calls) == 1                       # 2 patients, identical note -> 1 distinct call
        assert res["llm"]["called"] == 1
        assert con.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0] == 1
        # the regex-null depth got gap-filled on the primary
        depth = con.execute("SELECT depth_cm FROM wound_extraction "
                            "WHERE patient_id='FA-001' AND is_primary=1").fetchone()[0]
        assert depth == 0.4

        # second run (full rebuild): cache hit -> the model is NOT called again
        calls2 = []
        engine.extract_all(con, s, full=True, llm_caller=lambda t, f, st: calls2.append(t) or [])
        assert calls2 == []
        con.close()
    finally:
        for ext in ("", "-wal", "-shm"):
            try: os.remove(path + ext)
            except OSError: pass


def test_assessment_unwrap_nested_narrative():
    raw = ('{"sections":[{"questions":[{"question":"Wound narrative",'
           '"answer":"Pressure Ulcer to Right hip / Measures 2.9 cm x 2.8 cm / Stage: Stage 3"}]}]}')
    body = unwrap_assessment(raw)
    assert "Right hip" in body
    w = find_wounds(body)[0]
    assert w["length_cm"] == 2.9
