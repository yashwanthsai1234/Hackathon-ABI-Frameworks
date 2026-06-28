"""S5 — routing. The SQL view `v_patient_eligibility` is the EXECUTION path
(deterministic, computed live when export reads it). This module provides the
read-side distribution + a Python `classify()` ORACLE used by tests to assert
the SQL view agrees with the spec policy (SPEC R5).
"""
from __future__ import annotations

import sqlite3


def route_distribution(con: sqlite3.Connection) -> dict[str, int]:
    rows = con.execute(
        "SELECT route, COUNT(*) n FROM v_patient_eligibility GROUP BY route"
    ).fetchall()
    dist = {"auto_accept": 0, "flag_for_review": 0, "reject": 0}
    for route, n in rows:
        dist[route] = n
    return dist


def classify(*, has_active_mcb: bool, has_active_wound: bool, wound_type,
             length_cm, width_cm, depth_cm, drainage, overall_conf,
             all_agree: bool, n_sources: int, has_wound_dx: bool,
             failed_fetches: int = 0, tau: float = 0.80) -> str:
    """Python oracle mirroring v_patient_eligibility's CASE (SPEC §3).

    ``failed_fetches`` > 0 means one or more of the patient's source fan-out
    fetches failed (e.g. sustained 429): the chart is INCOMPLETE, so the patient
    can never auto_accept and routes to review with a distinct reason — we never
    bill on data we could not fully fetch (flag, don't hallucinate)."""
    if not has_active_mcb:
        return "reject"
    if not has_active_wound and wound_type is None:
        return "reject"
    if wound_type is None:
        return "reject"
    if failed_fetches > 0:
        return "flag_for_review"
    complete = all(v is not None for v in (length_cm, width_cm, depth_cm, drainage))
    if (complete and (overall_conf or 0) >= tau and all_agree and n_sources >= 2 and has_wound_dx):
        return "auto_accept"
    return "flag_for_review"
