ALTER TABLE source_bundles ADD COLUMN metrics_json TEXT;
ALTER TABLE source_bundles ADD COLUMN cleanup_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE source_bundles ADD COLUMN cleanup_last_error TEXT;
ALTER TABLE source_bundles ADD COLUMN cleanup_last_at TEXT;
CREATE TABLE IF NOT EXISTS source_bundle_scratches (
 scratch_key TEXT PRIMARY KEY, bundle_id TEXT NOT NULL REFERENCES source_bundles(bundle_id),
 run_id TEXT NOT NULL, token_id TEXT, status TEXT NOT NULL CHECK(status IN ('created','deleted')),
 created_at TEXT NOT NULL, expires_at TEXT NOT NULL, deleted_at TEXT,
 byte_size INTEGER NOT NULL, build_duration_ms INTEGER NOT NULL, reuse_count INTEGER NOT NULL DEFAULT 0,
 cleanup_attempts INTEGER NOT NULL DEFAULT 0, cleanup_last_error TEXT, cleanup_last_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_source_bundle_scratches_run ON source_bundle_scratches(run_id,status);
CREATE INDEX IF NOT EXISTS idx_source_bundle_scratches_token ON source_bundle_scratches(token_id,status);
