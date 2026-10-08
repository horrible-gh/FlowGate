-- 141_tr_self_check_draft_owner.sql
-- flowgate.default.0638 T#1 (0003-NR §4 option A): a TR(new) worker may run Self-check before
-- its TR document exists. Such a run is owned by the issuing TR(new) token instead of a TR row,
-- and is linked to the TR when that token registers it.
--
--   tr_doc_id       NOT NULL is relaxed. NULL means "draft": the TR is not registered yet. The
--                   documents FK and its ON DELETE CASCADE keep their meaning for linked rows.
--   owner_token_id  the TR(new) token that started the draft run (tokens.token_id). No FK: a
--                   token row may be purged later and must not take linked TR evidence with it.
--   draft_doc_ref   that token's doc_ref (the workflow sequence owner) at start time, for audit.
--   linked_at       when the draft run was attached to the registered TR.
--
-- A row is either bound to a TR or fully owned by a draft token (CHECK). Existing rows all
-- carry tr_doc_id, so nothing is backfilled.
ALTER TABLE tr_self_check_runs ALTER COLUMN tr_doc_id DROP NOT NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN owner_token_id TEXT NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN draft_doc_ref TEXT NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN linked_at TEXT NULL;
ALTER TABLE tr_self_check_runs ADD CONSTRAINT ck_selfcheck_owner
    CHECK (tr_doc_id IS NOT NULL OR (owner_token_id IS NOT NULL AND draft_doc_ref IS NOT NULL));

CREATE INDEX idx_selfcheck_owner_created ON tr_self_check_runs (owner_token_id, created_at DESC, self_check_run_id DESC);
