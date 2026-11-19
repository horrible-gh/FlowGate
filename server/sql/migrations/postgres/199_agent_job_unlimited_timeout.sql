-- Preserve the original positive timeout constraint and all existing finite jobs.
-- Unlimited jobs store a positive placeholder and are sent to the Agent with timeout_seconds=0.
ALTER TABLE agent_jobs ADD COLUMN timeout_unlimited BOOLEAN NOT NULL DEFAULT FALSE;
