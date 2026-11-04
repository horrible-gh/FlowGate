-- AI-run Job link and lifecycle fields (flowgate.v02.0129 T#2). Existing Jobs keep NULL
-- in every new column and therefore keep their current claim/expiry/report behaviour.
ALTER TABLE agent_jobs ADD COLUMN ai_run_id VARCHAR(96) NULL;
ALTER TABLE agent_jobs ADD COLUMN graph_exec_id VARCHAR(191) NULL;
ALTER TABLE agent_jobs ADD COLUMN ai_group_id VARCHAR(191) NULL;
ALTER TABLE agent_jobs ADD COLUMN ai_token_id VARCHAR(191) NULL;
ALTER TABLE agent_jobs ADD COLUMN cancel_requested_at VARCHAR(40) NULL;
ALTER TABLE agent_jobs ADD COLUMN terminal_cause VARCHAR(32) NULL;
ALTER TABLE agent_jobs ADD COLUMN retrieval_state VARCHAR(16) NULL CHECK(retrieval_state IS NULL OR retrieval_state IN('pending','collected','handed_over'));
ALTER TABLE agent_jobs ADD COLUMN retrieval_owner VARCHAR(191) NULL;
ALTER TABLE agent_jobs ADD COLUMN retrieval_deadline_at VARCHAR(40) NULL;
ALTER TABLE agent_jobs ADD COLUMN fenced_at VARCHAR(40) NULL;
ALTER TABLE agent_jobs ADD COLUMN scrubbed_at VARCHAR(40) NULL;
CREATE INDEX idx_agent_jobs_ai_run ON agent_jobs(ai_run_id);
CREATE INDEX idx_agent_jobs_ai_graph_exec ON agent_jobs(graph_exec_id);
-- The run record keeps the remote Job identity (target / claiming Agent, job_id/attempt_id).
ALTER TABLE ai_invoke_runs ADD COLUMN remote_job_json LONGTEXT NULL;
-- Restart recovery hold of a run whose remote Job was not settled (D0005 §3-4 step 2~5).
CREATE TABLE ai_remote_recovery_holds (
 run_id VARCHAR(96) NOT NULL PRIMARY KEY,
 group_id VARCHAR(191) NULL,
 project_id VARCHAR(191) NULL,
 graph_exec_id VARCHAR(191) NULL,
 token_id VARCHAR(191) NULL,
 held_at VARCHAR(40) NOT NULL,
 token_revoked_at VARCHAR(40) NULL,
 resumed_at VARCHAR(40) NULL,
 last_error TEXT NULL,
 updated_at VARCHAR(40) NOT NULL
);
