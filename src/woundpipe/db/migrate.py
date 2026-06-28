"""Zero-dependency migration runner driven by ``PRAGMA user_version``.

Convention (SPEC §5, spec-data):
- ``migrations/NNN_name.up.sql`` / ``migrations/NNN_name.down.sql``.
- ``NNN`` is the ``user_version`` the *up* migration advances the DB **to**
  (and the *down* migration rolls back **from**, leaving ``NNN-1``).
- Each migration runs in **one transaction**; the ``user_version`` stamp is part
  of that transaction, so a failed migration leaves the version untouched
  (atomic, rollback-safe).

Foreign-key enforcement is toggled **off** for the duration of DDL (it cannot be
changed inside a transaction, and schema rebuilds/drops are not DML); the
down-scripts still drop objects in FK-safe order as belt-and-suspenders.

CLI::

    python -m woundpipe.db.migrate <db_path> up            # -> latest
    python -m woundpipe.db.migrate <db_path> up   <target> # -> target version
    python -m woundpipe.db.migrate <db_path> down <target> # -> target (default 0)
"""
from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from woundpipe.db.engine import connect

__all__ = ["migrate_up", "migrate_down", "current_version", "discover", "main"]


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    up: Path
    down: Path


def migrations_dir() -> Path:
    """Locate the repo-root ``migrations/`` directory.

    ``src/woundpipe/db/migrate.py`` -> parents[3] is the repo root.
    """
    return Path(__file__).resolve().parents[3] / "migrations"


def discover(directory: Path | None = None) -> list[Migration]:
    """Return all migrations sorted ascending by version."""
    directory = directory or migrations_dir()
    out: dict[int, dict[str, Path]] = {}
    names: dict[int, str] = {}
    for p in directory.glob("*.sql"):
        # e.g. 001_initial_schema.up.sql  ->  stem '001_initial_schema.up'
        stem = p.name
        if stem.endswith(".up.sql"):
            kind, core = "up", stem[: -len(".up.sql")]
        elif stem.endswith(".down.sql"):
            kind, core = "down", stem[: -len(".down.sql")]
        else:
            continue
        num_str, _, name = core.partition("_")
        version = int(num_str)
        out.setdefault(version, {})[kind] = p
        names[version] = name
    migrations: list[Migration] = []
    for version in sorted(out):
        pair = out[version]
        if "up" not in pair or "down" not in pair:
            raise FileNotFoundError(
                f"migration {version:03d} missing an up or down file: {pair}"
            )
        migrations.append(Migration(version, names[version], pair["up"], pair["down"]))
    return migrations


def current_version(con: sqlite3.Connection) -> int:
    return int(con.execute("PRAGMA user_version").fetchone()[0])


def _apply(con: sqlite3.Connection, sql: str, new_version: int) -> None:
    """Run one migration body atomically and stamp ``user_version``.

    ``executescript`` always runs in autocommit, so the transaction is expressed
    *inside* the script text (BEGIN ... COMMIT). The ``user_version`` write is
    part of that transaction and rolls back with it on error.
    """
    script = f"BEGIN;\n{sql}\nPRAGMA user_version = {int(new_version)};\nCOMMIT;"
    try:
        con.executescript(script)
    except Exception:
        try:
            con.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise


def migrate_up(
    con: sqlite3.Connection, target: int | None = None, directory: Path | None = None
) -> list[int]:
    """Apply pending up-migrations up to ``target`` (default: latest)."""
    applied: list[int] = []
    con.execute("PRAGMA foreign_keys = OFF")
    try:
        cur = current_version(con)
        for m in discover(directory):
            if m.version <= cur:
                continue
            if target is not None and m.version > target:
                break
            _apply(con, m.up.read_text(), m.version)
            applied.append(m.version)
    finally:
        con.execute("PRAGMA foreign_keys = ON")
    return applied


def migrate_down(
    con: sqlite3.Connection, target: int = 0, directory: Path | None = None
) -> list[int]:
    """Roll back down-migrations until ``user_version == target``."""
    rolled: list[int] = []
    con.execute("PRAGMA foreign_keys = OFF")
    try:
        cur = current_version(con)
        for m in reversed(discover(directory)):
            if m.version > cur or m.version <= target:
                continue
            _apply(con, m.down.read_text(), m.version - 1)
            rolled.append(m.version)
    finally:
        con.execute("PRAGMA foreign_keys = ON")
    return rolled


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[1] not in ("up", "down"):
        print(
            "usage: python -m woundpipe.db.migrate <db_path> up|down [target]",
            file=sys.stderr,
        )
        return 2
    db_path, direction = argv[0], argv[1]
    target = int(argv[2]) if len(argv) > 2 else None
    con = connect(db_path)
    try:
        before = current_version(con)
        if direction == "up":
            changed = migrate_up(con, target)
        else:
            changed = migrate_down(con, target if target is not None else 0)
        after = current_version(con)
        verb = "applied" if direction == "up" else "rolled back"
        print(
            f"{direction}: user_version {before} -> {after}  "
            f"({verb}: {changed or 'none'})"
        )
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
