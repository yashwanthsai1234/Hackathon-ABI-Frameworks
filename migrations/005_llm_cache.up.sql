-- 005_llm_cache.up.sql
-- Fix 2: content-addressed cache for the Claude extraction lane. cache_key =
-- sha256(model | prompt_version | note_text), so identical notes are extracted
-- by the LLM at most once — across patients AND across runs. Combined with the
-- bounded concurrent pre-pass + per-call backoff, this turns the serial
-- pay-every-run LLM lane into pay-once-per-distinct-note.
CREATE TABLE llm_cache (
  cache_key   TEXT NOT NULL PRIMARY KEY,
  wounds_json TEXT NOT NULL,          -- span-gated wounds[] (anti-hallucination applied)
  model       TEXT,
  created_at  TEXT NOT NULL
) STRICT;
