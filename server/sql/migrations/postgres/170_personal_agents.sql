-- Existing Agents and queued jobs remain system resources (NULL owner).
ALTER TABLE agents ADD COLUMN owner_user_id TEXT REFERENCES users(user_id) ON DELETE RESTRICT;
CREATE INDEX idx_agents_owner ON agents(owner_user_id);
ALTER TABLE agent_jobs ADD COLUMN requested_by_user_id TEXT;
ALTER TABLE agent_jobs ADD COLUMN project_id TEXT;
CREATE INDEX idx_agent_jobs_requester ON agent_jobs(status,requested_by_user_id,created_at);
