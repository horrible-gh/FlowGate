-- 086_tr_commit_ledger.sql
-- flowgate.default.0332 DB0008 §2·§3 (D0005 K9 / P0006 §1·§2 / L0007 §2.2·§3):
-- the TR commit ledger. Until now a TR approval left git untouched — the group's whole work
-- landed in one absorb commit at finalize time, so nothing could say which commit a TR made
-- (NR0010 §1-1: `tr_commit_ledger|tr_commit_cancel` matched 0 rows of implementation).
-- `tr_commit_ledger` is the one persistent store the approval hook writes and the workflow
-- strip / Git status panel / time-machine preview read.
--
-- One row per TR APPROVAL ROUND, never per document: a TR that was rewound and approved again
-- gets a second row, and which one is live is read off `state` (D0005 K9). Rows are never
-- deleted — a cancel writes `state='canceled'` onto the row it cancels (D0005 K5: FlowGate is
-- a time machine, not an eraser). The only deletion path is the CASCADE from documents/groups
-- disappearing, which is the parent fact, not the ledger erasing itself (DB0008 §5-1).
--
-- `group_id` references `groups`, NOT `group_git_state`: a git-inactive group still gets a
-- `no_commit` row (skip_reason='git_inactive'), and those groups have no git-state row at all
-- (DB0008 §2-1 / §5-2).
--
-- No `seq` / `doc_code` columns on purpose — both are derived from `documents` and seq moves
-- when the workflow is edited, so a stored copy would point at another document later
-- (D0005 K9). Every read joins `documents` for the value of the moment (DB0008 §4-6).
--
-- The three `group_git_state` columns are the group-level "last time the cancel gate refused"
-- diagnosis (L0007 §4.1 blocked_reason + its sub-reason). They follow the
-- provision_error/provision_failed_at convention of 061 — last failure only, no history.
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
-- Everything here is `IF NOT EXISTS`, so re-application on an already-migrated DB is a no-op
-- wherever the file ends up in the order (DB0008 §3-1/§3-4).
--
-- Additive only. Rollback is `DROP TABLE tr_commit_ledger` plus the three DROP COLUMNs
-- (DB0008 §3-2); nothing references the new table.

BEGIN;

CREATE TABLE IF NOT EXISTS tr_commit_ledger (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id           TEXT    NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    doc_id             TEXT    NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    state              TEXT    NOT NULL DEFAULT 'no_commit'
        CHECK (state IN ('no_commit','live','canceled')),
    commit_sha         TEXT,
    commit_subject     TEXT,
    skip_reason        TEXT
        CHECK (skip_reason IS NULL OR skip_reason IN
            ('no_changes','artifacts_only','git_inactive','no_worktree','git_busy','commit_failed')),
    cancel_commit      TEXT,
    cancel_reason      TEXT CHECK (cancel_reason IS NULL OR cancel_reason = 'empty_revert'),
    canceled_at        TEXT,
    cancel_attempt_log TEXT NOT NULL DEFAULT '[]',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    CHECK ((commit_sha IS NULL) = (state = 'no_commit')),
    CHECK (skip_reason    IS NULL OR state = 'no_commit'),
    CHECK (cancel_commit  IS NULL OR state = 'canceled'),
    CHECK (cancel_reason  IS NULL OR state = 'canceled'),
    CHECK (canceled_at    IS NULL OR state = 'canceled')
);

CREATE INDEX IF NOT EXISTS idx_trl_group ON tr_commit_ledger(group_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_trl_doc   ON tr_commit_ledger(doc_id, id DESC);

ALTER TABLE group_git_state ADD COLUMN last_cancel_block_reason TEXT;
ALTER TABLE group_git_state ADD COLUMN last_cancel_block_sub    TEXT;
ALTER TABLE group_git_state ADD COLUMN last_cancel_block_at     TEXT;

COMMIT;
