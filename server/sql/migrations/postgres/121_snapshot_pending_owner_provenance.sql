-- Resolve historic duplicates before installing the partial unique guard.
UPDATE snapshot_requests AS sr
SET status='rejected', rejected_at=sr.requested_at,
    rejection_reason='Superseded by an older pending snapshot request'
FROM snapshot_requests AS older
WHERE sr.status='requested' AND older.status='requested'
 AND older.project_id=sr.project_id AND older.group_id=sr.group_id
 AND COALESCE(NULLIF(older.chain_id,''),older.run_id)=COALESCE(NULLIF(sr.chain_id,''),sr.run_id)
 AND (older.requested_at<sr.requested_at OR
      (older.requested_at=sr.requested_at AND older.snapshot_id<sr.snapshot_id));
CREATE UNIQUE INDEX uq_snapshot_pending_owner
 ON snapshot_requests(project_id,group_id,COALESCE(NULLIF(chain_id,''),run_id))
 WHERE status='requested';
ALTER TABLE snapshot_requests ADD COLUMN requested_provider_id TEXT NULL;
ALTER TABLE snapshot_requests ADD COLUMN actual_provider_name TEXT NULL;
ALTER TABLE snapshot_requests ADD COLUMN provider_source TEXT NULL;
ALTER TABLE snapshot_requests ADD COLUMN attempt_no INTEGER NULL;
ALTER TABLE snapshot_requests ADD COLUMN fallback_used BOOLEAN NULL;
