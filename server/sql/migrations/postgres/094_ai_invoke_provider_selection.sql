-- Persist the provider-resolution tier and whether startup fallback was possible.
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS selected_provider_source TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS fallback_allowed SMALLINT NULL DEFAULT 0;
