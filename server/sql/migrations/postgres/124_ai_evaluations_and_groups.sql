CREATE TABLE ai_evaluation_runs (
    evaluation_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    project_run_seq BIGINT NOT NULL CHECK (project_run_seq > 0),
    provider_id TEXT NOT NULL,
    provider_name_snapshot TEXT,
    subject_fingerprint CHAR(64) NOT NULL,
    subject_fingerprint_version INTEGER NOT NULL DEFAULT 1 CHECK (subject_fingerprint_version > 0),
    active_dedupe_key CHAR(64) UNIQUE,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    verdict TEXT CHECK (verdict IS NULL OR verdict IN ('suitable', 'unsuitable')),
    reason_code TEXT,
    diagnostic_summary TEXT,
    created_by TEXT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (project_id, project_run_seq)
);
CREATE INDEX idx_ai_evaluation_current ON ai_evaluation_runs(project_id, provider_id, subject_fingerprint_version, subject_fingerprint, project_run_seq DESC);
CREATE INDEX idx_ai_evaluation_provider_history ON ai_evaluation_runs(project_id, provider_id, project_run_seq DESC);
CREATE INDEX idx_ai_evaluation_project_history ON ai_evaluation_runs(project_id, project_run_seq DESC);
CREATE TABLE ai_groups (
    ai_group_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    name_key_hash CHAR(64) NOT NULL,
    description TEXT,
    work_executor_provider_id TEXT NOT NULL,
    reviewer_provider_id TEXT,
    reworker_provider_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (project_id, name_key_hash)
);
CREATE INDEX idx_ai_groups_project_updated ON ai_groups(project_id, updated_at, ai_group_id);
ALTER TABLE ai_invoke_runs ADD COLUMN requested_ai_group_id TEXT;
