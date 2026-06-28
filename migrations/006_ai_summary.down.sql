-- 006_ai_summary.down.sql
ALTER TABLE pcc_patient DROP COLUMN ai_summary_at;
ALTER TABLE pcc_patient DROP COLUMN ai_summary_model;
ALTER TABLE pcc_patient DROP COLUMN ai_summary;
