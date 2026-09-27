CREATE TABLE IF NOT EXISTS snapshot_requests (
 snapshot_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, group_id TEXT NOT NULL,
 run_id TEXT NOT NULL, token_id TEXT NOT NULL, provider_id TEXT NOT NULL,
 reason TEXT NOT NULL, scope TEXT NOT NULL CHECK (scope IN ('single_file','selected_files','directory','whole_source')),
 requested_paths TEXT NOT NULL, purpose TEXT NOT NULL,
 source_kind TEXT NOT NULL DEFAULT 'current_worktree' CHECK (source_kind='current_worktree'),
 status TEXT NOT NULL DEFAULT 'requested' CHECK (status IN ('requested','approved','rejected','created','failed','deleted')),
 requested_at TEXT NOT NULL, approved_at TEXT NULL, approved_by TEXT NULL,
 rejected_at TEXT NULL, rejected_by TEXT NULL, source_revision TEXT NULL, source_fingerprint TEXT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshot_requests_pending ON snapshot_requests(project_id,group_id,status,requested_at);
