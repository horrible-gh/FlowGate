-- Existing Agents and queued jobs remain system resources (NULL owner).
ALTER TABLE agents ADD COLUMN owner_user_id VARCHAR(191);
ALTER TABLE agents ADD CONSTRAINT fk_agents_owner FOREIGN KEY (owner_user_id) REFERENCES users(user_id) ON DELETE RESTRICT;
CREATE INDEX idx_agents_owner ON agents(owner_user_id);
ALTER TABLE agent_jobs ADD COLUMN requested_by_user_id VARCHAR(191);
ALTER TABLE agent_jobs ADD COLUMN project_id VARCHAR(191);
CREATE INDEX idx_agent_jobs_requester ON agent_jobs(status,requested_by_user_id,created_at);
