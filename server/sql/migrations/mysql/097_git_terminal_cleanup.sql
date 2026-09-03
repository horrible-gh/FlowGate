CREATE TABLE IF NOT EXISTS git_terminal_cleanup_snapshots (
    project_id VARCHAR(128) PRIMARY KEY,
    last_run_at VARCHAR(64) NOT NULL,
    last_run_status VARCHAR(16) NOT NULL CHECK (last_run_status IN ('ok','partial','failed')),
    last_cleaned_count INTEGER NOT NULL DEFAULT 0,
    pending_json TEXT NOT NULL DEFAULT ('[]'),
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
