"""Stage 1 — two-identity resolution HARD GATE (SPEC §Ingestion §4).

The PCC API splits a patient across two identities (api-reality "join trap"):
  * ``patient_id`` (string, ``FA-001``) -> /diagnoses, /coverage
  * ``id``         (integer, ``1``)      -> /notes, /assessments
``/patients`` returns both; we land them into ``pcc_patient`` and then fan out
with the CORRECT key per endpoint. Because the planner reads the key column
straight from the identity table, a wrong-key 422 is *structurally impossible*.

The gate is fail-closed: ``resolve_gate`` returns True only when every facility
list task is ``done`` AND at least one identity row exists. Callers MUST NOT plan
the fan-out or advance to later stages until it passes.
"""
from __future__ import annotations

from woundpipe.ingest.checkpoint import Task
from woundpipe.models import PatientRef


def resolve_gate(con) -> bool:
    """True iff all facility patient-list tasks are done AND identity rows > 0.

    Any ambiguity (an open list task, no list ever ran, or zero patients) ->
    False, so the pipeline refuses to fan out (SPEC §4 step 3, fail-closed)."""
    open_lists = con.execute(
        "SELECT COUNT(*) FROM fetch_log WHERE endpoint='patients' AND status != 'done'"
    ).fetchone()[0]
    done_lists = con.execute(
        "SELECT COUNT(*) FROM fetch_log WHERE endpoint='patients' AND status='done'"
    ).fetchone()[0]
    n_patients = con.execute("SELECT COUNT(*) FROM pcc_patient").fetchone()[0]
    return open_lists == 0 and done_lists > 0 and n_patients > 0


def build_map(con) -> dict[str, int]:
    """patient_id (FA-001) -> id (1). The canonical resolution map."""
    rows = con.execute(
        "SELECT patient_id, id FROM pcc_patient WHERE id IS NOT NULL"
    ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows}


def patient_refs(con) -> list[PatientRef]:
    """Typed identity rows for downstream stages."""
    rows = con.execute(
        "SELECT patient_id, id, facility_id, primary_payer_code, last_modified_at "
        "FROM pcc_patient"
    ).fetchall()
    return [
        PatientRef(
            patient_id=str(r[0]),
            id=int(r[1]),
            facility_id=int(r[2]),
            primary_payer_code=r[3],
            last_modified_at=r[4],
        )
        for r in rows
    ]


def plan_fanout(con) -> list[Task]:
    """Emit the four per-patient tasks with the CORRECT key per endpoint:
    patient_id -> diagnoses/coverage, id -> notes/assessments (SPEC §4 step 4).

    Caller is responsible for having passed ``resolve_gate`` first."""
    if not resolve_gate(con):
        raise RuntimeError("resolve_gate not satisfied; refusing to plan fan-out")

    tasks: list[Task] = []
    for ref in patient_refs(con):
        pid = ref.patient_id
        iid = str(ref.id)
        tasks.append(Task("diagnoses", "patient_id", pid))
        tasks.append(Task("coverage", "patient_id", pid))
        tasks.append(Task("notes", "id", iid))
        tasks.append(Task("assessments", "id", iid))
    return tasks
