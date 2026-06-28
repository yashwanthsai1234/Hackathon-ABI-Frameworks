-- 004_extract_state.up.sql
-- Fix 1: incremental extract. Replaces the global DELETE-and-rebuild with a
-- per-patient watermark. `fingerprint` is a hash of the patient's source rows
-- (notes + assessments + diagnoses, each as id+sync_version); when it is
-- unchanged since the last extraction the patient is skipped, so a re-run only
-- re-extracts patients whose source data actually changed. This also bounds
-- memory (per-patient transactions = streaming) instead of one giant txn.
CREATE TABLE extract_state (
  patient_id   TEXT NOT NULL PRIMARY KEY REFERENCES pcc_patient(patient_id),
  fingerprint  TEXT NOT NULL,        -- sha256 of sorted (kind,id,sync_version)
  extracted_at TEXT NOT NULL
) STRICT;
