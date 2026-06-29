"""On-demand summary API: generates a wound summary, caches it, 404s unknowns."""
import os
import tempfile

from fastapi.testclient import TestClient

from woundpipe import api
from woundpipe.db import migrate
from woundpipe.db.engine import connect


def test_summary_endpoint_generates_then_caches():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        con = connect(path); migrate.migrate_up(con); con.commit()
        now = "2026-06-28T00:00:00"
        con.execute("INSERT INTO pcc_patient (patient_id,id,facility_id,fetched_at,raw_payload)"
                    " VALUES ('FA-001',1,101,?, '{}')", (now,))
        con.execute("INSERT INTO pcc_coverage (id,patient_id,payer_code,effective_to,fetched_at,raw_payload)"
                    " VALUES (1,'FA-001','MCB',NULL,?,'{}')", (now,))
        con.execute("INSERT INTO wound_extraction (patient_id,source_kind,is_primary,extraction_method,"
                    "wound_type,stage,location,length_cm,width_cm,depth_cm,drainage,overall_conf,extracted_at,wound_key)"
                    " VALUES ('FA-001','note',1,'regex_envive','pressure_ulcer','3','Right hip',2.9,2.8,0.4,'heavy',0.9,?,"
                    "'pressure_ulcer|right_hip')", (now,))
        con.commit(); con.close()

        api.configure(path)
        api._settings.use_llm = False  # deterministic, no network
        client = TestClient(api.app)
        params = {"patient_id": "FA-001", "wound_key": "pressure_ulcer|right_hip"}

        r1 = client.get("/api/summary", params=params)
        assert r1.status_code == 200
        b1 = r1.json()
        assert b1["ai_summary"] and len(b1["ai_summary"]) > 20 and b1["cached"] is False

        r2 = client.get("/api/summary", params=params)  # now cached
        assert r2.json()["cached"] is True
        assert r2.json()["ai_summary"] == b1["ai_summary"]

        r3 = client.get("/api/summary", params={"patient_id": "FA-001", "wound_key": "nope|nope"})
        assert r3.status_code == 404
    finally:
        for ext in ("", "-wal", "-shm"):
            try: os.remove(path + ext)
            except OSError: pass
