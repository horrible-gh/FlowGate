-- Existing connections keep a NULL target; no Agent is selected automatically.
ALTER TABLE ai_providers ADD COLUMN target_agent_id TEXT REFERENCES agents(agent_id) ON DELETE RESTRICT;
CREATE INDEX idx_ai_providers_target_agent ON ai_providers(target_agent_id);
