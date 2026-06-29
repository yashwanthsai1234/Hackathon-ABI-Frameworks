-- 007_multi_wound.down.sql
DROP VIEW IF EXISTS v_wound_eligibility;
DROP VIEW IF EXISTS v_wkey_corroboration_summary;
DROP VIEW IF EXISTS v_wkey_corroboration;
DROP TABLE IF EXISTS wound_summary;
DROP INDEX IF EXISTS ix_wx_woundkey;
DROP INDEX IF EXISTS ux_wx_dedup;
CREATE UNIQUE INDEX ux_wx_dedup ON wound_extraction(
  patient_id, source_kind, IFNULL(source_note_id,-1), IFNULL(source_assessment_id,-1));
ALTER TABLE wound_extraction DROP COLUMN wound_key;
