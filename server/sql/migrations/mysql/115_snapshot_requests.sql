CREATE TABLE IF NOT EXISTS snapshot_requests (
 snapshot_id VARCHAR(64) PRIMARY KEY, project_id VARCHAR(191) NOT NULL, group_id VARCHAR(191) NOT NULL,
 run_id VARCHAR(191) NOT NULL, token_id VARCHAR(191) NOT NULL, provider_id VARCHAR(191) NOT NULL,
 reason TEXT NOT NULL, scope VARCHAR(32) NOT NULL, requested_paths TEXT NOT NULL, purpose TEXT NOT NULL,
 source_kind VARCHAR(32) NOT NULL DEFAULT 'current_worktree', status VARCHAR(32) NOT NULL DEFAULT 'requested',
 requested_at VARCHAR(40) NOT NULL, approved_at VARCHAR(40), approved_by VARCHAR(191),
 rejected_at VARCHAR(40), rejected_by VARCHAR(191), source_revision VARCHAR(191), source_fingerprint VARCHAR(191),
 CHECK (scope IN ('single_file','selected_files','directory','whole_source')),
 CHECK (source_kind='current_worktree'), CHECK (status IN ('requested','approved','rejected','created','failed','deleted')),
 INDEX idx_snapshot_requests_pending(project_id,group_id,status,requested_at)
);
