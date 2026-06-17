-- 031_rejection_history.sql
-- T574 — add a cumulative rejection-reason history column to the documents table

ALTER TABLE documents ADD COLUMN rejection_history TEXT NOT NULL DEFAULT '[]';
-- Backfill rows with an existing rejection reason: convert the current rejection_reason into a one-entry history
UPDATE documents
SET rejection_history = (json_build_array(
    json_build_object(
        'reason',      rejection_reason,
        'rejected_at', COALESCE(updated_at, created_at),
        'rejected_by', NULL
    )
))::text
WHERE rejection_reason IS NOT NULL AND rejection_reason != '';