-- 006_ai_summary.up.sql
-- Per-patient AI summary: a short, human-readable overview shown in the
-- patient-detail panel. Generated from the patient's structured data (Claude
-- haiku when an API key is configured, deterministic data-driven text otherwise)
-- and persisted so it is computed once and survives re-publishes.
-- ai_summary_model records provenance ('deterministic-v1' or the model id);
-- ai_summary_at is the UTC ISO timestamp it was written.
ALTER TABLE pcc_patient ADD COLUMN ai_summary       TEXT;
ALTER TABLE pcc_patient ADD COLUMN ai_summary_model TEXT;
ALTER TABLE pcc_patient ADD COLUMN ai_summary_at    TEXT;
