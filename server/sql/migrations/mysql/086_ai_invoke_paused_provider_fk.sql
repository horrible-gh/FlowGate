-- 086_ai_invoke_paused_provider_fk.sql
-- flowgate.default.0444 (B0001 → N0002 → NR0003 §4-3/§2-6 → WP0004 T#3).
-- 076a_ai_invoke_paused_provider.sql declared continuation_base_provider_id with a
-- column-level inline REFERENCES. InnoDB parses and silently discards inline
-- REFERENCES on ALTER TABLE ... ADD COLUMN (the same class of problem 076b's own
-- comment attributes to migration 067 -- see 067a_auth_sessions.sql for the same
-- ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY repair shape used here). 076a itself
-- is left untouched: it may already be applied to a deployed MySQL database, and
-- sqloader tracks applied migrations by filename, not content.
--
-- ON DELETE SET NULL (not CASCADE), matching 076a's stated intent: deleting a
-- provider must degrade a paused chain's pin to "no pin", never delete the paused
-- chain row itself.

ALTER TABLE ai_invoke_paused_chains
    ADD CONSTRAINT fk_aipc_provider
    FOREIGN KEY (continuation_base_provider_id) REFERENCES ai_providers(provider_id)
    ON DELETE SET NULL;
