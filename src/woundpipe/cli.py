"""woundpipe CLI — the pipeline orchestrator (Typer).

Commands: init-db · migrate · ingest · extract · route · publish · run-all
Every stage is independently re-runnable and resumable.
"""
from __future__ import annotations

import uuid

import typer

from woundpipe.config import load_settings

app = typer.Typer(add_completion=False, help="Wound-care billing-triage pipeline (woundpipe).")


def _settings(db):
    s = load_settings()
    if db:
        s.db_path = db
    return s


def _migrate(db: str, direction: str = "up", target: int | None = None):
    from woundpipe.db import migrate
    from woundpipe.db.engine import connect
    con = connect(db)
    try:
        applied = (migrate.migrate_up(con, target) if direction == "up"
                   else migrate.migrate_down(con, target or 0))
        con.commit()
        return applied
    finally:
        con.close()


@app.command(name="init-db")
def init_db(db: str = typer.Option("data/woundpipe.db", "--db")):
    """Create the SQLite DB and migrate to the latest schema."""
    applied = _migrate(db, "up")
    typer.echo(f"init-db: {db} migrated (applied {applied or 'none — already current'})")


@app.command(name="migrate")
def migrate_cmd(db: str = typer.Option("data/woundpipe.db", "--db"),
                direction: str = typer.Option("up", "--direction"),
                target: int = typer.Option(None, "--target")):
    """Apply migrations up/down."""
    applied = _migrate(db, direction, target)
    typer.echo(f"migrate {direction}: {applied}")


@app.command()
def ingest(db: str = typer.Option("data/woundpipe.db", "--db"),
           facilities: str = typer.Option("101,102,103", "--facilities"),
           since: str = typer.Option(None, "--since"),
           limit: int = typer.Option(0, "--limit", help="cap patients (debug/fast demo)")):
    """Fetch patients + per-patient records from the PCC API (resilient, resumable)."""
    from woundpipe.db.engine import connect
    from woundpipe.ingest import checkpoint, fetch
    from woundpipe.observability.manifest import RunManifest
    from woundpipe.resolve import identity

    s = _settings(db)
    run_id = uuid.uuid4().hex[:12]
    manifest = RunManifest(run_id=run_id)
    facs = [int(x) for x in facilities.split(",") if x.strip()]

    con = connect(db)
    checkpoint.seed(con, checkpoint.plan_tasks(facs), run_id)
    con.commit(); con.close()

    typer.echo(f"[ingest] phase 1: fetching patient lists for {facs} …")
    fetch.execute(s, manifest, db_path=db, since=since)

    con = connect(db)
    if not identity.resolve_gate(con):
        con.close()
        typer.secho("[ingest] identity gate FAILED — patient lists incomplete", fg="red")
        raise typer.Exit(1)
    fanout = identity.plan_fanout(con)
    if limit:
        # keep only the first `limit` patients' fan-out tasks (debug)
        keep = set()
        seen = []
        for t in fanout:
            pid = getattr(t, "identity_value", None)
            if pid not in keep and len(keep) < limit:
                keep.add(pid)
            if pid in keep:
                seen.append(t)
        fanout = seen
    checkpoint.seed(con, fanout, run_id)
    con.commit(); con.close()

    typer.echo(f"[ingest] phase 2: fanning out {len(fanout)} per-patient fetches …")
    fetch.execute(s, manifest, db_path=db, since=since)

    con = connect(db)
    done = checkpoint.count_done(con)
    failed = checkpoint.count_failed(con)
    incomplete_patients = checkpoint.failed_fanout_patients(con)
    manifest.persist(con, s.runs_dir)
    con.commit(); con.close()
    typer.secho(
        f"[ingest] done · run={run_id} · tasks_done={done} · calls={manifest.calls_total} "
        f"· 429s={manifest.calls_429} · retries={manifest.retries}", fg="green")
    if failed:
        typer.secho(
            f"[ingest] WARNING: {failed} fetch(es) failed across {incomplete_patients} patient(s) — "
            f"their charts are INCOMPLETE and will be flagged, never auto-accepted. "
            f"Run `woundpipe requeue-failed --db {db}` to retry.", fg="yellow")


@app.command(name="requeue-failed")
def requeue_failed(db: str = typer.Option("data/woundpipe.db", "--db")):
    """Re-drive every failed fetch task (dead-letter drain), then re-fetch them."""
    from woundpipe.db.engine import connect
    from woundpipe.ingest import checkpoint, fetch
    from woundpipe.observability.manifest import RunManifest

    s = _settings(db)
    con = connect(db)
    n = checkpoint.requeue_failed(con)
    con.commit(); con.close()
    if not n:
        typer.secho("[requeue-failed] no failed tasks — nothing to do.", fg="green")
        return
    typer.echo(f"[requeue-failed] requeued {n} failed task(s); re-fetching …")
    manifest = RunManifest(run_id=uuid.uuid4().hex[:12])
    fetch.execute(s, manifest, db_path=db)
    con = connect(db)
    still = checkpoint.count_failed(con)
    con.close()
    color = "green" if not still else "yellow"
    typer.secho(f"[requeue-failed] done · remaining_failed={still}", fg=color)


@app.command()
def extract(db: str = typer.Option("data/woundpipe.db", "--db"),
            full: bool = typer.Option(False, "--full",
                                      help="re-extract every patient, ignoring the change watermark")):
    """Extract wound fields from notes/assessments into wound_extraction.

    Incremental by default: only patients whose source data changed since the
    last run are re-extracted. Use --full to force a complete rebuild."""
    from woundpipe.db.engine import connect
    from woundpipe.extract import engine
    s = _settings(db)
    con = connect(db)
    res = engine.extract_all(con, s, full=full)
    con.close()
    llm = f" llm={res['llm']}" if res.get("llm") else ""
    typer.secho(f"[extract] dirty={res['dirty']} skipped={res['skipped']} "
                f"patients_with_wounds={res['patients_with_wounds']} "
                f"by_format={res['by_format']}{llm}", fg="green")


@app.command()
def route(db: str = typer.Option("data/woundpipe.db", "--db")):
    """Show the routing distribution (computed live by v_patient_eligibility)."""
    from woundpipe.db.engine import connect
    from woundpipe.route.eligibility import route_distribution
    con = connect(db)
    dist = route_distribution(con)
    con.close()
    typer.secho(f"[route] {dist}", fg="green")


@app.command()
def publish(db: str = typer.Option("data/woundpipe.db", "--db"),
            out: str = typer.Option("data/export.json", "--out"),
            frontend: str = typer.Option("frontend/public/export.json", "--frontend")):
    """Build the static export.json the frontend reads."""
    from woundpipe.db.engine import connect
    from woundpipe.publish import export
    con = connect(db)
    res = export.publish(con, out, frontend_public=frontend)
    con.close()
    typer.secho(f"[publish] wrote {res['export_path']} ({res['patients']} patients) + frontend", fg="green")


@app.command()
def run_all(db: str = typer.Option("data/woundpipe.db", "--db"),
            facilities: str = typer.Option("101,102,103", "--facilities"),
            since: str = typer.Option(None, "--since"),
            limit: int = typer.Option(0, "--limit")):
    """init-db → ingest → extract → route → publish."""
    init_db(db=db)
    ingest(db=db, facilities=facilities, since=since, limit=limit)
    extract(db=db)
    route(db=db)
    publish(db=db, out="data/export.json", frontend="frontend/public/export.json")
    typer.secho("[run-all] pipeline complete.", fg="green", bold=True)


if __name__ == "__main__":
    app()
