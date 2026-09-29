CREATE TABLE agent_jobs (
 job_id VARCHAR(96) NOT NULL,
 attempt_id VARCHAR(96) NOT NULL,
 execution_type VARCHAR(32) NOT NULL CHECK(execution_type='remote_cli'),
 command_line LONGTEXT NOT NULL,
 stdin_text LONGTEXT NOT NULL,
 cwd TEXT NOT NULL,
 timeout_seconds INTEGER NOT NULL CHECK(timeout_seconds>0),
 status VARCHAR(16) NOT NULL CHECK(status IN('NEW','CLAIMED','STARTED','DONE','FAILED','CANCELLED','TIMED_OUT','REJECTED')),
 target_agent_id VARCHAR(96),
 payload_digest CHAR(64) NOT NULL,
 lease_owner_agent_id VARCHAR(96),
 lease_token VARCHAR(96),
 lease_expires_at VARCHAR(40),
 cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
 child_exit_code INTEGER,
 stdout LONGTEXT,
 stderr LONGTEXT,
 stdout_truncated BOOLEAN NOT NULL DEFAULT FALSE,
 stderr_truncated BOOLEAN NOT NULL DEFAULT FALSE,
 diagnostics LONGTEXT,
 end_reason TEXT,
 duration_seconds DOUBLE,
 reported_at VARCHAR(40),
 result_digest CHAR(64),
 created_at VARCHAR(40) NOT NULL,
 claimed_at VARCHAR(40),
 started_at VARCHAR(40),
 finished_at VARCHAR(40),
 updated_at VARCHAR(40) NOT NULL,
 PRIMARY KEY(job_id,attempt_id),
 CHECK((lease_owner_agent_id IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL)
       OR(lease_owner_agent_id IS NOT NULL AND lease_token IS NOT NULL))
);
CREATE INDEX idx_agent_jobs_claim ON agent_jobs(status,target_agent_id,created_at);
CREATE INDEX idx_agent_jobs_lease ON agent_jobs(status,lease_expires_at);
ALTER TABLE agent_jobs ADD active_job_id VARCHAR(96) GENERATED ALWAYS AS
 (CASE WHEN status IN('NEW','CLAIMED','STARTED') THEN job_id ELSE NULL END) STORED,
 ADD UNIQUE KEY uq_agent_jobs_active(active_job_id);
