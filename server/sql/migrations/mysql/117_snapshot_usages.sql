CREATE TABLE IF NOT EXISTS snapshot_usages (
 usage_id VARCHAR(64) PRIMARY KEY,
 snapshot_id VARCHAR(64) NOT NULL,
 run_id VARCHAR(191) NOT NULL,
 token_id VARCHAR(191) NOT NULL,
 access_kind VARCHAR(32) NOT NULL,
 operation VARCHAR(64) NOT NULL,
 task_kind VARCHAR(64) NULL,
 success BOOLEAN NOT NULL DEFAULT TRUE,
 stale_at_use BOOLEAN NOT NULL DEFAULT FALSE,
 current_worktree_claim BOOLEAN NOT NULL DEFAULT FALSE,
 detail TEXT NULL,
 used_at VARCHAR(40) NOT NULL,
 document_id VARCHAR(191) NULL,
 CHECK (access_kind IN ('access','execution')),
 INDEX idx_snapshot_usages_run(run_id, used_at),
 INDEX idx_snapshot_usages_snapshot(snapshot_id, used_at),
 INDEX idx_snapshot_usages_document(document_id, used_at)
);