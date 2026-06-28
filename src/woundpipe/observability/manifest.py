"""RunManifest — thread-safe ingestion telemetry (SPEC §6, SPEC.md §C.3).

The manifest is the single source of operational truth for a run: every HTTP
send, every 429/500/422, every retry and task outcome is counted here under one
lock so the totals are exact under the ThreadPoolExecutor (SPEC §2 invariant:
"Manifest counters behind one threading.Lock -> exact under concurrency").

Persistence is twofold (SPEC §6): a `run_manifest` DB row (best-effort, column
filtered so we never break on the DB engineer's evolving DDL) AND a
`data/runs/<run_id>.json` file whose shape matches the frontend contract.
No PII or secrets ever enter the manifest — only ids, counts and timings.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

# integer counters that `bump`/`set` operate on
_COUNTERS = (
    "calls_total",
    "calls_429",
    "calls_500",
    "calls_422",
    "retries",
    "tasks_done",
    "tasks_failed",
    "tasks_resumed",
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunManifest:
    run_id: str
    started_at: str = field(default_factory=_utcnow)
    finished_at: str | None = None
    wall_ms: int = 0

    # HTTP / task counters (SPEC §6)
    calls_total: int = 0
    calls_429: int = 0
    calls_500: int = 0
    calls_422: int = 0
    retries: int = 0
    tasks_done: int = 0
    tasks_failed: int = 0
    tasks_resumed: int = 0

    # categorical roll-ups (frontend funnel / Sankey)
    by_format: dict[str, int] = field(default_factory=dict)
    by_route: dict[str, int] = field(default_factory=dict)
    per_stage_ms: dict[str, int] = field(default_factory=dict)

    # not part of the public dict; internal wall-clock anchor
    _t0: float = field(default_factory=time.monotonic, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ------------------------------------------------------------------ writes
    def bump(self, name: str, n: int = 1) -> None:
        """Atomically increment one of the integer counters."""
        if name not in _COUNTERS:
            raise KeyError(f"unknown counter {name!r}")
        with self._lock:
            setattr(self, name, getattr(self, name) + n)

    def set(self, name: str, value: int) -> None:
        """Atomically set a counter to an absolute value (e.g. resumed count)."""
        if name not in _COUNTERS:
            raise KeyError(f"unknown counter {name!r}")
        with self._lock:
            setattr(self, name, value)

    def add_format(self, fmt: str, n: int = 1) -> None:
        with self._lock:
            self.by_format[fmt] = self.by_format.get(fmt, 0) + n

    def add_route(self, route: str, n: int = 1) -> None:
        with self._lock:
            self.by_route[route] = self.by_route.get(route, 0) + n

    def stage_ms(self, stage: str, ms: int) -> None:
        with self._lock:
            self.per_stage_ms[stage] = self.per_stage_ms.get(stage, 0) + int(ms)

    def finalize(self) -> None:
        with self._lock:
            self.finished_at = _utcnow()
            self.wall_ms = int((time.monotonic() - self._t0) * 1000)

    # ------------------------------------------------------------------ reads
    def to_dict(self) -> dict:
        with self._lock:
            return {
                "run_id": self.run_id,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "wall_ms": self.wall_ms,
                "calls_total": self.calls_total,
                "calls_429": self.calls_429,
                "calls_500": self.calls_500,
                "calls_422": self.calls_422,
                "retries": self.retries,
                "tasks_done": self.tasks_done,
                "tasks_failed": self.tasks_failed,
                "tasks_resumed": self.tasks_resumed,
                "by_format": dict(self.by_format),
                "by_route": dict(self.by_route),
                "per_stage_ms": dict(self.per_stage_ms),
            }

    # --------------------------------------------------------------- persist
    def persist(self, con=None, runs_dir: str = "data/runs") -> str:
        """Write the JSON sidecar always; upsert the run_manifest row if a
        connection is supplied. Returns the JSON file path.

        DB persistence is best-effort and column-filtered: the manifest must
        never abort a run because the DB schema lacks a column.
        """
        d = self.to_dict()
        os.makedirs(runs_dir, exist_ok=True)
        path = os.path.join(runs_dir, f"{self.run_id}.json")
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)  # atomic file swap

        if con is not None:
            self._persist_row(con, d)
        return path

    def _persist_row(self, con, d: dict) -> None:
        # Map manifest -> run_manifest column names (SPEC §1 data-layer DDL).
        candidate = {
            "run_id": d["run_id"],
            "started_at": d["started_at"],
            "finished_at": d["finished_at"],
            "n_calls": d["calls_total"],
            "n_429": d["calls_429"],
            "n_retries": d["retries"],
            "n_patients": d["tasks_done"],
            "n_extracted": 0,
            "counts_json": json.dumps(d, sort_keys=True),
        }
        try:
            cols = {
                r[1]
                for r in con.execute("PRAGMA table_xinfo(run_manifest)").fetchall()
                if r[6] == 0  # hidden==0 -> ordinary writable column
            }
        except Exception:
            return  # table absent / DB not ready: sidecar JSON still written
        payload = {k: v for k, v in candidate.items() if k in cols}
        if "run_id" not in payload:
            return
        names = ", ".join(payload)
        qs = ", ".join("?" for _ in payload)
        updates = ", ".join(f"{k}=excluded.{k}" for k in payload if k != "run_id")
        sql = f"INSERT INTO run_manifest ({names}) VALUES ({qs})"
        if updates:
            sql += f" ON CONFLICT(run_id) DO UPDATE SET {updates}"
        try:
            con.execute(sql, tuple(payload.values()))
            con.commit()
        except Exception:
            pass  # fail-open on telemetry; never abort the run for a manifest write
