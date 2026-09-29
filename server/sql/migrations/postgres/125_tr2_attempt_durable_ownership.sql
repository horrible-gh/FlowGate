-- 125_tr2_attempt_durable_ownership.sql
-- flowgate.default.0641 D0004 rev2: the approval-attempt journal is durable
-- provenance/ownership, not a child whose lifetime follows documents.
BEGIN;

ALTER TABLE tr2_approval_attempts
    DROP CONSTRAINT IF EXISTS tr2_approval_attempts_tr2_doc_id_fkey;

ALTER TABLE tr2_approval_attempts
    DROP CONSTRAINT IF EXISTS tr2_approval_attempts_ledger_row_id_fkey;

ALTER TABLE tr2_approval_attempts
    ADD CONSTRAINT tr2_approval_attempts_ledger_row_id_fkey
    FOREIGN KEY (ledger_row_id) REFERENCES tr_commit_ledger(id) ON DELETE SET NULL;

COMMIT;
