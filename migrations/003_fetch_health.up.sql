-- 003_fetch_health.up.sql
-- Fix 3b: surface per-patient fan-out INCOMPLETENESS into routing so a patient
-- whose source fetches failed (e.g. sustained 429) can never auto_accept and is
-- given a distinct, actionable reason instead of looking like sparse charting.
--
-- A fan-out task is keyed by identity_value: 'patient_id' (FA-001) -> dx/coverage,
-- 'id' (1) -> notes/assessments. v_patient_fetch_health joins fetch_log back to
-- the patient on BOTH keys and counts failed/done fan-out tasks.

CREATE VIEW v_patient_fetch_health AS
SELECT
  p.patient_id,
  COALESCE(SUM(CASE WHEN fl.status = 'failed' THEN 1 ELSE 0 END), 0) AS failed_fetches,
  COALESCE(SUM(CASE WHEN fl.status = 'done'   THEN 1 ELSE 0 END), 0) AS done_fetches
FROM pcc_patient p
LEFT JOIN fetch_log fl
  ON fl.identity_kind IN ('patient_id', 'id')
 AND ( (fl.identity_kind = 'patient_id' AND fl.identity_value = p.patient_id)
    OR (fl.identity_kind = 'id'         AND fl.identity_value = CAST(p.id AS TEXT)) )
WHERE p.is_current = 1
GROUP BY p.patient_id;

-- Recreate v_patient_eligibility with a fetch-health join. New columns
-- (failed_fetches, data_complete) are additive; the route/reason CASEs gain an
-- incomplete-data guard placed AFTER the definitive rejects but BEFORE auto_accept.
DROP VIEW IF EXISTS v_patient_eligibility;
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
),
fh AS (
  SELECT patient_id, failed_fetches FROM v_patient_fetch_health
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
  COALESCE(fh.failed_fetches, 0)                                  AS failed_fetches,
  (COALESCE(fh.failed_fetches, 0) = 0)                            AS data_complete,
  CASE
    WHEN amcb.patient_id IS NULL                                  THEN 'reject'
    WHEN awd.patient_id IS NULL AND prim.wound_type IS NULL       THEN 'reject'
    WHEN prim.wound_type IS NULL                                  THEN 'reject'
    WHEN COALESCE(fh.failed_fetches, 0) > 0                       THEN 'flag_for_review'
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
    WHEN COALESCE(fh.failed_fetches, 0) > 0
      THEN 'Incomplete data — ' || COALESCE(fh.failed_fetches, 0)
           || ' source fetch(es) failed; re-run ingest before billing'
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
LEFT JOIN fh                   ON fh.patient_id   = p.patient_id
WHERE p.is_current = 1;
