-- 001_initial_schema.down.sql
-- Reverse of 001 in FK-safe order: views -> triggers -> FTS -> child tables ->
-- standalone tables -> root table. (Runner also disables FK during DDL.)

-- Views (depend on tables; drop most-derived first).
DROP VIEW IF EXISTS v_patient_eligibility;
DROP VIEW IF EXISTS v_corroboration_summary;
DROP VIEW IF EXISTS v_wound_corroboration;

-- FTS triggers + virtual table (before progress_note).
DROP TRIGGER IF EXISTS progress_note_au;
DROP TRIGGER IF EXISTS progress_note_ad;
DROP TRIGGER IF EXISTS progress_note_ai;
DROP TABLE IF EXISTS progress_note_fts;

-- Derived table referencing pcc_patient / progress_note / pcc_assessment.
DROP TABLE IF EXISTS wound_extraction;

-- Operational / reference tables (no inbound FKs).
DROP TABLE IF EXISTS fetch_log;
DROP TABLE IF EXISTS run_manifest;
DROP TABLE IF EXISTS sync_state;
DROP TABLE IF EXISTS config;
DROP TABLE IF EXISTS wound_icd_family;

-- Child raw tables referencing pcc_patient.
DROP TABLE IF EXISTS pcc_assessment;
DROP TABLE IF EXISTS progress_note;
DROP TABLE IF EXISTS pcc_coverage;
DROP TABLE IF EXISTS pcc_diagnosis;

-- Root identity table last.
DROP TABLE IF EXISTS pcc_patient;
