-- Persist the provider-selection decision for every completed AI invoke hop.
-- Existing rows remain compatible: both columns are nullable.
ALTER TABLE ai_invoke_runs ADD COLUMN selected_provider_source VARCHAR(32) NULL;
ALTER TABLE ai_invoke_runs ADD COLUMN fallback_allowed BOOLEAN NULL;
