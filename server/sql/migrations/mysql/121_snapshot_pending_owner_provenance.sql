-- Resolve historic duplicates before installing the generated-column unique guard.
UPDATE snapshot_requests AS sr
JOIN snapshot_requests AS older
 ON older.project_id=sr.project_id AND older.group_id=sr.group_id
 AND COALESCE(NULLIF(older.chain_id,''),older.run_id)=COALESCE(NULLIF(sr.chain_id,''),sr.run_id)
 AND older.status='requested'
 AND (older.requested_at<sr.requested_at OR
      (older.requested_at=sr.requested_at AND older.snapshot_id<sr.snapshot_id))
SET sr.status='rejected', sr.rejected_at=sr.requested_at,
    sr.rejection_reason='Superseded by an older pending snapshot request'
WHERE sr.status='requested';
ALTER TABLE snapshot_requests ADD COLUMN pending_owner_key VARCHAR(191)
 GENERATED ALWAYS AS (CASE WHEN status='requested' THEN COALESCE(NULLIF(chain_id,''),run_id) ELSE NULL END) STORED;
CREATE UNIQUE INDEX uq_snapshot_pending_owner
 ON snapshot_requests(project_id,group_id,pending_owner_key);
ALTER TABLE snapshot_requests ADD COLUMN requested_provider_id VARCHAR(191) NULL;
ALTER TABLE snapshot_requests ADD COLUMN actual_provider_name VARCHAR(255) NULL;
ALTER TABLE snapshot_requests ADD COLUMN provider_source VARCHAR(64) NULL;
ALTER TABLE snapshot_requests ADD COLUMN attempt_no INTEGER NULL;
ALTER TABLE snapshot_requests ADD COLUMN fallback_used BOOLEAN NULL;
