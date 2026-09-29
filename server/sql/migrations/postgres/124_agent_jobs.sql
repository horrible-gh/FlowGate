CREATE TABLE agent_jobs (
 job_id TEXT NOT NULL,
 attempt_id TEXT NOT NULL,
 execution_type TEXT NOT NULL CHECK(execution_type='remote_cli'),
 command_line TEXT NOT NULL,
 stdin_text TEXT NOT NULL,
 cwd TEXT NOT NULL,
 timeout_seconds INTEGER NOT NULL CHECK(timeout_seconds>0),
 status TEXT NOT NULL CHECK(status IN('NEW','CLAIMED','STARTED','DONE','FAILED','CANCELLED','TIMED_OUT','REJECTED')),
 target_agent_id TEXT,
 payload_digest CHAR(64) NOT NULL,
 lease_owner_agent_id TEXT,
 lease_token TEXT,
 lease_expires_at TEXT,
 cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
 child_exit_code INTEGER,
 stdout TEXT,
 stderr TEXT,
 stdout_truncated BOOLEAN NOT NULL DEFAULT FALSE,
 stderr_truncated BOOLEAN NOT NULL DEFAULT FALSE,
 diagnostics TEXT,
 end_reason TEXT,
 duration_seconds DOUBLE PRECISION,
 reported_at TEXT,
 result_digest CHAR(64),
 created_at TEXT NOT NULL,
 claimed_at TEXT,
 started_at TEXT,
 finished_at TEXT,
 updated_at TEXT NOT NULL,
 PRIMARY KEY(job_id,attempt_id),
 CHECK((lease_owner_agent_id IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL)
       OR(lease_owner_agent_id IS NOT NULL AND lease_token IS NOT NULL))
);
CREATE INDEX idx_agent_jobs_claim ON agent_jobs(status,target_agent_id,created_at);
CREATE INDEX idx_agent_jobs_lease ON agent_jobs(status,lease_expires_at);
CREATE UNIQUE INDEX uq_agent_jobs_active ON agent_jobs(job_id) WHERE status IN('NEW','CLAIMED','STARTED');
