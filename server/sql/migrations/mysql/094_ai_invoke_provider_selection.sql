-- Persist the provider-resolution tier and whether startup fallback was possible.
ALTER TABLE ai_invoke_runs ADD COLUMN selected_provider_source VARCHAR(32) NULL;
ALTER TABLE ai_invoke_runs ADD COLUMN fallback_allowed TINYINT(1) NULL DEFAULT 0;
