ALTER TABLE snapshot_requests ADD COLUMN chain_id TEXT NULL;
CREATE INDEX IF NOT EXISTS idx_snapshot_requests_lineage
 ON snapshot_requests(project_id, group_id, chain_id, status);
