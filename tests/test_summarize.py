"""AI summary: deterministic template output + idempotent backfill (no network)."""
import os
import tempfile

from woundpipe.config import load_settings
from woundpipe.db import migrate
from woundpipe.db.engine import connect
from woundpipe import summarize


def test_template_summary_covers_every_route_and_is_null_safe():
    accept = {
        "first_name": "Agnes", "last_name": "Dunbar", "payer_code": "MCB",
        "wound_type": "pressure_ulcer", "stage": "3", "location": "Right hip",
        "length_cm": 2.9, "width_cm": 2.8, "depth_cm": 0.4, "drainage": "heavy",
        "route": "auto_accept", "reason": "safe to bill", "n_sources": 3, "n_conflict": 0,
    }
    flag = {**accept, "depth_cm": None, "route": "flag_for_review",
            "reason": "Missing depth measurement"}
    reject = {"first_name": None, "last_name": None, "payer_code": "HMO",
              "wound_type": None, "stage": None, "location": None,
              "length_cm": None, "width_cm": None, "depth_cm": None, "drainage": None,
              "route": "reject", "reason": "No active Medicare Part B coverage",
              "n_sources": 0, "n_conflict": 0}

    for row in (accept, flag, reject):
        text = summarize.template_summary(row)
        assert isinstance(text, str) and len(text) > 20

    assert "ready to bill" in summarize.template_summary(accept).lower()
    assert "review" in summarize.template_summary(flag).lower()
    assert "not billable" in summarize.template_summary(reject).lower()
    # null-safe: no name, no wound -> still a sentence, no crash / "None"
    assert "None" not in summarize.template_summary(reject)


def test_backfill_fills_only_nulls_and_is_idempotent():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        con = connect(path); migrate.migrate_up(con); con.commit()
        now = "2026-06-28T00:00:00"
        con.execute(
            "INSERT INTO pcc_patient (patient_id,id,facility_id,fetched_at,raw_payload)"
            " VALUES ('FA-001',1,101,?, '{}')", (now,))
        con.execute(
            "INSERT INTO pcc_coverage (id,patient_id,payer_code,effective_to,fetched_at,raw_payload)"
            " VALUES (1,'FA-001','MCB',NULL,?,'{}')", (now,))
        con.execute(
            "INSERT INTO wound_extraction (patient_id,source_kind,is_primary,extraction_method,"
            "wound_type,stage,location,length_cm,width_cm,depth_cm,drainage,overall_conf,extracted_at,wound_key)"
            " VALUES ('FA-001','note',1,'regex_envive','pressure_ulcer','3','Right hip',2.9,2.8,0.4,'heavy',0.9,?,"
            "'pressure_ulcer|right_hip')",
            (now,))
        con.commit()

        s = load_settings()
        res1 = summarize.backfill(con, s, use_llm=False)  # deterministic, no network
        assert res1["generated"] == 1 and res1["template"] == 1

        row = con.execute(
            "SELECT ai_summary, ai_summary_model FROM wound_summary WHERE patient_id='FA-001'"
        ).fetchone()
        assert row["ai_summary"] and len(row["ai_summary"]) > 20
        assert row["ai_summary_model"] == summarize.TEMPLATE_VERSION

        # second pass: nothing missing -> no-op
        res2 = summarize.backfill(con, s, use_llm=False)
        assert res2["generated"] == 0

        # force re-summarizes the (single) wound
        res3 = summarize.backfill(con, s, use_llm=False, force=True)
        assert res3["generated"] == 1
        con.close()
    finally:
        for ext in ("", "-wal", "-shm"):
            try: os.remove(path + ext)
            except OSError: pass
