CREATE TABLE IF NOT EXISTS source_bundles (
 bundle_id VARCHAR(64) PRIMARY KEY, project_id VARCHAR(191) NOT NULL, group_id VARCHAR(191) NOT NULL,
 scope VARCHAR(32) NOT NULL DEFAULT 'whole_source',
 source_revision VARCHAR(64), source_dirty BOOLEAN NOT NULL DEFAULT FALSE,
 content_fingerprint VARCHAR(64), bundle_sha256 VARCHAR(64),
 exclusion_policy_version VARCHAR(64) NOT NULL,
 status VARCHAR(16) NOT NULL,
 file_count BIGINT, byte_size BIGINT, created_at VARCHAR(40), expires_at VARCHAR(40),
 deleted_at VARCHAR(40), failure_code VARCHAR(64), failure_reason TEXT, started_at VARCHAR(40) NOT NULL,
 CHECK(scope='whole_source'), CHECK(status IN ('building','created','failed','deleted')),
 INDEX idx_source_bundles_reuse(project_id,group_id,exclusion_policy_version,status,source_revision,content_fingerprint)
);
CREATE TABLE IF NOT EXISTS source_bundle_builds (
 project_id VARCHAR(191) NOT NULL, group_id VARCHAR(191) NOT NULL, exclusion_policy_version VARCHAR(64) NOT NULL,
 owner_id VARCHAR(64) NOT NULL, bundle_id VARCHAR(64) NOT NULL, lease_until VARCHAR(40) NOT NULL,
 outcome VARCHAR(16) NOT NULL, failure_code VARCHAR(64),
 PRIMARY KEY(project_id,group_id,exclusion_policy_version),
 CHECK(outcome IN ('building','created','failed'))
);
CREATE TABLE IF NOT EXISTS source_bundle_usages (
 usage_id VARCHAR(64) PRIMARY KEY, bundle_id VARCHAR(64) NOT NULL,
 run_id VARCHAR(191), token_id VARCHAR(191), document_id VARCHAR(191), operation VARCHAR(64) NOT NULL,
 success BOOLEAN NOT NULL, current_worktree_claim BOOLEAN NOT NULL DEFAULT FALSE,
 detail TEXT, used_at VARCHAR(40) NOT NULL,
 FOREIGN KEY(bundle_id) REFERENCES source_bundles(bundle_id),
 INDEX idx_source_bundle_usages_run(run_id,used_at)
);