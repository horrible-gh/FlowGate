-- 076_ai_invoke_paused_provider.sql
-- Preserve a paused chain's provider / [전달멘트] selections across pause→resume
-- (0365 B0001/NR0003/DB0004). Additive only: the four columns are NULL-able payload the
-- resume path reads back, so pre-existing paused rows keep today's fallback behaviour.
-- ON DELETE SET NULL (not CASCADE): deleting a provider must degrade the pin to "no pin",
-- never delete the user's paused chain row.

BEGIN;

ALTER TABLE ai_invoke_paused_chains ADD COLUMN continuation_base_provider_id TEXT REFERENCES ai_providers(provider_id) ON DELETE SET NULL;
ALTER TABLE ai_invoke_paused_chains ADD COLUMN continuation_provider_overrides TEXT;
ALTER TABLE ai_invoke_paused_chains ADD COLUMN continuation_default_note TEXT;
ALTER TABLE ai_invoke_paused_chains ADD COLUMN continuation_note_overrides TEXT;

COMMIT;
