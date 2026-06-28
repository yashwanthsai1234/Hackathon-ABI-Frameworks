"""Routing oracle + SQL view equality (SPEC R5 CI gate) and confidence bounds."""
import tempfile, os
import pytest

from woundpipe.db.engine import connect
from woundpipe.db import migrate
from woundpipe.route.eligibility import classify
from woundpipe.extract.reconcile import reconcile


def test_oracle_reject_no_mcb():
    assert classify(has_active_mcb=False, has_active_wound=True, wound_type="pressure_ulcer",
                    length_cm=2, width_cm=2, depth_cm=1, drainage="heavy", overall_conf=0.9,
                    all_agree=True, n_sources=3, has_wound_dx=True) == "reject"


def test_oracle_flag_missing_depth():
    assert classify(has_active_mcb=True, has_active_wound=True, wound_type="pressure_ulcer",
                    length_cm=2, width_cm=2, depth_cm=None, drainage="heavy", overall_conf=0.9,
                    all_agree=True, n_sources=3, has_wound_dx=True) == "flag_for_review"


def test_oracle_auto_accept_full_corroborated():
    assert classify(has_active_mcb=True, has_active_wound=True, wound_type="pressure_ulcer",
                    length_cm=2, width_cm=2, depth_cm=1, drainage="heavy", overall_conf=0.9,
                    all_agree=True, n_sources=2, has_wound_dx=True) == "auto_accept"


def test_oracle_incomplete_fetch_flags_would_be_auto_accept():
    """A complete+corroborated patient with a FAILED fan-out fetch must NOT
    auto_accept — incomplete data flags instead (Fix 3b)."""
    kw = dict(has_active_mcb=True, has_active_wound=True, wound_type="pressure_ulcer",
              length_cm=2, width_cm=2, depth_cm=1, drainage="heavy", overall_conf=0.9,
              all_agree=True, n_sources=2, has_wound_dx=True)
    assert classify(**kw) == "auto_accept"
    assert classify(**kw, failed_fetches=1) == "flag_for_review"


def test_oracle_incomplete_fetch_still_rejects_no_mcb():
    """Incompleteness never overrides a definitive reject (no MCB)."""
    assert classify(has_active_mcb=False, has_active_wound=True, wound_type="pressure_ulcer",
                    length_cm=2, width_cm=2, depth_cm=1, drainage="heavy", overall_conf=0.9,
                    all_agree=True, n_sources=2, has_wound_dx=True, failed_fetches=3) == "reject"


def test_confidence_in_bounds_and_complete():
    primary = {"wound_type": "pressure_ulcer", "location": "Right hip", "length_cm": 2.9,
               "width_cm": 2.8, "depth_cm": 0.4, "drainage": "heavy", "stage": "3",
               "stage_status": "staged"}
    src2 = dict(primary)
    reconcile(primary, [primary, src2], method="regex")
    assert 0.0 <= primary["overall_conf"] <= 1.0
    assert primary["completeness"] == 1.0
    assert primary["agreement"] == "agree"


def test_sql_view_matches_oracle_on_seeded_db():
    """The v_patient_eligibility VIEW must agree with the Python oracle (R5)."""
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        con = connect(path); migrate.migrate_up(con); con.commit()
        now = "2026-06-28T00:00:00"
        # FA-001: MCB active + active L89 dx + complete corroborated wound -> auto_accept
        con.execute("INSERT INTO pcc_patient (patient_id,id,facility_id,fetched_at,raw_payload) VALUES ('FA-001',1,101,?, '{}')", (now,))
        con.execute("INSERT INTO pcc_coverage (id,patient_id,payer_code,effective_to,fetched_at,raw_payload) VALUES (1,'FA-001','MCB',NULL,?,'{}')", (now,))
        con.execute("INSERT INTO pcc_diagnosis (id,patient_id,icd10_code,clinical_status,fetched_at,raw_payload) VALUES (1,'FA-001','L89.143','active',?,'{}')", (now,))
        for sk, prim in (("note", 1), ("assessment", 0), ("diagnosis", 0)):
            con.execute(
                "INSERT INTO wound_extraction (patient_id,source_kind,is_primary,extraction_method,"
                "wound_type,stage,location,length_cm,width_cm,depth_cm,drainage,overall_conf,extracted_at)"
                " VALUES ('FA-001',?,?, 'regex_envive','pressure_ulcer','3','Right hip',2.9,2.8,0.4,'heavy',0.9,?)",
                (sk, prim, now))
        con.commit()
        row = con.execute("SELECT route, has_active_mcb, n_sources FROM v_patient_eligibility WHERE patient_id='FA-001'").fetchone()
        view_route = row[0]
        oracle = classify(has_active_mcb=bool(row[1]), has_active_wound=True, wound_type="pressure_ulcer",
                          length_cm=2.9, width_cm=2.8, depth_cm=0.4, drainage="heavy", overall_conf=0.9,
                          all_agree=True, n_sources=row[2], has_wound_dx=True)
        assert view_route == oracle == "auto_accept", f"view={view_route} oracle={oracle}"

        # Fix 3b: a FAILED fan-out fetch makes the same patient incomplete ->
        # both the VIEW and the oracle must downgrade auto_accept to flag.
        con.execute(
            "INSERT INTO fetch_log (task_id,endpoint,identity_kind,identity_value,status,planned_at)"
            " VALUES ('notes:1','notes','id','1','failed',?)", (now,))
        con.commit()
        row = con.execute(
            "SELECT route, failed_fetches FROM v_patient_eligibility WHERE patient_id='FA-001'"
        ).fetchone()
        oracle2 = classify(has_active_mcb=True, has_active_wound=True, wound_type="pressure_ulcer",
                           length_cm=2.9, width_cm=2.8, depth_cm=0.4, drainage="heavy", overall_conf=0.9,
                           all_agree=True, n_sources=3, has_wound_dx=True,
                           failed_fetches=row["failed_fetches"])
        assert row["route"] == oracle2 == "flag_for_review", f"view={row['route']} oracle={oracle2}"
        con.close()
    finally:
        for ext in ("", "-wal", "-shm"):
            try: os.remove(path + ext)
            except OSError: pass
