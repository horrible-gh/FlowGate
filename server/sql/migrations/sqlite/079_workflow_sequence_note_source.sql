-- 079_workflow_sequence_note_source.sql
-- flowgate.default.0399 DB0012 §2·§3 (D0010 §3.4 / L0011 §2.1): a sequence row had nowhere
-- to keep the step note, so a row poured from a work plan lost its note and its origin the
-- moment it was saved. Three additive columns fix that:
--   note                — the one-line step note that travels WITH the row (never the row number)
--   source_doc_id       — which work plan poured this row (NULL = a row a person added)
--   source_revision_no  — that plan's revision at pour time; never rewritten when the plan changes
--
-- Additive only: ADD COLUMN x3. No backfill — pre-migration rows read note='' /
-- source_doc_id=NULL, which is exactly "there is no record that this row came from a plan"
-- (L0011 §2.1 origin_of_loaded_row reads a NULL source as "manual").
--
-- DB0012 §5 불변식 2 (source_revision_no IS NULL OR source_doc_id IS NOT NULL) is a CHECK in
-- the postgres/mysql dialects. SQLite has no ALTER TABLE ... ADD CONSTRAINT, and the only way
-- to add one is the full table rebuild 033 had to do — which would drop and recreate a table
-- that other rows point at, for an invariant no write path can reach: the API layer rejects
-- that pair before insert (P0013 ② invalid_sequence_item, 422). The rebuild is not worth the
-- risk here, so this dialect enforces the invariant one layer up.

BEGIN;

ALTER TABLE workflow_sequence_items
    ADD COLUMN note TEXT NOT NULL DEFAULT '';

ALTER TABLE workflow_sequence_items
    ADD COLUMN source_doc_id TEXT DEFAULT NULL
        REFERENCES documents(doc_id) ON DELETE SET NULL;

ALTER TABLE workflow_sequence_items
    ADD COLUMN source_revision_no INTEGER DEFAULT NULL;

COMMIT;
