-- 007_multi_wound.up.sql
-- Make the WOUND the unit of work. Each distinct wound (identified by a normalized
-- wound_key = wound_type|location) is its own billable line with its own route.
-- This migration is ADDITIVE: the per-patient v_patient_eligibility (and the
-- per-patient corroboration views) are left intact for the funnel rollup and the
-- existing routing tests; the new per-wound views live alongside them.

-- Wound identity within a patient (set by the extractor; NULL on un-re-extracted rows).
ALTER TABLE wound_extraction ADD COLUMN wound_key TEXT;

-- Dedup now allows several wounds per source record (one per wound_key).
DROP INDEX IF EXISTS ux_wx_dedup;
CREATE UNIQUE INDEX ux_wx_dedup ON wound_extraction(
  patient_id, source_kind, IFNULL(source_note_id,-1), IFNULL(source_assessment_id,-1), IFNULL(wound_key,''));
CREATE INDEX IF NOT EXISTS ix_wx_woundkey ON wound_extraction(patient_id, wound_key);

-- Per-wound AI summary. Keyed by (patient_id, wound_key) so it survives re-extraction
-- (wound_extraction rows are cleared+rebuilt each run). Supersedes pcc_patient.ai_summary.
CREATE TABLE wound_summary (
  patient_id       TEXT NOT NULL,
  wound_key        TEXT NOT NULL,
  ai_summary       TEXT,
  ai_summary_model TEXT,
  ai_summary_at    TEXT,
  PRIMARY KEY (patient_id, wound_key)
) STRICT;

-- ---- per-wound corroboration ------------------------------------------------
-- One row per (wound x evidence source). Rows sharing a wound_key ARE the same
-- wound by construction, so they always corroborate (different keys = different
-- wounds, never a "conflict"). This is what removes the false-conflict artifact.
CREATE VIEW v_wkey_corroboration AS
WITH wnd AS (
  SELECT patient_id, wound_key, stage, MAX(overall_conf) AS overall_conf
  FROM wound_extraction WHERE wound_key IS NOT NULL
  GROUP BY patient_id, wound_key
)
SELECT
  w.patient_id, w.wound_key,
  w.source_kind            AS evidence_node,
  w.source_note_id, w.source_assessment_id,
  w.id                     AS extraction_id,
  1                        AS type_agrees,
  1                        AS location_agrees,
  (w.stage IS p.stage)     AS stage_agrees,
  1                        AS corroborates,
  w.overall_conf, w.evidence_quote
FROM wound_extraction w
JOIN wnd p USING (patient_id, wound_key)
WHERE w.wound_key IS NOT NULL;

CREATE VIEW v_wkey_corroboration_summary AS
SELECT patient_id, wound_key,
  COUNT(*) AS n_sources, COUNT(*) AS n_agree, 1 AS all_agree, 0 AS n_conflict
FROM v_wkey_corroboration
GROUP BY patient_id, wound_key;

-- ---- per-wound eligibility / routing ----------------------------------------
-- Same rule family as v_patient_eligibility, evaluated PER WOUND. MCB coverage and
-- source-completeness are patient-level; measurements, drainage, confidence and
-- cross-source agreement (>=2 sources on THIS wound_key) are per-wound. The
-- corroborating ICD-10 wound dx stays patient-level (per-wound dx-site matching is
-- a future refinement).
CREATE VIEW v_wound_eligibility AS
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
  JOIN wound_icd_family f ON d.icd10_code LIKE f.prefix || '%'
  WHERE d.clinical_status = 'active' AND d.is_current = 1
),
wnd AS (
  SELECT patient_id, wound_key, wound_type, stage, location,
         length_cm, width_cm, depth_cm, drainage, MAX(overall_conf) AS overall_conf
  FROM wound_extraction WHERE wound_key IS NOT NULL
  GROUP BY patient_id, wound_key
),
corr AS (
  SELECT patient_id, wound_key, n_sources, n_agree, all_agree, n_conflict
  FROM v_wkey_corroboration_summary
),
fh AS (
  SELECT patient_id, failed_fetches FROM v_patient_fetch_health
)
SELECT
  wnd.patient_id, wnd.wound_key,
  p.id AS internal_id, p.facility_id, p.first_name, p.last_name,
  p.primary_payer_code AS payer_code,
  wnd.wound_type, wnd.stage, wnd.location,
  wnd.length_cm, wnd.width_cm, wnd.depth_cm, wnd.drainage,
  wnd.overall_conf AS confidence,
  (amcb.patient_id IS NOT NULL)                                   AS has_active_mcb,
  1                                                               AS has_active_wound,
  (awd.patient_id IS NOT NULL)                                    AS has_active_wound_dx,
  COALESCE(corr.n_sources, 0)                                     AS n_sources,
  COALESCE(corr.n_agree, 0)                                       AS n_agree,
  COALESCE(corr.all_agree, 0)                                     AS all_agree,
  COALESCE(corr.n_conflict, 0)                                    AS n_conflict,
  COALESCE(fh.failed_fetches, 0)                                  AS failed_fetches,
  (COALESCE(fh.failed_fetches, 0) = 0)                            AS data_complete,
  CASE
    WHEN amcb.patient_id IS NULL                                  THEN 'reject'
    WHEN wnd.wound_type IS NULL                                   THEN 'reject'
    WHEN COALESCE(fh.failed_fetches, 0) > 0                       THEN 'flag_for_review'
    WHEN wnd.length_cm IS NOT NULL AND wnd.width_cm IS NOT NULL
         AND wnd.depth_cm IS NOT NULL AND wnd.drainage IS NOT NULL
         AND wnd.overall_conf >= cfg.tau
         AND corr.n_sources >= 2
         AND awd.patient_id IS NOT NULL                           THEN 'auto_accept'
    ELSE 'flag_for_review'
  END AS route,
  CASE
    WHEN amcb.patient_id IS NULL                                  THEN 'No active Medicare Part B coverage'
    WHEN wnd.wound_type IS NULL                                   THEN 'No extractable wound found'
    WHEN COALESCE(fh.failed_fetches, 0) > 0
      THEN 'Incomplete data — ' || COALESCE(fh.failed_fetches, 0)
           || ' source fetch(es) failed; re-run ingest before billing'
    WHEN wnd.length_cm IS NOT NULL AND wnd.width_cm IS NOT NULL
         AND wnd.depth_cm IS NOT NULL AND wnd.drainage IS NOT NULL
         AND wnd.overall_conf >= cfg.tau
         AND corr.n_sources >= 2
         AND awd.patient_id IS NOT NULL
      THEN 'Active MCB + active wound + complete measurements; multiple sources agree — safe to bill'
    WHEN wnd.depth_cm IS NULL                                     THEN 'Missing depth measurement'
    WHEN wnd.length_cm IS NULL OR wnd.width_cm IS NULL            THEN 'Missing wound dimensions'
    WHEN wnd.drainage IS NULL                                     THEN 'Missing drainage'
    WHEN COALESCE(corr.n_sources, 0) < 2                          THEN 'Single-source extraction — no cross-source corroboration'
    WHEN awd.patient_id IS NULL                                   THEN 'No corroborating active ICD-10 wound diagnosis'
    WHEN wnd.overall_conf < cfg.tau                              THEN 'Low extraction confidence'
    ELSE 'Incomplete documentation — needs manual review'
  END AS reason
FROM wnd
JOIN pcc_patient p ON p.patient_id = wnd.patient_id AND p.is_current = 1
CROSS JOIN cfg
LEFT JOIN active_mcb      amcb ON amcb.patient_id = wnd.patient_id
LEFT JOIN active_wound_dx awd  ON awd.patient_id  = wnd.patient_id
LEFT JOIN corr  ON corr.patient_id = wnd.patient_id AND corr.wound_key = wnd.wound_key
LEFT JOIN fh    ON fh.patient_id   = wnd.patient_id;
