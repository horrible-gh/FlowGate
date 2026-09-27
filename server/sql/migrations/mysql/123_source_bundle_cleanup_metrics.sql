ALTER TABLE source_bundles ADD COLUMN metrics_json TEXT;
ALTER TABLE source_bundles ADD COLUMN cleanup_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE source_bundles ADD COLUMN cleanup_last_error TEXT;
ALTER TABLE source_bundles ADD COLUMN cleanup_last_at TEXT;
CREATE TABLE IF NOT EXISTS source_bundle_scratches (
 scratch_key VARCHAR(64) PRIMARY KEY, bundle_id VARCHAR(64) NOT NULL,
 run_id VARCHAR(191) NOT NULL, token_id VARCHAR(191), status VARCHAR(16) NOT NULL,
 created_at VARCHAR(40) NOT NULL, expires_at VARCHAR(40) NOT NULL, deleted_at VARCHAR(40),
 byte_size BIGINT NOT NULL, build_duration_ms BIGINT NOT NULL, reuse_count INTEGER NOT NULL DEFAULT 0,
 cleanup_attempts INTEGER NOT NULL DEFAULT 0, cleanup_last_error TEXT, cleanup_last_at VARCHAR(40),
 FOREIGN KEY(bundle_id) REFERENCES source_bundles(bundle_id),
 CHECK(status IN ('created','deleted')),
 INDEX idx_source_bundle_scratches_run(run_id,status),
 INDEX idx_source_bundle_scratches_token(token_id,status)
);
