-- 134_group_git_state_approval_freeze.sql
-- flowgate.default.0669 (unit 6a) / 0666 0010-DB §2.7·§3·§4(q) (design number 133 → 134), 0009-L §2.11.
-- Add approval-freeze claims (approval_freeze_job_id/state/at) and CAS-only touch_seq to group_git_state.
-- At most one claim per Group (structurally guaranteed by PK group_id). State: freezing (F1–F4) / publish_wait (F5 onward).
-- FK uses ON DELETE SET NULL so operational job cleanup cannot orphan a claim (DB §2.7).
-- Additive, no backfill (NULL = no freeze claim). touch_seq is NOT NULL DEFAULT 0, filling existing rows with zero.

ALTER TABLE group_git_state ADD COLUMN approval_freeze_job_id VARCHAR(30) NULL
    REFERENCES operation_job(job_id) ON DELETE SET NULL;
ALTER TABLE group_git_state ADD COLUMN approval_freeze_state VARCHAR(16) NULL
    CHECK (approval_freeze_state IS NULL OR approval_freeze_state IN ('freezing', 'publish_wait'));
ALTER TABLE group_git_state ADD COLUMN approval_freeze_at VARCHAR(25) NULL;
ALTER TABLE group_git_state ADD COLUMN touch_seq INTEGER NOT NULL DEFAULT 0;

CREATE INDEX idx_group_git_state_freeze ON group_git_state (approval_freeze_job_id);
