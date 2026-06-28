# Database & Backend Architecture — Brainstorm
_Team of 4 · SQLite MVP · 2026-06-28_

---

## 0. What we're building (one sentence)

A 7-stage Python pipeline that pulls ~1,203 API calls from a rate-limited EHR mock (30% chance of 429 per request), lands raw data in SQLite, extracts wound fields from 4 messy note formats, applies a deterministic eligibility routing rule, and exports a clean output table that a biller reads at a glance.

---

## 1. The numbers that drive every decision

| Fact | Impact |
|---|---|
| 300 patients across 3 facilities | Fan-out shape for fetch |
| ~1,203 API calls per full sync | 3 facility patient lists + 300 × 4 per-patient endpoints — need checkpoint/resume |
| 30% 429 rate, random per request | ~360+ expected retries; honor `Retry-After`, bounded concurrency |
| Two patient identities | `patient_id` (string `FA-001`) → diagnoses + coverage; `id` (integer `1`) → notes + assessments. Identity resolution is a hard gate, not a detail. |
| `note_type` field is unreliable | A `Wound (SPN)` note can contain an Envive narrative — must sniff format from the note text itself |
| `raw_json` shape varies by assessment type | `Weekly Wound Information Sheet` = flat fields; `HP Skin & Wound` wraps narrative text in a `sections[].questions[].answer` structure. Parser must handle both. |
| API returns `payer_type: "Medicare"`, not `"Medicare B"` | Key eligibility off `payer_code == 'MCB'` and `effective_to IS NULL`, not the payer_type string |

---

## 2. SQLite database — layers and design decisions

### 2.1 Why these connection settings

- **WAL mode**: readers don't block writers — the dashboard can query while the pipeline writes
- **foreign_keys = ON**: catches bad key references during development
- **busy_timeout = 5000**: thread-pool workers queue instead of crashing on lock contention
- **synchronous = NORMAL**: safe with WAL, meaningfully faster than FULL
- **STRICT tables**: SQLite by default accepts any type for any column; STRICT enforces declared types

### 2.2 Four schema layers

**Layer 1 — Raw / Landing**
Mirrors the API exactly. Every table stores the raw JSON payload as a TEXT column for provenance. Every row carries `fetched_at`, `sync_version`, and `is_current` so we can re-sync and track what changed.

**Layer 2 — Extraction / Derived**
`wound_extraction` — one row per evidence source per patient. A patient might have a note, an assessment, and an active diagnosis all pointing at a wound; these live as separate rows. The `is_primary` flag marks the single wound we route on.

**Layer 3 — Output View**
`v_patient_eligibility` — a live SQL VIEW, not a materialized table. Computes one row per patient with wound fields, eligibility flags, routing decision, and plain-English reason. Sub-millisecond at 300 patients. Only materialize into a real table if routing becomes expensive.

`v_wound_corroboration` — a companion view that shows, per patient, whether each evidence source (diagnosis, note, assessment) agrees with the primary wound on type, location, and stage. Used to draw the evidence-graph visual in the UI.

**Layer 4 — Pipeline Metadata**
`fetch_log`, `runs`, `sync_state` — the operational tables that enable checkpoint/resume, observability metrics, and incremental sync.

---

### 2.3 Table-by-table decisions

**`pcc_patient`**
The join spine. Holds both `patient_id` (TEXT, primary key) and `id` (INTEGER, unique) in the same row. This is the identity resolution artifact — every downstream table can join against whichever key it needs.

**`pcc_diagnosis`**
Stores ICD-10 codes with `clinical_status`. Has a partial index on `(patient_id) WHERE clinical_status = 'active' AND icd10_code LIKE 'L89%'` — the exact predicate the eligibility view hits. Partial indexes are small and the query plan chooses them automatically.

**`pcc_coverage`**
The eligibility key column is `payer_code`, not `payer_type`. Has a partial index on `(patient_id) WHERE payer_code = 'MCB' AND effective_to IS NULL` — the single hottest predicate in the whole schema.

**`progress_note`**
The `note_type` column is stored but explicitly NOT used for format detection. `note_text` is the extraction target. Has a companion FTS5 virtual table (`progress_note_fts`) for full-text search — zero text duplication via the external-content option, kept in sync by INSERT/UPDATE/DELETE triggers.

**`pcc_assessment`**
Stores `raw_json` as TEXT. Has virtual generated columns that call `json_extract()` on the flat JSON shape — these return NULL cleanly when the key doesn't exist (e.g., nested shape), no errors. The nested narrative shape goes through a text extractor instead.

**`wound_extraction`**
Every field is nullable. NULL means "not found," never zero or a default. Has a `measure_span` column that stores the verbatim substring from the note that produced the numeric measurements — this is the LLM hallucination guard (any number not found literally in the source text gets dropped).

**`fetch_log`**
Keyed on `(endpoint, key)`. Status is `pending | done | failed`. Resume logic is just "run all tasks where status != done." A crash at call 700 of 1,203 costs only the remaining 503 on restart.

**`runs`**
One row per pipeline run. Holds raw counts: total calls, 429s, retries, 422s, notes by format, routing distribution, per-stage timing. This is what feeds the dashboard's data-flow animation — these are real numbers, not illustrative.

**`sync_state`**
Per-facility, per-endpoint high-water mark. Passed as the `?since=` parameter on incremental re-runs.

---

### 2.4 Indexing strategy

| Index | Table | Type | Why |
|---|---|---|---|
| `ix_patient_facility` | `pcc_patient` | Standard | Fetch by facility |
| `ix_patient_payer` | `pcc_patient` | Standard | Quick payer filter |
| `ix_dx_active_wound` | `pcc_diagnosis` | **Partial** | Active wound eligibility hot path |
| `ix_dx_patient` | `pcc_diagnosis` | Standard | Per-patient lookup |
| `ix_cov_active_mcb` | `pcc_coverage` | **Partial** | MCB eligibility hot path — single most-hit predicate |
| `ix_note_patient` | `progress_note` | Standard | Per-patient, ordered by date |
| `ix_assess_patient` | `pcc_assessment` | Standard | Per-patient, ordered by date |
| `ix_assess_wtype` | `pcc_assessment` | Standard | Via generated column |
| `ix_wx_patient` | `wound_extraction` | Standard | Primary wound per patient |

---

### 2.5 Idempotent upsert pattern

Every raw table uses `INSERT ... ON CONFLICT DO UPDATE` with a monotonic guard: only update if the incoming `last_modified_at` is newer than what's already stored. Running the same fetch twice produces the same row count — a crashed or re-run sync is safe.

---

### 2.6 Schema migrations

Versioned `migrations/NNN_name.up.sql` and `NNN_name.down.sql` files, tracked by `PRAGMA user_version`. A minimal Python runner applies pending migrations in order, one transaction each. `EXPAND` before `CONTRACT`: add nullable columns first, deploy, then drop old ones in a later migration. Rollback is tested (up → down → up clean).

---

## 3. Backend pipeline — 7 stages

Every stage is a pure-ish function: read SQLite → transform → upsert SQLite. Idempotent by design. Orchestrated by a Typer CLI — one subcommand per stage plus `run` for all stages in order. No workflow engine (Prefect, Dagster) — overkill for 6 single-machine stages.

---

### Stage 0 — Scaffold / Setup
Initialize SQLite, run migrations, configure structlog with a `run_id` bound to every log event.

---

### Stage 1 — Ingest (critical path)

**Goal:** land all 300 patients × 5 endpoint types in SQLite with checkpoint/resume.

**Sequence inside this stage:**
1. Fetch 3 facility patient lists — this is the ONLY thing that runs first
2. Persist the `patient_id ↔ id` mapping — **hard gate, nothing else runs until done**
3. Fan out per-patient fetches using the correct key per endpoint

**Resilience design:**

| Concern | Choice | Rationale |
|---|---|---|
| HTTP client | httpx sync client in ThreadPoolExecutor(8) | IO-bound; threads are simpler than async for a demo; async debugging eats hackathon hours |
| 429 handling | Read `Retry-After` header, sleep exactly that value | The API's 429 is random per-request, not a token bucket — honor the header literally |
| 500 / timeout | Exponential backoff + jitter, max 6 attempts | Jitter prevents 8 workers re-firing in lockstep |
| 422 handling | Fail-fast, log as our bug, skip that patient | Never retry — it's a malformed request on our side |
| Concurrency | Semaphore(8) | Bounded politeness; the number is a single config knob |
| Checkpoint | `fetch_log` table, status = pending/done/failed | Resume = re-run all non-done tasks; crash safety |

**Pre-demo snapshot:** run full ingest once, save `woundpipe.db`. Demo reads the snapshot — no live API dependency during the 10-minute presentation.

---

### Stage 2 — Normalize

**Goal:** typed columns from raw TEXT payloads; compute derived booleans.

Key transforms:
- `active_mcb` = `payer_code == 'MCB'` AND `effective_to IS NULL` (NOT `payer_type`)
- `active_wound_dx` = ICD-10 in wound families (`L89`, `E11.6x`, `I87`, `I70`, `L97`, `L98`) AND `clinical_status == 'active'`
- Assessment `raw_json` routing: if flat shape → generated columns handle it; if nested narrative shape → extract the answer string, send to text extractor

---

### Stage 3 — Extract (hardest stage)

**Goal:** fill `wound_extraction` rows with per-field values and confidence scores.

**Three lanes:**

**Lane 1 — Regex (always runs, owns all numbers)**
Runs on every note and assessment. Returns a verbatim substring from the source text or NULL — never invents. Owns: measurements (L×W×D in every format variant), stage, drainage level, location. The tolerant measurement grammar covers `4.3 cm x 1.8 cm x 0.3 cm`, `4.5cm`, `Meas 4.2x3.1x1.5cm`, `0.9cm deep`, and 2D-only measurements.

**Lane 2 — LLM (runs on Envive and ambiguous cases)**
`claude-haiku-4-5-20251001` for bulk; escalate to `claude-sonnet-4-6` if haiku produces conflicting fields. Uses Structured Outputs (constrained decoding with JSON schema) so the output shape is guaranteed. Schema field order: evidence span before value — forces the model to ground its answer in the source text before stating the value.

Verbatim-span gate: any numeric measurement the LLM returns that is NOT present as a literal substring in the note text is dropped and set to NULL. This prevents hallucinated dimensions from reaching a billing claim.

LLM owns: Envive narrative comprehension, wound-type normalization (e.g., `"Diabetic diabetic"` → DFU), primary wound selection in multi-wound notes.

**Lane 3 — Reconciler (produces final confidence)**
Per field: if regex and LLM agree → high confidence; if only one source → medium; if conflict → low, set flag. Cross-source agreement (ICD-10 dx vs. note vs. assessment): each agreeing source increments `overall_conf`. The reconciler, not the LLM, owns the final confidence number.

**Format detection** (from note text, not `note_type`):
- Envive: contains `*Envive`
- SOAP: contains `Subjective:` / `Objective:`
- Prose/shorthand: contains `Meas` abbreviation
- Multi-wound: wound count > 1 mentioned, or `Wound #` pattern

---

### Stage 4 — Route

**Goal:** one routing decision + one plain-English reason per patient.

**Deterministic policy (no ML, fully auditable):**

| Check | Failing result | Reason text |
|---|---|---|
| Has active MCB coverage | reject | "No active Medicare Part B coverage" |
| Has active wound | reject | "No active wound" |
| wound_type extracted | reject | "No extractable wound found" |
| All required measurements + confidence ≥ 0.80 + sources agree | auto_accept | "Active MCB + active wound + complete measurements — sources agree" |
| Anything else | flag_for_review | Specific missing field (depth, drainage, confidence, corroboration) |

**Why deterministic, not ML:** billers auditing Medicare claims must be able to trace every decision. A rule-based classifier gives route + reason by construction. The 0.80 threshold is a placeholder to calibrate against a small gold set before trusting auto-routing at scale.

---

### Stage 5 — Publish

**Goal:** produce the output artifacts the dashboard consumes.

- The `v_patient_eligibility` VIEW is the live output — no materialization needed for 300 patients
- Export VIEW to `data/eligibility_export.json` (static; the dashboard reads this, not a live API)
- Write `RunManifest` to the `runs` table and `data/runs/<run_id>.json` — these are the real numbers the data-flow animation uses (total calls, 429s, retries, notes by format, routing distribution, per-stage timing)

---

## 4. Incremental sync

Three endpoints support a `since` parameter: `/patients` (by `last_modified_at`), `/notes` (by `effective_date`), `/assessments` (by `assessment_date`). `/diagnoses` and `/coverage` have no `since` — re-fetch only for patients whose `last_modified_at` advanced, or just re-fetch all (only 600 calls).

The `sync_state` table stores the per-facility, per-endpoint high-water mark. Advance it only after a clean run. The idempotent UPSERT makes a partial incremental run safe — re-fetching an already-stored record produces no duplicates.

This is the "wow" mini-demo moment: run full sync once offline, then show a live `since` run that pulls only changed records in seconds.

---

## 5. Team ownership (4 people)

| Person | Owns | Interfaces to others |
|---|---|---|
| **Person 1** | Ingestion, identity resolution, CLI orchestration | Delivers: `pcc_patient`, `progress_note`, `pcc_assessment`, `pcc_diagnosis`, `pcc_coverage` populated in SQLite with `fetch_log` clean |
| **Person 2** | Database schema, migrations, eligibility views, queries | Delivers: `v_patient_eligibility` columns + `eligibility_export.json` schema to P4; receives `wound_extraction` contract from P3 |
| **Person 3** | Format detection, regex extraction, LLM extraction, reconciler | Delivers: `wound_extraction` rows with all per-field values and confidence scores |
| **Person 4** | Frontend dashboard, publish/export | Consumes: `eligibility_export.json` + `runs/<run_id>.json` manifest; owns the biller-facing UI |

---

## 6. Open decisions

| # | Question | Options | Current lean |
|---|---|---|---|
| Q1 | ORM vs raw sqlite3? | SQLAlchemy-core vs stdlib sqlite3 | stdlib — fixed DDL, no query builder needed |
| Q2 | LLM: all notes or Envive-only? | All notes vs ambiguous/Envive only | Only when regex fails or format is Envive |
| Q3 | 0.80 confidence threshold — ship or calibrate? | Build small gold set vs ship as placeholder | Ship as placeholder; disclose it clearly in the presentation |
| Q4 | Dashboard data source — FastAPI endpoint or static JSON? | FastAPI (more impressive) vs static JSON (demo-safe) | Static JSON for demo; FastAPI is optional stretch |
| Q5 | Multi-wound primary selection — rule or LLM? | Largest wound / first wound / LLM judgment | LLM judgment for primary selection; regex owns measurements after |
| Q6 | Expand wound ICD-10 families beyond pressure ulcers? | L89 only vs L89 + E11.6 + I87 + I70 + L97 + L98 | Expand — DFU and venous ulcer are in the stated wound type list |

---

## 7. Build order (critical path first)

| Phase | Time estimate | Deliverable | Must / Stretch |
|---|---|---|---|
| Scaffold | 30 min | Repo, pyproject.toml, SQLite init, migration runner, CLI skeleton | Must |
| Ingest + identity resolve | 90 min | All 300 patients fetched with 429 handling; `fetch_log` checkpoint working; pre-demo snapshot saved | Must (critical path) |
| Normalize | 60 min | `active_mcb`, `active_wound_dx`, assessment JSON unwrap | Must |
| Extract — regex baseline | 90 min | All 4 formats covered; `wound_extraction` rows populated | Must |
| Extract — LLM lane | 30 min | Envive + ambiguous cases; verbatim-span gate | Must (layered on regex) |
| Route | 60 min | Deterministic policy; plain-English reasons; `v_patient_eligibility` working | Must |
| Publish | 30 min | `eligibility_export.json` + run manifest JSON | Must |
| Frontend — triage table + patient detail | 150 min | Biller-facing table with routing colors, reason column, source highlight | Must |
| Polish | 90 min | Animated pipeline flow, Sankey, `since` incremental mini-demo, gold-set spot-check | Stretch |

**Cut line:** if time runs short, keep everything through Publish + a static triage table. Drop the animated pipeline view, `since` sync demo, and live FastAPI endpoint.

---

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Live API fails mid-demo | Pre-saved SQLite snapshot — demo never depends on cold fetch |
| 429 storms extend full ingest to 20+ minutes | Semaphore(8) + honor `Retry-After` exactly; surface retry count as a story, not a failure |
| LLM unavailable or too slow | Regex baseline always present; LLM is a layer on top; timeout-bounded; fallback to `flag_for_review` |
| `note_type` misleads format detection | Sniff format from `note_text` content — `note_type` is ignored |
| Assessment `raw_json` in unexpected shape | Parser handles both flat and nested narrative shapes |
| Wrong `payer_type` value from API | Key off `payer_code == 'MCB'`, confirmed against live API data |
| 0.80 threshold routes incorrectly | Label it a placeholder in the presentation; honest methodology beats a hidden assumption |
