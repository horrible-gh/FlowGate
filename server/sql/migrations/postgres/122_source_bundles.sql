CREATE TABLE IF NOT EXISTS source_bundles (
 bundle_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, group_id TEXT NOT NULL,
 scope TEXT NOT NULL DEFAULT 'whole_source' CHECK(scope='whole_source'),
 source_revision TEXT, source_dirty BOOLEAN NOT NULL DEFAULT FALSE,
 content_fingerprint TEXT, bundle_sha256 TEXT,
 exclusion_policy_version TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('building','created','failed','deleted')),
 file_count BIGINT, byte_size BIGINT, created_at TEXT, expires_at TEXT,
 deleted_at TEXT, failure_code TEXT, failure_reason TEXT, started_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_source_bundles_reuse ON source_bundles(project_id,group_id,exclusion_policy_version,status,source_revision,content_fingerprint);
CREATE TABLE IF NOT EXISTS source_bundle_builds (
 project_id TEXT NOT NULL, group_id TEXT NOT NULL, exclusion_policy_version TEXT NOT NULL,
 owner_id TEXT NOT NULL, bundle_id TEXT NOT NULL, lease_until TEXT NOT NULL,
 outcome TEXT NOT NULL CHECK(outcome IN ('building','created','failed')),
 failure_code TEXT, PRIMARY KEY(project_id,group_id,exclusion_policy_version)
);
CREATE TABLE IF NOT EXISTS source_bundle_usages (
 usage_id TEXT PRIMARY KEY, bundle_id TEXT NOT NULL REFERENCES source_bundles(bundle_id),
 run_id TEXT, token_id TEXT, document_id TEXT, operation TEXT NOT NULL,
 success BOOLEAN NOT NULL, current_worktree_claim BOOLEAN NOT NULL DEFAULT FALSE,
 detail TEXT, used_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_source_bundle_usages_run ON source_bundle_usages(run_id,used_at);