-- 034_t824_backfill_null_doc_review_status.sql
-- T824: Backfill legacy NULL doc_review_status rows to 'pending_review'
-- Date: 2026-05-29
-- Purpose: Set doc_review_status = 'pending_review' for all legacy documents that
--          were created before T820/T823 blocked NULL on new creations, per PM policy:
--          "A worker submitted or created a document -> pending review" (NR152 / T823 follow-up).
--          Cited enum value 'pending_review' is valid per CHECK constraint in migration 027.
--
-- Rollback: NOT PROVIDED (intentional).
--   Converting 'pending_review' back to NULL would be lossy: we cannot reconstruct which
--   rows were originally NULL vs. already 'pending_review'. Take a DB backup before applying
--   if a rollback path is required:
--     cp server/flowgate.db server/flowgate.db.pre_034_backup

-- ── Policy basis ──────────────────────────────────────────────────────────────
-- M026 §8-1: Worker-created or worker-submitted documents must enter 'pending_review'
--   users must be able to [approve] or [reject] them.
--   Exception: type_code='R' with workflow not yet decided stays NULL (shown as [—]).
-- DB004 §6.1: doc_review_status is a single-writer field owned by the review subsystem.
-- DB004 §7 Q-2: One-shot backfill migrations are the recognized exception to the
--   single-writer rule — runtime API paths must not set this field in bulk — a migration is
--   the correct mechanism.

-- ── Backfill ──────────────────────────────────────────────────────────────────
UPDATE documents
SET    doc_review_status = 'pending_review',
       updated_at        = to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
WHERE  (doc_review_status IS NULL OR doc_review_status = '')
  AND  type_code != 'M'   -- belt-and-suspenders: M rows already handled by migration 033
  AND  (
           type_code != 'R'
        OR (
               type_code = 'R'
           AND workflow_steps IS NOT NULL
           AND workflow_steps != '[]'
           AND workflow_steps != ''
           )
       );
-- ── Verification (run after applying — should return 0) ────────────────────────
-- SELECT COUNT(*) FROM documents
-- WHERE (doc_review_status IS NULL OR doc_review_status = '')
--   AND type_code != 'R'
-- (R + workflow-undecided rows may still legitimately have NULL — these are excluded.)