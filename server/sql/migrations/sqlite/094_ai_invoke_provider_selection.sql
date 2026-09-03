-- Persist the provider-resolution tier and whether startup fallback was possible.
ALTER TABLE ai_invoke_runs ADD COLUMN selected_provider_source TEXT NULL;
ALTER TABLE ai_invoke_runs ADD COLUMN fallback_allowed INTEGER NULL DEFAULT 0;
