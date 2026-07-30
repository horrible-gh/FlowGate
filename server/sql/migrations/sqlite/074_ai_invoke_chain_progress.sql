-- 074_ai_invoke_chain_progress.sql
-- Persist chain-lifetime progress across a user pause/resume boundary (0357 T0004).

BEGIN;

ALTER TABLE ai_invoke_paused_chains ADD COLUMN chain_id TEXT;
ALTER TABLE ai_invoke_paused_chains ADD COLUMN chain_docs_target INTEGER;
ALTER TABLE ai_invoke_paused_chains ADD COLUMN chain_docs_reached INTEGER NOT NULL DEFAULT 0;

COMMIT;
