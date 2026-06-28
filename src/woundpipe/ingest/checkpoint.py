"""fetch_log checkpoint / resume helpers (SPEC §Ingestion §3).

`fetch_log` is the durable work-ledger that makes ingestion resumable and
idempotent. The deterministic primary key `task_id = '<endpoint>:<identity_value>'`
(e.g. ``diagnoses:FA-001``, ``notes:1``) is the stable identity of a unit of work,
so seeding is UPSERT-safe (no duplicates) and a killed run resumes by simply
selecting everything that is not yet ``done``.

Resume algorithm (SPEC §3):
  1. plan the full task set (facility lists here; per-patient fan-out in
     ``resolve/identity.py``),
  2. seed ``pending`` via INSERT … ON CONFLICT DO NOTHING (done rows untouched),
  3. select ``status != 'done'`` (uses the partial index),
  4. execute,
  5. content-hash short-circuit: unchanged payload -> skip raw write, still mark done,
  6. done-gate = NOT EXISTS WHERE status != 'done'.

All writes are column-filtered against the live schema so the module never
breaks on the DB engineer's additive DDL changes. All SQL is parameterized;
the only interpolated tokens are column names read from the catalog.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# endpoint -> the API query param that carries a `since` cursor (SPEC §7).
# diagnoses & coverage have NO since (patient-delta driven), so they are absent.
SINCE_PARAM = {"patients", "notes", "assessments"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Task:
    """One unit of fetch work. `identity_kind` mirrors the fetch_log CHECK
    ('facility' | 'patient_id' | 'id') and drives param reconstruction."""

    endpoint: str
    identity_kind: str
    identity_value: str

    @property
    def task_id(self) -> str:
        return f"{self.endpoint}:{self.identity_value}"


# ----------------------------------------------------------------- catalog
def writable_columns(con, table: str) -> set[str]:
    """Ordinary writable columns of `table` (excludes GENERATED/virtual cols).

    PRAGMA table_xinfo's `hidden` flag: 0 = normal, 1 = hidden, 2 = virtual
    generated, 3 = stored generated. We keep only 0 so we never try to write a
    generated column (e.g. pcc_assessment.a_wound_type)."""
    rows = con.execute(f"PRAGMA table_xinfo({table})").fetchall()
    return {r[1] for r in rows if r[6] == 0}


def _filter(d: dict, cols: set[str]) -> dict:
    return {k: v for k, v in d.items() if k in cols}


# ----------------------------------------------------------------- planning
def plan_tasks(facilities) -> list[Task]:
    """Cold plan for the patient-list phase: one task per facility (SPEC §3).
    Per-patient fan-out is planned later by resolve.identity.plan_fanout once
    the identity gate passes."""
    return [Task("patients", "facility", str(int(f))) for f in facilities]


def params_for(endpoint: str, identity_kind: str, identity_value: str, since=None) -> dict:
    """Reconstruct the HTTP params from a fetch_log row. The correct key per
    endpoint is structurally encoded in identity_kind, so a wrong-key 422 is
    impossible (SPEC §4)."""
    if identity_kind == "facility":
        params: dict = {"facility_id": int(identity_value)}
    elif identity_kind == "patient_id":
        params = {"patient_id": identity_value}          # string FA-001 -> dx/coverage
    elif identity_kind == "id":
        params = {"patient_id": int(identity_value)}     # integer id -> notes/assessments
    else:  # pragma: no cover - defensive
        raise ValueError(f"unknown identity_kind {identity_kind!r}")
    if since and endpoint in SINCE_PARAM:
        params["since"] = since
    return params


# ----------------------------------------------------------------- ledger ops
def seed(con, tasks, run_id: str) -> int:
    """Seed `pending` rows; existing rows (incl. done) are left untouched.
    Returns the number of NEW rows inserted."""
    cols = writable_columns(con, "fetch_log")
    inserted = 0
    now = now_iso()
    for t in tasks:
        base = {
            "task_id": t.task_id,
            "endpoint": t.endpoint,
            "identity_kind": t.identity_kind,
            "identity_value": t.identity_value,
            "status": "pending",
            "attempts": 0,
            "retry_count": 0,
            "planned_at": now,
            "run_id": run_id,
        }
        payload = _filter(base, cols)
        names = ", ".join(payload)
        qs = ", ".join("?" for _ in payload)
        cur = con.execute(
            f"INSERT INTO fetch_log ({names}) VALUES ({qs}) "
            f"ON CONFLICT(task_id) DO NOTHING",
            tuple(payload.values()),
        )
        inserted += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    con.commit()
    return inserted


def select_open(con) -> list[dict]:
    """All not-done tasks (resume set). Uses the partial index ix_fetch_pending."""
    cur = con.execute("SELECT * FROM fetch_log WHERE status != 'done'")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def _update(con, task_id: str, fields: dict) -> None:
    cols = writable_columns(con, "fetch_log")
    payload = _filter(fields, cols)
    if not payload:
        return
    sets = ", ".join(f"{k}=?" for k in payload)
    con.execute(
        f"UPDATE fetch_log SET {sets} WHERE task_id=?",
        (*payload.values(), task_id),
    )
    con.commit()


def mark_done(
    con,
    task_id: str,
    http_status: int,
    n_records: int,
    content_hash: str | None,
    run_id: str,
) -> None:
    _update(
        con,
        task_id,
        {
            "status": "done",
            "http_status": http_status,
            "n_records": n_records,
            "content_hash": content_hash,
            "error": None,
            "run_id": run_id,
            "last_attempt_at": now_iso(),
            "completed_at": now_iso(),
        },
    )


def mark_failed(
    con,
    task_id: str,
    http_status: int | None,
    error: str,
    run_id: str,
) -> None:
    """Mark a task failed. Failed rows keep the run not-done so a later ingest
    retries them — one bad task never aborts the run (SPEC §1/§3)."""
    _update(
        con,
        task_id,
        {
            "status": "failed",
            "http_status": http_status,
            "error": (error or "")[:1000],
            "run_id": run_id,
            "last_attempt_at": now_iso(),
        },
    )


def content_unchanged(con, task_id: str, content_hash: str) -> bool:
    """Content-hash short-circuit: True if the stored hash equals the new one,
    so the raw write can be skipped while the task is still marked done."""
    if "content_hash" not in writable_columns(con, "fetch_log"):
        return False
    row = con.execute(
        "SELECT content_hash FROM fetch_log WHERE task_id=?", (task_id,)
    ).fetchone()
    return bool(row and row[0] is not None and row[0] == content_hash)


def all_done(con) -> bool:
    """Done-gate: no open tasks remain (SPEC §3 step 6)."""
    row = con.execute(
        "SELECT 1 FROM fetch_log WHERE status != 'done' LIMIT 1"
    ).fetchone()
    return row is None


def count_done(con) -> int:
    return con.execute(
        "SELECT COUNT(*) FROM fetch_log WHERE status='done'"
    ).fetchone()[0]


def count_failed(con) -> int:
    return con.execute(
        "SELECT COUNT(*) FROM fetch_log WHERE status='failed'"
    ).fetchone()[0]


def failed_fanout_patients(con) -> int:
    """Distinct patients with at least one FAILED per-patient fan-out task.

    These charts are incomplete: a downstream auto_accept on them would bill on
    data we could not fully fetch, so routing must flag them (Fix 3b)."""
    return con.execute(
        "SELECT COUNT(DISTINCT identity_value) FROM fetch_log "
        "WHERE status='failed' AND identity_kind IN ('patient_id','id')"
    ).fetchone()[0]


def requeue_failed(con) -> int:
    """Reset every failed task back to pending so the resilient executor re-drives
    it (the dead-letter drain). Returns the number of tasks requeued."""
    cur = con.execute(
        "UPDATE fetch_log SET status='pending', error=NULL WHERE status='failed'"
    )
    con.commit()
    return cur.rowcount or 0
