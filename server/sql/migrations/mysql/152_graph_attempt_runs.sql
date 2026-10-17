-- 152: durable AI executions, separate from node attempts. Status is an open key.
CREATE TABLE IF NOT EXISTS graph_attempt_runs (
    exec_id VARCHAR(64) PRIMARY KEY,
    attempt_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    kind VARCHAR(16) NOT NULL,
    status VARCHAR(32) NOT NULL,
    lease_token VARCHAR(64),
    lease_expires_at VARCHAR(40),
    ai_run_id VARCHAR(64),
    cancel_requested_at VARCHAR(40),
    created_at VARCHAR(40) NOT NULL,
    ended_at VARCHAR(40),
    -- A changed value even for same-timestamp renewals (MySQL affected-row CAS).
    lease_touch_seq INTEGER NOT NULL DEFAULT 0,
    request_json LONGTEXT NOT NULL,
    active_attempt_id VARCHAR(64) GENERATED ALWAYS AS
        (CASE WHEN status IN ('requested', 'running') THEN attempt_id ELSE NULL END) STORED,
    UNIQUE KEY idx_graph_exec_active (active_attempt_id),
    INDEX idx_graph_exec_run (run_id, status),
    INDEX idx_graph_exec_ai (ai_run_id),
    FOREIGN KEY (attempt_id) REFERENCES graph_node_attempts(attempt_id) ON DELETE CASCADE,
    FOREIGN KEY (run_id) REFERENCES graph_runs(run_id) ON DELETE CASCADE
);
