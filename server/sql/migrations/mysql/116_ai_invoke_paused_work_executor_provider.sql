-- 116_ai_invoke_paused_work_executor_provider.sql
-- flowgate.default.0596 T0004 (NR0003 rev3). Preserve the ACTUAL work-hop executor across
-- the review gate and pause->resume, separately from continuation_base_provider_id (the
-- header/default selection). A step-level provider (stored sequence / step override) can be
-- the provider that really produced the work while the header default stays whatever the
-- chain was started with; resolve_step_executor() used base_provider_id as a stand-in for
-- "the provider that just did the work", so a rework silently ran on the header default
-- instead of the actual author. Additive only, NULL-able: pre-existing paused rows keep
-- today's fallback behaviour (resolve_step_executor falls through to base/stored/default).
-- ON DELETE SET NULL, matching 076a: deleting a provider degrades this to "no captured
-- executor", never deletes the user's paused chain row.
--
-- InnoDB parses and silently discards a column-level inline REFERENCES on
-- ALTER TABLE ... ADD COLUMN (the same class of problem 086a_ai_invoke_paused_provider_fk.sql
-- repairs for continuation_base_provider_id). This file is new and unmerged, so unlike 076a
-- there is no deployed MySQL database whose applied-migrations record needs preserving; the
-- fix is folded directly into this migration rather than a follow-up 116a file: add the
-- plain column, then a table-level ADD CONSTRAINT ... FOREIGN KEY ... ON DELETE SET NULL to
-- actually create the FK.

ALTER TABLE ai_invoke_paused_chains ADD COLUMN continuation_work_executor_provider_id VARCHAR(191);

ALTER TABLE ai_invoke_paused_chains
    ADD CONSTRAINT fk_aipc_work_executor_provider
    FOREIGN KEY (continuation_work_executor_provider_id) REFERENCES ai_providers(provider_id)
    ON DELETE SET NULL;
