-- 037_rejection_id_backfill.sql
-- P0005 / T0006 — AI rejection-response record & sync.
--
-- One-time deterministic backfill: assign a stable rejection_id to every
-- pre-existing rejection_history item that lacks one, and initialise the four
-- new response fields to NULL. No new table is created — the data model is a
-- pure JSON key extension of documents.rejection_history.
--
-- Backfill id rule (matches workflow.rejection_identity.legacy_rejection_id):
--   rejection_id = 'rej_legacy_' || <rejected_at digits, 14, right-padded '0'> || '_' || <array index>
-- The digit string strips the ISO separators ( - T : + space . ) and any
-- trailing tz/'Z' is dropped by the substr(...,1,14) cut.
--
-- Idempotent: COALESCE keeps any rejection_id already present, and json_extract
-- preserves response fields that already exist, so re-running is a no-op.

BEGIN;

UPDATE documents
SET rejection_history = (
    SELECT json_group_array(
        json_object(
            'rejection_id',
                COALESCE(
                    json_extract(value, '$.rejection_id'),
                    'rej_legacy_'
                    || substr(
                        replace(replace(replace(replace(replace(replace(
                            json_extract(value, '$.rejected_at'),
                            '-', ''), 'T', ''), ':', ''), '+', ''), ' ', ''), '.', '')
                        || '00000000000000',
                        1, 14)
                    || '_' || key
                ),
            'reason',                json_extract(value, '$.reason'),
            'rejected_at',           json_extract(value, '$.rejected_at'),
            'rejected_by',           json_extract(value, '$.rejected_by'),
            'ai_response',           json_extract(value, '$.ai_response'),
            'responded_at',          json_extract(value, '$.responded_at'),
            'response_recorded_by',  json_extract(value, '$.response_recorded_by'),
            'response_revision_no',  json_extract(value, '$.response_revision_no')
        )
    )
    FROM json_each(documents.rejection_history)
)
WHERE rejection_history IS NOT NULL
  AND rejection_history != '[]'
  AND json_valid(rejection_history)
  AND json_array_length(rejection_history) > 0;

COMMIT;
