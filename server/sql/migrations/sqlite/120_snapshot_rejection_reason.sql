-- flowgate.default.0517 T0026: the human's reason for rejecting a snapshot request.
-- NULL for requests that were never rejected and for lifecycle closes (owner run/group
-- finished), which are system rejections with no human text.
ALTER TABLE snapshot_requests ADD COLUMN rejection_reason TEXT NULL;

