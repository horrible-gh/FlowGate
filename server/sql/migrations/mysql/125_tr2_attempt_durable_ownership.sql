-- 125_tr2_attempt_durable_ownership.sql
-- flowgate.default.0641 D0004 rev2: preserve succeeded approval attempts when
-- their TR2 document/history ledger is removed.
ALTER TABLE tr2_approval_attempts
    DROP FOREIGN KEY fk_tr2_attempt_document,
    DROP FOREIGN KEY fk_tr2_attempt_ledger;

ALTER TABLE tr2_approval_attempts
    ADD CONSTRAINT fk_tr2_attempt_ledger
    FOREIGN KEY (ledger_row_id) REFERENCES tr_commit_ledger(id) ON DELETE SET NULL;
