-- 081_document_origin_snapshot.sql
-- flowgate.default.0410 NR0003/WP0005: nullable snapshot of the AI provider (if any)
-- that authored a document, plus the AI invoke run it came from. Additive only; no
-- backfill, no provider FK (the display name is a point-in-time snapshot, not a live
-- lookup — a renamed/deleted ai_providers row must not change what was shown at the
-- time the document was created). Old rows stay NULL/NULL and are rendered as unknown.

BEGIN;

ALTER TABLE documents
    ADD COLUMN origin_provider_name TEXT DEFAULT NULL;

ALTER TABLE documents
    ADD COLUMN origin_ai_run_id TEXT DEFAULT NULL;

COMMIT;
