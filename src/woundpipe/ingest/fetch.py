"""Per-task fetch + idempotent landing + bounded-concurrency executor
(SPEC §Ingestion §1/§2).

Each open fetch_log task is run on its OWN sqlite connection inside a worker
thread (sqlite Connection objects are not safe to share across threads; WAL
mode serializes the writes). A `Semaphore(max_concurrency)` is held DURING the
fetch — including its retry sleeps — so the in-flight + sleeping count can never
exceed the bound: that is the anti-retry-storm invariant (SPEC §2). DB writes
happen AFTER the permit is released.

Fail-closed call-site bookkeeping (SPEC §1):
  * 200 + unchanged hash -> mark_done, skip raw write (content short-circuit)
  * 200 + new content    -> idempotent UPSERT (monotonic guard) + mark_done
  * FatalHTTP (4xx)      -> mark_failed + continue
  * retry budget exhausted -> mark_failed + continue
One bad task NEVER aborts the run.
"""
from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from woundpipe.errors import FatalHTTP, RetryableHTTP
from woundpipe.ingest import checkpoint as cp
from woundpipe.ingest.client import fetch_one

# endpoint -> (table, pk, monotonic-guard column, source_endpoint path).
# notes/assessments carry no last_modified_at, so they guard on fetched_at
# (always advances -> latest wins); the content-hash short-circuit upstream
# keeps re-runs from rewriting unchanged payloads (SPEC data-layer §4).
_TABLES = {
    "patients": ("pcc_patient", "patient_id", "last_modified_at", "/pcc/patients"),
    "diagnoses": ("pcc_diagnosis", "id", "last_modified_at", "/pcc/diagnoses"),
    "coverage": ("pcc_coverage", "id", "last_modified_at", "/pcc/coverage"),
    "notes": ("progress_note", "id", "fetched_at", "/pcc/notes"),
    "assessments": ("pcc_assessment", "id", "fetched_at", "/pcc/assessments"),
}


def _default_connect(db_path: str):
    # Imported lazily: the db engineer owns db/engine.py (written in parallel).
    from woundpipe.db.engine import connect

    return connect(db_path)


# ----------------------------------------------------------------- landing
def _coerce(v):
    """SQLite has no bool type; store booleans as 0/1."""
    if isinstance(v, bool):
        return 1 if v else 0
    return v


def _upsert(con, table: str, pk: str, guard_col: str, path: str, rows: list) -> int:
    """Idempotent UPSERT with a monotonic guard (SPEC data-layer §4).

    Insert verbatim; on PK conflict update only when the incoming row is not
    older than the stored one, bumping sync_version and re-marking is_current.
    Provenance (fetched_at/source_endpoint/raw_payload) is always set by us; the
    API's own sync_version/is_current are dropped so they cannot clobber ours."""
    cols = cp.writable_columns(con, table)
    now = cp.now_iso()
    n = 0
    for rec in rows:
        if not isinstance(rec, dict):
            continue
        data = {k: _coerce(v) for k, v in rec.items()}
        data.pop("sync_version", None)
        data.pop("is_current", None)
        data["fetched_at"] = now
        data["source_endpoint"] = path
        data["raw_payload"] = json.dumps(rec, sort_keys=True, default=str)
        data["is_current"] = 1
        payload = {k: v for k, v in data.items() if k in cols}
        if pk not in payload:
            continue  # malformed record without a primary key — skip, fail-closed
        names = ", ".join(payload)
        qs = ", ".join("?" for _ in payload)
        update_cols = [c for c in payload if c != pk]
        sets = ", ".join(f"{c}=excluded.{c}" for c in update_cols)
        if "sync_version" in cols:
            sets += f"{', ' if sets else ''}sync_version={table}.sync_version+1"
        if "is_current" in cols:
            sets += f"{', ' if sets else ''}is_current=1"
        guard = (
            f" WHERE excluded.{guard_col} >= {table}.{guard_col}"
            if guard_col in cols
            else ""
        )
        sql = f"INSERT INTO {table} ({names}) VALUES ({qs})"
        if sets:
            sql += f" ON CONFLICT({pk}) DO UPDATE SET {sets}{guard}"
        else:  # pragma: no cover - pk-only table
            sql += f" ON CONFLICT({pk}) DO NOTHING"
        con.execute(sql, tuple(payload.values()))
        n += 1
    con.commit()
    return n


def land(con, endpoint: str, rows: list, run_id: str) -> int:
    table, pk, guard_col, path = _TABLES[endpoint]
    return _upsert(con, table, pk, guard_col, path, rows)


# Named per-endpoint landers (brief contract).
def land_patients(con, rows, run_id):
    return land(con, "patients", rows, run_id)


def land_diagnoses(con, rows, run_id):
    return land(con, "diagnoses", rows, run_id)


def land_coverage(con, rows, run_id):
    return land(con, "coverage", rows, run_id)


def land_notes(con, rows, run_id):
    return land(con, "notes", rows, run_id)


def land_assessments(con, rows, run_id):
    return land(con, "assessments", rows, run_id)


# ----------------------------------------------------------------- one task
def _run_task(settings, manifest, db_path, task_row, since, sem, connect_fn) -> None:
    """Fetch one task; land + checkpoint. Swallows per-task failure (fail-closed,
    run continues). Runs on its own connection in a worker thread."""
    endpoint = task_row["endpoint"]
    ik = task_row.get("identity_kind")
    iv = task_row.get("identity_value")
    tid = task_row["task_id"]
    run_id = manifest.run_id
    params = cp.params_for(endpoint, ik, iv, since)
    con = connect_fn(db_path)
    try:
        with sem:  # held across retries: caps in-flight + sleeping (anti-storm)
            data = fetch_one(settings, endpoint, params, manifest=manifest)
        rows = data if isinstance(data, list) else [data]
        canonical = json.dumps(data, sort_keys=True, default=str)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        if cp.content_unchanged(con, tid, digest):
            cp.mark_done(con, tid, 200, len(rows), digest, run_id)
            manifest.bump("tasks_done")
            return

        land(con, endpoint, rows, run_id)
        cp.mark_done(con, tid, 200, len(rows), digest, run_id)
        manifest.bump("tasks_done")
    except FatalHTTP as exc:
        cp.mark_failed(con, tid, exc.status, f"fatal {exc.status}", run_id)
        manifest.bump("tasks_failed")
    except RetryableHTTP as exc:  # reraised after the retry budget was exhausted
        cp.mark_failed(con, tid, getattr(exc, "status", 0), "retry_exhausted", run_id)
        manifest.bump("tasks_failed")
    except Exception as exc:  # noqa: BLE001 - any other error fails the task, not the run
        cp.mark_failed(con, tid, None, repr(exc)[:500], run_id)
        manifest.bump("tasks_failed")
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass


# ----------------------------------------------------------------- executor
def execute(
    settings,
    manifest,
    *,
    db_path: str | None = None,
    since: str | None = None,
    max_concurrency: int | None = None,
    connect_fn=None,
) -> dict:
    """Run every open fetch_log task via a bounded thread pool.

    Resume-safe: only ``status != 'done'`` rows are run; already-done tasks are
    counted as resumed. Returns a small summary dict."""
    connect_fn = connect_fn or _default_connect
    db_path = db_path or settings.db_path
    n = int(max_concurrency or getattr(settings, "max_concurrency", 8))

    con = connect_fn(db_path)
    try:
        open_rows = cp.select_open(con)
        done_before = cp.count_done(con)
    finally:
        con.close()

    manifest.set("tasks_resumed", done_before)
    if not open_rows:
        return {"open": 0, "resumed": done_before}

    sem = threading.Semaphore(n)
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [
            pool.submit(
                _run_task, settings, manifest, db_path, row, since, sem, connect_fn
            )
            for row in open_rows
        ]
        for fut in as_completed(futures):
            fut.result()  # _run_task never raises; surfaces only catastrophic bugs

    return {"open": len(open_rows), "resumed": done_before}
