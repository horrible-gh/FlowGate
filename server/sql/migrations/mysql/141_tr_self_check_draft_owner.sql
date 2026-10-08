-- 141_tr_self_check_draft_owner.sql
-- flowgate.default.0638 T#1 (0003-NR §4 option A): a TR(new) worker may run Self-check before
-- its TR document exists. Such a run is owned by the issuing TR(new) token instead of a TR row,
-- and is linked to the TR when that token registers it.
--
--   tr_doc_id       NOT NULL is relaxed. NULL means "draft": the TR is not registered yet. The
--                   fk_selfcheck_document FK and its ON DELETE CASCADE keep their meaning for
--                   linked rows (MODIFY keeps the FK; a nullable FK column is allowed).
--   owner_token_id  the TR(new) token that started the draft run (tokens.token_id). No FK: a
--                   token row may be purged later and must not take linked TR evidence with it.
--   draft_doc_ref   that token's doc_ref (the workflow sequence owner) at start time, for audit.
--   linked_at       when the draft run was attached to the registered TR.
--
-- A row is either bound to a TR or fully owned by a draft token. SQLite/PostgreSQL state that
-- as ck_selfcheck_owner; MySQL rejects a CHECK on a column used by an FK referential action
-- (ER 3823, tr_doc_id's ON DELETE CASCADE), so here the repository (create_pending) is the only
-- guard. Existing rows all carry tr_doc_id, so nothing is backfilled.
ALTER TABLE tr_self_check_runs MODIFY tr_doc_id VARCHAR(191) NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN owner_token_id VARCHAR(191) NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN draft_doc_ref VARCHAR(191) NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN linked_at VARCHAR(40) NULL;

CREATE INDEX idx_selfcheck_owner_created ON tr_self_check_runs (owner_token_id, created_at DESC, self_check_run_id DESC);
