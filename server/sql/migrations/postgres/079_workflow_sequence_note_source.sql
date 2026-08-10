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

ALTER TABLE workflow_sequence_items
    ADD COLUMN note TEXT NOT NULL DEFAULT '';

ALTER TABLE workflow_sequence_items
    ADD COLUMN source_doc_id TEXT DEFAULT NULL
        REFERENCES documents(doc_id) ON DELETE SET NULL;

ALTER TABLE workflow_sequence_items
    ADD COLUMN source_revision_no INTEGER DEFAULT NULL;

-- DB0012 §5 불변식 2: a revision number without the document it belongs to is meaningless.
ALTER TABLE workflow_sequence_items
    ADD CONSTRAINT ck_wfseq_items_source_revision
        CHECK (source_revision_no IS NULL OR source_doc_id IS NOT NULL);
