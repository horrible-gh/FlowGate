-- 009_groups_soft_delete.sql
-- D019: add the deleted_at column for soft delete to the groups table
-- Execute only after the Python runner checks whether the column exists (idempotency guaranteed)

ALTER TABLE groups ADD COLUMN deleted_at VARCHAR(191);
-- Index for query performance
CREATE INDEX IF NOT EXISTS idx_groups_deleted ON groups(deleted_at);