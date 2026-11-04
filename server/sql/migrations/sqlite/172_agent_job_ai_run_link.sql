-- AI-run Job link and lifecycle fields (flowgate.v02.0129 T#2). Existing Jobs keep NULL
-- in every new column and therefore keep their current claim/expiry/report behaviour.
ALTER TABLE agent_jobs ADD COLUMN ai_run_id TEXT;
ALTER TABLE agent_jobs ADD COLUMN graph_exec_id TEXT;
ALTER TABLE agent_jobs ADD COLUMN ai_group_id TEXT;
ALTER TABLE agent_jobs ADD COLUMN ai_token_id TEXT;
ALTER TABLE agent_jobs ADD COLUMN cancel_requested_at TEXT;
ALTER TABLE agent_jobs ADD COLUMN terminal_cause TEXT;
ALTER TABLE agent_jobs ADD COLUMN retrieval_state TEXT CHECK(retrieval_state IS NULL OR retrieval_state IN('pending','collected','handed_over'));
ALTER TABLE agent_jobs ADD COLUMN retrieval_owner TEXT;
ALTER TABLE agent_jobs ADD COLUMN retrieval_deadline_at TEXT;
ALTER TABLE agent_jobs ADD COLUMN fenced_at TEXT;
ALTER TABLE agent_jobs ADD COLUMN scrubbed_at TEXT;
CREATE INDEX idx_agent_jobs_ai_run ON agent_jobs(ai_run_id);
CREATE INDEX idx_agent_jobs_ai_graph_exec ON agent_jobs(graph_exec_id);
-- The run record keeps the remote Job identity (target / claiming Agent, job_id/attempt_id).
ALTER TABLE ai_invoke_runs ADD COLUMN remote_job_json TEXT;
-- Restart recovery hold of a run whose remote Job was not settled (D0005 §3-4 step 2~5).
CREATE TABLE ai_remote_recovery_holds (
 run_id TEXT PRIMARY KEY,
 group_id TEXT,
 project_id TEXT,
 graph_exec_id TEXT,
 token_id TEXT,
 held_at TEXT NOT NULL,
 token_revoked_at TEXT,
 resumed_at TEXT,
 last_error TEXT,
 updated_at TEXT NOT NULL
);
