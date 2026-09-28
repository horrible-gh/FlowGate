CREATE TABLE IF NOT EXISTS snapshot_usages (
 usage_id TEXT PRIMARY KEY,
 snapshot_id TEXT NOT NULL,
 run_id TEXT NOT NULL,
 token_id TEXT NOT NULL,
 access_kind TEXT NOT NULL CHECK (access_kind IN ('access','execution')),
 operation TEXT NOT NULL,
 task_kind TEXT NULL,
 success INTEGER NOT NULL DEFAULT 1 CHECK (success IN (0,1)),
 stale_at_use INTEGER NOT NULL DEFAULT 0 CHECK (stale_at_use IN (0,1)),
 current_worktree_claim INTEGER NOT NULL DEFAULT 0 CHECK (current_worktree_claim IN (0,1)),
 detail TEXT NULL,
 used_at TEXT NOT NULL,
 document_id TEXT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshot_usages_run ON snapshot_usages(run_id, used_at);
CREATE INDEX IF NOT EXISTS idx_snapshot_usages_snapshot ON snapshot_usages(snapshot_id, used_at);
CREATE INDEX IF NOT EXISTS idx_snapshot_usages_document ON snapshot_usages(document_id, used_at);