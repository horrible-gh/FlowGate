ALTER TABLE snapshot_requests ADD COLUMN created_at TEXT NULL;
ALTER TABLE snapshot_requests ADD COLUMN expires_at TEXT NULL;
ALTER TABLE snapshot_requests ADD COLUMN deleted_at TEXT NULL;
ALTER TABLE snapshot_requests ADD COLUMN stale INTEGER NOT NULL DEFAULT 0 CHECK (stale IN (0,1));
ALTER TABLE snapshot_requests ADD COLUMN stale_detected_at TEXT NULL;
ALTER TABLE snapshot_requests ADD COLUMN cleanup_failed INTEGER NOT NULL DEFAULT 0 CHECK (cleanup_failed IN (0,1));
ALTER TABLE snapshot_requests ADD COLUMN cleanup_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE snapshot_requests ADD COLUMN cleanup_last_error TEXT NULL;
ALTER TABLE snapshot_requests ADD COLUMN cleanup_next_at TEXT NULL;
ALTER TABLE snapshot_requests ADD COLUMN failure_code TEXT NULL;
ALTER TABLE snapshot_requests ADD COLUMN failure_reason TEXT NULL;
ALTER TABLE snapshot_requests ADD COLUMN copied_file_count INTEGER NULL;
ALTER TABLE snapshot_requests ADD COLUMN copied_byte_size INTEGER NULL;
CREATE INDEX IF NOT EXISTS idx_snapshot_requests_cleanup
 ON snapshot_requests(status, expires_at, cleanup_next_at);
