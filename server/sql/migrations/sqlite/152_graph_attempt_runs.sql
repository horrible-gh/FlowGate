-- 152: durable AI executions, separate from node attempts. Status is an open key.
BEGIN;
CREATE TABLE IF NOT EXISTS graph_attempt_runs (
    exec_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    lease_token TEXT,
    lease_expires_at TEXT,
    ai_run_id TEXT,
    cancel_requested_at TEXT,
    created_at TEXT NOT NULL,
    ended_at TEXT,
    -- A changed value even for same-timestamp renewals (MySQL affected-row CAS).
    lease_touch_seq INTEGER NOT NULL DEFAULT 0,
    request_json TEXT NOT NULL,
    FOREIGN KEY (attempt_id) REFERENCES graph_node_attempts(attempt_id) ON DELETE CASCADE,
    FOREIGN KEY (run_id) REFERENCES graph_runs(run_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_graph_exec_active ON graph_attempt_runs(attempt_id)
    WHERE status IN ('requested', 'running');
CREATE INDEX IF NOT EXISTS idx_graph_exec_run ON graph_attempt_runs(run_id, status);
CREATE INDEX IF NOT EXISTS idx_graph_exec_ai ON graph_attempt_runs(ai_run_id);
CREATE TRIGGER IF NOT EXISTS trg_graph_exec_attempt_deleted AFTER DELETE ON graph_node_attempts BEGIN DELETE FROM graph_attempt_runs WHERE attempt_id = OLD.attempt_id; END;
COMMIT;
