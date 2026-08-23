-- 086_tr_commit_ledger.sql
-- flowgate.default.0332 DB0008 §2·§3 (D0005 K9 / P0006 §1·§2 / L0007 §2.2·§3):
-- the TR commit ledger. Until now a TR approval left git untouched — the group's whole work
-- landed in one absorb commit at finalize time, so nothing could say which commit a TR made
-- (NR0010 §1-1). One row per TR APPROVAL ROUND; rows are never deleted, a cancel writes
-- `state='canceled'` onto the row it cancels (D0005 K5).
--
-- Column widths follow the attachments/group_git_state convention: VARCHAR(191) for the two
-- id columns so the composite keys stay inside InnoDB's utf8mb4 key-length budget.
--
-- MySQL has no `CREATE INDEX IF NOT EXISTS`, so both indexes are declared INSIDE the
-- `CREATE TABLE IF NOT EXISTS` as KEY clauses (the 083 convention). Same names, same columns
-- as the sqlite/postgres dialects — and the whole statement stays a no-op on a database that
-- already has the table, which a bare `CREATE INDEX` would not (DB0008 §3-4).
--
-- Two deliberate deviations from the sqlite/postgres shape, both MySQL-only and both already
-- named in DB0008 §3-4:
--   * `cancel_attempt_log` carries no column DEFAULT — MySQL rejects a literal DEFAULT on a
--     TEXT column before 8.0.13. The value is never omitted: every INSERT in
--     db/tr_commit_ledger.py writes '[]' explicitly.
--   * the three `group_git_state` ADD COLUMNs carry no `IF NOT EXISTS` — that syntax is not
--     available before 8.0.29 and the migrations ledger (filename PK) already applies this
--     file exactly once.
--
-- Numbering: this file was authored as 085 against a base whose newest migration was 083,
-- with 084 held by a parallel group. It is 086 now because origin/main has since merged both
-- 084_ai_invoke_provider_pin.sql and 085_conversation_backward_page_audit.sql in all three
-- dialects, so 085 stopped being free — and an ordinal collision is the one kind of clash a
-- merge resolution cannot repair. The repo's rule is that the later arrival takes the next
-- free ordinal rather than a letter suffix (flowgate.default.0413 T0007 moved
-- 080_workflow_sequence_provider.sql to 081 the same way). `migration_renames.RENAMES` carries
-- 085_tr_commit_ledger.sql -> 086_tr_commit_ledger.sql, which is what keeps a database that
-- already applied the old name from running this file a second time.
-- The RENAMES entry matters most on this dialect: the three `group_git_state` ADD COLUMNs
-- below carry no `IF NOT EXISTS`, so a database that re-ran this file under its new name
-- would fail on the first one.
--
-- Additive only. Rollback is `DROP TABLE tr_commit_ledger` plus the three DROP COLUMNs.

SET FOREIGN_KEY_CHECKS=0;

CREATE TABLE IF NOT EXISTS tr_commit_ledger (
    id                 INT          NOT NULL AUTO_INCREMENT,
    group_id           VARCHAR(191) NOT NULL,
    doc_id             VARCHAR(191) NOT NULL,
    state              VARCHAR(16)  NOT NULL DEFAULT 'no_commit',
    commit_sha         VARCHAR(40)  NULL,
    commit_subject     TEXT         NULL,
    skip_reason        VARCHAR(32)  NULL,
    cancel_commit      VARCHAR(40)  NULL,
    cancel_reason      VARCHAR(32)  NULL,
    canceled_at        TEXT         NULL,
    cancel_attempt_log TEXT         NOT NULL,
    created_at         TEXT         NOT NULL,
    updated_at         TEXT         NOT NULL,
    PRIMARY KEY (id),
    KEY idx_trl_group (group_id, id),
    KEY idx_trl_doc (doc_id, id),
    CONSTRAINT fk_trl_group FOREIGN KEY (group_id)
        REFERENCES groups(group_id) ON DELETE CASCADE,
    CONSTRAINT fk_trl_doc FOREIGN KEY (doc_id)
        REFERENCES documents(doc_id) ON DELETE CASCADE,
    CONSTRAINT ck_trl_state CHECK (state IN ('no_commit','live','canceled')),
    CONSTRAINT ck_trl_skip_reason CHECK (skip_reason IS NULL OR skip_reason IN
        ('no_changes','artifacts_only','git_inactive','no_worktree','git_busy','commit_failed')),
    CONSTRAINT ck_trl_cancel_reason CHECK (cancel_reason IS NULL OR cancel_reason = 'empty_revert'),
    CONSTRAINT ck_trl_commit_sha_state CHECK ((commit_sha IS NULL) = (state = 'no_commit')),
    CONSTRAINT ck_trl_skip_scope CHECK (skip_reason IS NULL OR state = 'no_commit'),
    CONSTRAINT ck_trl_cancel_commit_scope CHECK (cancel_commit IS NULL OR state = 'canceled'),
    CONSTRAINT ck_trl_cancel_reason_scope CHECK (cancel_reason IS NULL OR state = 'canceled'),
    CONSTRAINT ck_trl_canceled_at_scope CHECK (canceled_at IS NULL OR state = 'canceled')
) ROW_FORMAT=DYNAMIC;

ALTER TABLE group_git_state ADD COLUMN last_cancel_block_reason VARCHAR(20) NULL
    CHECK (last_cancel_block_reason IS NULL OR last_cancel_block_reason IN
        ('git_inactive','already_merged','no_worktree','git_busy'));
ALTER TABLE group_git_state ADD COLUMN last_cancel_block_sub VARCHAR(32) NULL
    CHECK (last_cancel_block_sub IS NULL OR last_cancel_block_sub IN
        ('integration_disabled','no_group_git_state','git_unavailable','already_merged',
         'merge_in_flight','worktree_unregistered','lock_timeout','worktree_missing',
         'dirty_worktree','commits_absent'));
ALTER TABLE group_git_state ADD COLUMN last_cancel_block_at TEXT NULL;

SET FOREIGN_KEY_CHECKS=1;
