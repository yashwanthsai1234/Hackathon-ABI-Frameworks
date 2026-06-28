"""SQLite connection factory — the one place connection PRAGMAs are set.

Every consumer in woundpipe opens its connection through :func:`connect` so the
durability/concurrency posture is identical everywhere:

- ``journal_mode=WAL``      — readers never block the single writer (the
  ~1,203-call sync writes while the dashboard reads).
- ``foreign_keys=ON``       — referential integrity enforced in the engine, not
  in app code (an app-only invariant is a future bad row).
- ``busy_timeout=5000``     — wait up to 5s for the WAL writer instead of
  raising ``SQLITE_BUSY`` immediately.
- ``synchronous=NORMAL``    — the WAL-recommended durability/throughput balance
  (safe against application crashes; only a power loss mid-checkpoint risks the
  last transaction, acceptable for a derived analytics store with a snapshot).

SPEC §0 (spec-data) / research-schema "Connection PRAGMAs".
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

__all__ = ["connect"]


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open ``db_path`` with the canonical woundpipe PRAGMAs and ``Row`` factory.

    ``row_factory`` is :class:`sqlite3.Row` so every query yields mapping-style
    rows; the repository layer converts these to plain dicts (the frontend
    contract atoms).
    """
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    # PRAGMAs must run outside any transaction. journal_mode/foreign_keys are
    # connection- (foreign_keys) or database-level (journal_mode) and stick.
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 5000")
    con.execute("PRAGMA synchronous = NORMAL")
    return con
