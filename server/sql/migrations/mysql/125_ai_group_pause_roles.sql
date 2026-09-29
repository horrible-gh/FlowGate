ALTER TABLE ai_invoke_paused_chains ADD COLUMN requested_ai_group_id VARCHAR(191);
ALTER TABLE ai_invoke_paused_chains ADD COLUMN continuation_reworker_provider_id VARCHAR(191);
