-- 002_wound_field_evidence.up.sql
-- R1: per-FIELD evidence spans powering the patient-detail highlights[] and the
-- per-field confidence gauges. The scalar evidence_span_*/evidence_quote on
-- wound_extraction remain the cheap primary-wound summary span; this child table
-- is the canonical home for {value, span, method, confidence} per field.
CREATE TABLE wound_field_evidence (
  id            INTEGER PRIMARY KEY,
  extraction_id INTEGER NOT NULL REFERENCES wound_extraction(id),
  field         TEXT    NOT NULL,   -- 'wound_type'|'location'|'length'|'width'|'depth'|'drainage'|'stage'
  char_start    INTEGER,
  char_end      INTEGER,
  quote         TEXT,               -- verbatim substring (anti-hallucination)
  method        TEXT,               -- extraction_method that produced this field
  confidence    REAL CHECK (confidence BETWEEN 0 AND 1 OR confidence IS NULL)
) STRICT;
CREATE INDEX ix_wfe_extraction ON wound_field_evidence(extraction_id);
