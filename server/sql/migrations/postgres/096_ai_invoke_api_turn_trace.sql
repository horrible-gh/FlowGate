-- B0001 API turn correlation trace
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS api_turn_trace TEXT;
