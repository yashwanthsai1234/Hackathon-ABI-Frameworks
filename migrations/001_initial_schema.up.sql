-- 001_initial_schema.up.sql
-- woundpipe full schema (greenfield, migration 001). SQLite 3.51+ STRICT.
-- Conforms to SPEC §C.1 and the spec-data report. All constraints live in the
-- schema (NOT NULL / UNIQUE / FK / CHECK enums); the app outlives no invariant.

-- =====================================================================
-- RAW / LANDING LAYER
-- =====================================================================
CREATE TABLE pcc_patient (
  patient_id          TEXT    NOT NULL PRIMARY KEY,  -- string identity (FA-001) -> dx, coverage
  id                  INTEGER NOT NULL UNIQUE,       -- integer identity (1)     -> notes, assessments
  facility_id         INTEGER NOT NULL,
  first_name          TEXT,
  last_name           TEXT,
  birth_date          TEXT,
  gender              TEXT,
  primary_payer_code  TEXT,
  last_modified_at    TEXT,
  is_new_admission    INTEGER NOT NULL DEFAULT 0 CHECK (is_new_admission IN (0,1)),
  fetched_at          TEXT    NOT NULL,
  source_endpoint     TEXT    NOT NULL DEFAULT '/pcc/patients',
  raw_payload         TEXT    NOT NULL,
  sync_version        INTEGER NOT NULL DEFAULT 1,
  is_current          INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0,1))
) STRICT;
CREATE INDEX ix_patient_facility ON pcc_patient(facility_id);
CREATE INDEX ix_patient_payer    ON pcc_patient(primary_payer_code);

CREATE TABLE pcc_diagnosis (
  id                INTEGER NOT NULL PRIMARY KEY,
  patient_id        TEXT    NOT NULL REFERENCES pcc_patient(patient_id),
  icd10_code        TEXT,
  icd10_description TEXT,
  clinical_status   TEXT CHECK (clinical_status IN ('active','resolved','inactive') OR clinical_status IS NULL),
  onset_date        TEXT,
  last_modified_at  TEXT,
  fetched_at        TEXT    NOT NULL,
  source_endpoint   TEXT    NOT NULL DEFAULT '/pcc/diagnoses',
  raw_payload       TEXT    NOT NULL,
  sync_version      INTEGER NOT NULL DEFAULT 1,
  is_current        INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0,1))
) STRICT;
CREATE INDEX ix_dx_patient ON pcc_diagnosis(patient_id);
-- PARTIAL (R3): any active wound dx; family membership decided by wound_icd_family
-- join in the eligibility view, not by a hardcoded L89 predicate here.
CREATE INDEX ix_dx_active_wound ON pcc_diagnosis(patient_id)
  WHERE clinical_status = 'active';

CREATE TABLE pcc_coverage (
  id               INTEGER NOT NULL PRIMARY KEY,
  patient_id       TEXT    NOT NULL REFERENCES pcc_patient(patient_id),
  payer_name       TEXT,
  payer_code       TEXT,            -- eligibility keys off 'MCB' (NOT payer_type)
  payer_type       TEXT,           -- reality: "Medicare", not "Medicare B"
  effective_from   TEXT,
  effective_to     TEXT,            -- NULL => active coverage (eligibility key)
  last_modified_at TEXT,
  fetched_at       TEXT    NOT NULL,
  source_endpoint  TEXT    NOT NULL DEFAULT '/pcc/coverage',
  raw_payload      TEXT    NOT NULL,
  sync_version     INTEGER NOT NULL DEFAULT 1,
  is_current       INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0,1))
) STRICT;
-- PARTIAL: #1 eligibility hot path (active Medicare Part B), no table scan.
CREATE INDEX ix_cov_active_mcb ON pcc_coverage(patient_id)
  WHERE payer_code = 'MCB' AND effective_to IS NULL;

CREATE TABLE progress_note (
  id              INTEGER NOT NULL PRIMARY KEY,
  patient_id      INTEGER NOT NULL REFERENCES pcc_patient(id),  -- INTEGER identity
  org_id          TEXT,
  pcc_note_id     INTEGER,
  note_type       TEXT,             -- does NOT determine format (sniff from text)
  effective_date  TEXT,
  note_text       TEXT,
  created_by      TEXT,
  note_label      TEXT,
  fetched_at      TEXT    NOT NULL,
  source_endpoint TEXT    NOT NULL DEFAULT '/pcc/notes',
  raw_payload     TEXT    NOT NULL,
  sync_version    INTEGER NOT NULL DEFAULT 1,
  is_current      INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0,1))
) STRICT;
CREATE INDEX ix_note_patient ON progress_note(patient_id, effective_date);

CREATE TABLE pcc_assessment (
  id                          INTEGER NOT NULL PRIMARY KEY,
  patient_id                  INTEGER NOT NULL REFERENCES pcc_patient(id),
  org_id                      TEXT,
  pcc_assessment_id           INTEGER,
  assessment_type             TEXT,
  status                      TEXT,
  assessment_date             TEXT,
  completion_date             TEXT,
  template_id                 INTEGER,
  assessment_type_description TEXT,
  raw_json                    TEXT,   -- shape VARIES: flat OR nested narrative
  -- VIRTUAL generated cols: zero-storage structured pull for the flat shape;
  -- nested-narrative shapes return NULL here -> text extractor handles them.
  a_wound_type TEXT GENERATED ALWAYS AS (json_extract(raw_json,'$.wound_type')) VIRTUAL,
  a_stage      TEXT GENERATED ALWAYS AS (json_extract(raw_json,'$.stage'))      VIRTUAL,
  a_location   TEXT GENERATED ALWAYS AS (json_extract(raw_json,'$.location'))   VIRTUAL,
  a_length_cm  REAL GENERATED ALWAYS AS (json_extract(raw_json,'$.length_cm'))  VIRTUAL,
  a_width_cm   REAL GENERATED ALWAYS AS (json_extract(raw_json,'$.width_cm'))   VIRTUAL,
  a_depth_cm   REAL GENERATED ALWAYS AS (json_extract(raw_json,'$.depth_cm'))   VIRTUAL,
  fetched_at      TEXT    NOT NULL,
  source_endpoint TEXT    NOT NULL DEFAULT '/pcc/assessments',
  raw_payload     TEXT    NOT NULL,
  sync_version    INTEGER NOT NULL DEFAULT 1,
  is_current      INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0,1))
) STRICT;
CREATE INDEX ix_assess_patient ON pcc_assessment(patient_id, assessment_date);
CREATE INDEX ix_assess_wtype   ON pcc_assessment(a_wound_type);

-- FTS5 external-content index over note_text (zero text duplication).
CREATE VIRTUAL TABLE progress_note_fts USING fts5(
  note_text,
  content='progress_note',
  content_rowid='id',
  tokenize='porter unicode61'
);
CREATE TRIGGER progress_note_ai AFTER INSERT ON progress_note BEGIN
  INSERT INTO progress_note_fts(rowid, note_text) VALUES (new.id, new.note_text);
END;
CREATE TRIGGER progress_note_ad AFTER DELETE ON progress_note BEGIN
  INSERT INTO progress_note_fts(progress_note_fts, rowid, note_text)
    VALUES ('delete', old.id, old.note_text);
END;
CREATE TRIGGER progress_note_au AFTER UPDATE ON progress_note BEGIN
  INSERT INTO progress_note_fts(progress_note_fts, rowid, note_text)
    VALUES ('delete', old.id, old.note_text);
  INSERT INTO progress_note_fts(rowid, note_text) VALUES (new.id, new.note_text);
END;

-- =====================================================================
-- EXTRACTION / DERIVED LAYER
-- =====================================================================
CREATE TABLE wound_extraction (
  id                   INTEGER PRIMARY KEY,
  patient_id           TEXT NOT NULL REFERENCES pcc_patient(patient_id),
  source_kind          TEXT NOT NULL CHECK (source_kind IN ('note','assessment','diagnosis')),
  source_note_id       INTEGER REFERENCES progress_note(id),
  source_assessment_id INTEGER REFERENCES pcc_assessment(id),
  is_primary           INTEGER NOT NULL DEFAULT 1 CHECK (is_primary IN (0,1)),
  extraction_method    TEXT NOT NULL CHECK (extraction_method IN
    ('regex_spn','regex_envive','regex_prose','soap','json','llm','manual')),
  wound_type      TEXT,
  wound_type_conf REAL CHECK (wound_type_conf BETWEEN 0 AND 1 OR wound_type_conf IS NULL),
  stage           TEXT CHECK (stage IN ('1','2','3','4','unstageable','DTI','N/A') OR stage IS NULL),
  stage_conf      REAL CHECK (stage_conf BETWEEN 0 AND 1 OR stage_conf IS NULL),
  location        TEXT,
  location_conf   REAL CHECK (location_conf BETWEEN 0 AND 1 OR location_conf IS NULL),
  length_cm       REAL CHECK (length_cm >= 0 OR length_cm IS NULL),
  width_cm        REAL CHECK (width_cm  >= 0 OR width_cm  IS NULL),
  depth_cm        REAL CHECK (depth_cm  >= 0 OR depth_cm  IS NULL),
  measure_conf    REAL CHECK (measure_conf BETWEEN 0 AND 1 OR measure_conf IS NULL),
  drainage        TEXT CHECK (drainage IN ('none','light','moderate','heavy') OR drainage IS NULL),
  drainage_conf   REAL CHECK (drainage_conf BETWEEN 0 AND 1 OR drainage_conf IS NULL),
  overall_conf    REAL CHECK (overall_conf BETWEEN 0 AND 1 OR overall_conf IS NULL),
  -- primary-wound summary highlight span (cheap path); per-field spans live in
  -- wound_field_evidence (migration 002, R1).
  evidence_span_start INTEGER,
  evidence_span_end   INTEGER,
  evidence_quote      TEXT,
  extracted_at        TEXT NOT NULL
) STRICT;
CREATE INDEX ix_wx_patient ON wound_extraction(patient_id, is_primary);
CREATE INDEX ix_wx_source  ON wound_extraction(source_kind, patient_id);
-- One extraction per (patient, source_kind, source row); IFNULL sentinels make
-- the synthetic 'diagnosis' rows (no note/assessment id) dedup cleanly.
CREATE UNIQUE INDEX ux_wx_dedup ON wound_extraction(
  patient_id, source_kind, IFNULL(source_note_id,-1), IFNULL(source_assessment_id,-1));

-- =====================================================================
-- RUNTIME / OPERATIONAL LAYER
-- =====================================================================
CREATE TABLE fetch_log (
  task_id        TEXT NOT NULL PRIMARY KEY,   -- deterministic '<endpoint>:<identity_value>'
  endpoint       TEXT NOT NULL,
  identity_kind  TEXT CHECK (identity_kind IN ('facility','patient_id','id') OR identity_kind IS NULL),
  identity_value TEXT,
  status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','in_flight','done','failed')),
  attempts        INTEGER NOT NULL DEFAULT 0,
  http_status     INTEGER,
  retry_count     INTEGER NOT NULL DEFAULT 0,
  retry_after_s   REAL,
  n_records       INTEGER,
  content_hash    TEXT,                        -- R2 (ingestion): change detection
  error           TEXT,
  planned_at        TEXT,
  first_attempt_at  TEXT,
  last_attempt_at   TEXT,
  completed_at      TEXT
) STRICT;
-- PARTIAL: resume = "what's not done".
CREATE INDEX ix_fetch_pending ON fetch_log(status) WHERE status <> 'done';

CREATE TABLE run_manifest (
  run_id      TEXT NOT NULL PRIMARY KEY,
  started_at  TEXT,
  finished_at TEXT,
  n_calls     INTEGER,
  n_429       INTEGER,
  n_retries   INTEGER,
  n_patients  INTEGER,
  n_extracted INTEGER,
  counts_json TEXT,                            -- by-format / by-route JSON
  git_sha     TEXT
) STRICT;

-- R2 (ingestion): incremental 'since' sync watermark per scope.
CREATE TABLE sync_state (
  scope        TEXT NOT NULL PRIMARY KEY,      -- e.g. 'diagnoses:FA-001' or 'patients:101'
  watermark    TEXT,                           -- last seen last_modified_at / cursor
  last_run_id  TEXT,
  updated_at   TEXT
) STRICT;

-- R4: single source of truth for calibratable constants. The eligibility view
-- reads auto_accept_tau from here; calibration = one UPDATE, zero code edits.
CREATE TABLE config (
  key   TEXT NOT NULL PRIMARY KEY,
  value TEXT NOT NULL
) STRICT;
INSERT INTO config(key, value) VALUES ('auto_accept_tau', '0.80');

-- R3: wound ICD-10 family allowlist (case-insensitive prefix match in view).
CREATE TABLE wound_icd_family (
  prefix      TEXT NOT NULL PRIMARY KEY,
  wound_class TEXT NOT NULL
) STRICT;
INSERT INTO wound_icd_family(prefix, wound_class) VALUES
  ('L89',     'pressure_ulcer'),
  ('L97',     'non_pressure_lower_limb'),
  ('L98.41',  'non_pressure_other_site'),
  ('L98.42',  'non_pressure_other_site'),
  ('L98.43',  'non_pressure_other_site'),
  ('L98.44',  'non_pressure_other_site'),
  ('L98.45',  'non_pressure_other_site'),
  ('L98.46',  'non_pressure_other_site'),
  ('L98.47',  'non_pressure_other_site'),
  ('L98.48',  'non_pressure_other_site'),
  ('L98.49',  'non_pressure_other_site'),
  ('E11.621', 'diabetic_foot_ulcer'),
  ('E11.622', 'diabetic_skin_ulcer'),
  ('E10.621', 'diabetic_foot_ulcer'),
  ('E10.622', 'diabetic_skin_ulcer'),
  ('E08.621', 'diabetic_foot_ulcer'),
  ('E08.622', 'diabetic_skin_ulcer'),
  ('E09.621', 'diabetic_foot_ulcer'),
  ('E09.622', 'diabetic_skin_ulcer'),
  ('E13.621', 'diabetic_foot_ulcer'),
  ('E13.622', 'diabetic_skin_ulcer'),
  ('I83.0',   'venous_ulcer'),
  ('I83.2',   'venous_ulcer'),
  ('I70.23',  'arterial_ulcer'),
  ('I70.24',  'arterial_ulcer'),
  ('I70.25',  'arterial_ulcer');

-- =====================================================================
-- CORROBORATION VIEWS (§4b)
-- =====================================================================
-- One row per (patient x evidence-source): edge from each source to the
-- primary wound; corroborates = edge color (type AND location agree).
CREATE VIEW v_wound_corroboration AS
WITH primary_wound AS (
  SELECT patient_id, wound_type, stage, location, MAX(overall_conf) AS overall_conf
  FROM wound_extraction
  WHERE is_primary = 1
  GROUP BY patient_id
)
SELECT
  w.patient_id,
  w.source_kind            AS evidence_node,
  w.source_note_id,
  w.source_assessment_id,
  w.id                     AS extraction_id,
  (w.wound_type = p.wound_type)                          AS type_agrees,
  (w.location   = p.location)                            AS location_agrees,
  (w.stage IS p.stage)                                   AS stage_agrees,
  (w.wound_type = p.wound_type AND w.location = p.location) AS corroborates,
  w.overall_conf,
  w.evidence_quote
FROM wound_extraction w
JOIN primary_wound p USING (patient_id);

CREATE VIEW v_corroboration_summary AS
SELECT
  patient_id,
  COUNT(*)                              AS n_sources,
  SUM(corroborates)                     AS n_agree,
  MIN(corroborates)                     AS all_agree,
  (COUNT(*) - SUM(corroborates))        AS n_conflict
FROM v_wound_corroboration
GROUP BY patient_id;

-- =====================================================================
-- ELIGIBILITY VIEW (single SQL execution path for routing — R5)
-- threshold from config (R4); dx-family via wound_icd_family (R3);
-- agreement = all_agree=1 AND n_sources>=2.
-- =====================================================================
CREATE VIEW v_patient_eligibility AS
WITH cfg AS (
  SELECT CAST(value AS REAL) AS tau FROM config WHERE key = 'auto_accept_tau'
),
active_mcb AS (
  SELECT DISTINCT patient_id FROM pcc_coverage
  WHERE payer_code = 'MCB' AND effective_to IS NULL AND is_current = 1
),
active_wound_dx AS (
  SELECT DISTINCT d.patient_id
  FROM pcc_diagnosis d
  JOIN wound_icd_family f
    ON d.icd10_code LIKE f.prefix || '%'
  WHERE d.clinical_status = 'active' AND d.is_current = 1
),
prim AS (
  SELECT
    patient_id, wound_type, stage, location,
    length_cm, width_cm, depth_cm, drainage,
    MAX(overall_conf) AS overall_conf
  FROM wound_extraction
  WHERE is_primary = 1
  GROUP BY patient_id
),
corr AS (
  SELECT patient_id, n_sources, n_agree, all_agree, n_conflict
  FROM v_corroboration_summary
)
SELECT
  p.patient_id,
  p.id          AS internal_id,
  p.facility_id,
  p.first_name,
  p.last_name,
  p.primary_payer_code AS payer_code,
  prim.wound_type, prim.stage, prim.location,
  prim.length_cm, prim.width_cm, prim.depth_cm, prim.drainage,
  prim.overall_conf AS confidence,
  (amcb.patient_id IS NOT NULL)                                   AS has_active_mcb,
  (awd.patient_id IS NOT NULL OR prim.wound_type IS NOT NULL)     AS has_active_wound,
  (awd.patient_id IS NOT NULL)                                    AS has_active_wound_dx,
  COALESCE(corr.n_sources, 0)                                     AS n_sources,
  COALESCE(corr.n_agree, 0)                                       AS n_agree,
  COALESCE(corr.all_agree, 0)                                     AS all_agree,
  COALESCE(corr.n_conflict, 0)                                    AS n_conflict,
  CASE
    WHEN amcb.patient_id IS NULL                                  THEN 'reject'
    WHEN awd.patient_id IS NULL AND prim.wound_type IS NULL       THEN 'reject'
    WHEN prim.wound_type IS NULL                                  THEN 'reject'
    WHEN prim.length_cm IS NOT NULL AND prim.width_cm IS NOT NULL
         AND prim.depth_cm IS NOT NULL AND prim.drainage IS NOT NULL
         AND prim.overall_conf >= cfg.tau
         AND corr.all_agree = 1 AND corr.n_sources >= 2
         AND awd.patient_id IS NOT NULL                           THEN 'auto_accept'
    ELSE 'flag_for_review'
  END AS route,
  CASE
    WHEN amcb.patient_id IS NULL                                  THEN 'No active Medicare Part B coverage'
    WHEN awd.patient_id IS NULL AND prim.wound_type IS NULL       THEN 'No active wound diagnosis or documented wound'
    WHEN prim.wound_type IS NULL                                  THEN 'No extractable wound found'
    WHEN prim.length_cm IS NOT NULL AND prim.width_cm IS NOT NULL
         AND prim.depth_cm IS NOT NULL AND prim.drainage IS NOT NULL
         AND prim.overall_conf >= cfg.tau
         AND corr.all_agree = 1 AND corr.n_sources >= 2
         AND awd.patient_id IS NOT NULL
      THEN 'Active MCB + active wound + complete measurements; diagnosis, note and assessment agree — safe to bill'
    WHEN COALESCE(corr.n_conflict, 0) > 0                         THEN 'Sources disagree on wound type or location'
    WHEN prim.depth_cm IS NULL                                   THEN 'Missing depth measurement'
    WHEN prim.length_cm IS NULL OR prim.width_cm IS NULL          THEN 'Missing wound dimensions'
    WHEN prim.drainage IS NULL                                   THEN 'Missing drainage'
    WHEN COALESCE(corr.n_sources, 0) < 2                          THEN 'Single-source extraction — no cross-source corroboration'
    WHEN awd.patient_id IS NULL                                  THEN 'No corroborating active ICD-10 wound diagnosis'
    WHEN prim.overall_conf < cfg.tau                             THEN 'Low extraction confidence'
    ELSE 'Incomplete documentation — needs manual review'
  END AS reason
FROM pcc_patient p
CROSS JOIN cfg
LEFT JOIN active_mcb      amcb ON amcb.patient_id = p.patient_id
LEFT JOIN active_wound_dx awd  ON awd.patient_id  = p.patient_id
LEFT JOIN prim                 ON prim.patient_id = p.patient_id
LEFT JOIN corr                 ON corr.patient_id = p.patient_id
WHERE p.is_current = 1;
