# 🛠️ BUILD TRACKER — woundpipe MVP
_Live build status. CEO-owned. Spec: `.agency/artifacts/SPEC.md`._

**Branch:** `build/woundpipe-mvp` · **Started:** 2026-06-28 · **Status:** 🟢 INTEGRATION / RUNNING

## Legend
⬜ todo · 🟡 in progress · ✅ done · 🧪 verified (run/tested) · 🚧 blocked (BLOCKERS.md)

## Phase 0 — Foundation
- 🧪 Repo skeleton + branch + pyproject.toml + .gitignore/.env.example
- 🧪 config.py · errors.py · logging.py · models.py (shared DTO contract) — import-verified

## Phase 1 — Data layer (database-engineer ✅)
- 🧪 db/engine.py (WAL, FK, PRAGMAs)
- 🧪 db/migrate.py (user_version runner) — up→down→up rollback verified
- 🧪 migrations/001 (all tables+views+config+wound_icd_family) + 002 (wound_field_evidence)
- 🧪 repository.py (worklist/patient_detail/corroboration/funnel/note_search/export_json)

## Phase 2 — Ingestion (backend-engineer ✅, 39/39 tests)
- 🧪 ingest/client.py (httpx+tenacity, Retry-After, 422 fail-fast, Semaphore8)
- 🧪 ingest/fetch.py (idempotent UPSERT landers + threaded executor)
- 🧪 ingest/checkpoint.py (fetch_log resume) · resolve/identity.py (HARD GATE)
- 🧪 observability/manifest.py (RunManifest)

## Phase 3 — Extraction (CEO; LLM lane = Claude)
- 🧪 sniff.py · regex_lane.py — VERIFIED on real samples, 0 misses, multi-wound + Stage N/A
- 🧪 reconcile.py (confidence + corroboration) · llm_lane.py (Claude lane, degrades)
- 🧪 engine.py (S4 orchestrator) — runs, writes wound_extraction + per-field evidence

## Phase 4 — Routing & Publish (CEO)
- 🧪 route/eligibility.py (SQL VIEW execution + Python oracle)
- 🧪 publish/export.py (export.json + wires to frontend/public)
- 🧪 cli.py (Typer) — all 7 commands parse (fixed Typer 0.12→0.26 for Python 3.14 / Click 8.4)

## Phase 5 — Frontend (frontend-engineer ✅, builds clean)
- 🧪 Vite+React 19+Tailwind v4+shadcn — white/teal glass, gradients, 48-patient fixture
- 🧪 Command Center · Triage Table · Patient Detail (highlighted note + evidence graph) · Pipeline Flow
- 🧪 All states (loading/error/empty/ready), a11y, design tokens

## Phase 5b — A/B design test (design-explorers ✅)
- 🧪 variant-A "Calm Clinical Glass" + variant-B "Teal Command Deck" + COMPARISON.md (rec: B hero + A table)

## Phase 6 — Tests + Docs + Run  ✅
- 🧪 tests/ — 11 passing incl. R5 SQL-view↔Python-oracle equality + no-fabricated-measurements
- 🧪 README.md (big OSS style, 3 Mermaid diagrams: system, sequence, ERD)
- 🧪 END-TO-END live run: 300 patients, 1693 calls, 485 429s survived, 15 auto / 89 flag / 196 reject
- 🧪 real export.json (586KB, 300 patients) wired → frontend builds clean
- 🧪 verify-restore drill PASSED (R-risk-3 closed): integrity ok, 300 patients, all 3 routes

## Run log (CEO verification)
- ✅ regex lane: 4/4 real archetypes, 0 misses (incl. multi-wound split, Stage N/A)
- ✅ INGEST: 1693 calls / 485 429s / 490 retries / 1200 done / 3 transient-failed in 280s
- ✅ EXTRACT: 299 patients, formats envive 134 / prose 228 / soap 110
- ✅ ROUTE: 15 auto_accept · 89 flag_for_review · 196 reject (FA-001→FLAG "missing depth", the money case)
- ✅ PUBLISH: export.json + manifest (real numbers) + frontend wired
- ✅ verify-restore + 11 tests green

**STATUS: 🟢 COMPLETE — pipeline runs end-to-end against live API; frontend builds; tests + restore pass.**
