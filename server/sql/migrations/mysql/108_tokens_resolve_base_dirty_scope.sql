SET FOREIGN_KEY_CHECKS=0;
-- 108_tokens_resolve_base_dirty_scope.sql
-- flowgate.default.0481 T0010 rev6 (rejection 1-2): admit 'resolve_base_dirty' to the
-- tokens.action_scope allow-list.
--
-- 0482 T0011 added the project-scoped base-branch cleanup run and its token scope in code
-- (ai_invoke_routes._ALLOWED_SCOPES, remote_tool_service._exec_resolve_base_dirty, which
-- answers 403 to any token whose action_scope is not this one) but never widened this
-- CHECK, so token_service.issue() failed with a raw IntegrityError on every real press:
-- "CHECK constraint failed: action_scope IN (...)". That is not one of the exceptions the
-- start route rolls back on, so the press answered 500 (the generic "could not start the
-- base-branch AI cleanup" toast) and stranded the project admission lease it had already
-- taken, turning every retry for the next two minutes into "this project's AI cleanup is
-- already running". The feature had therefore never once run against a real database; the
-- unit suites use the in-memory store, which enforces no constraints at all.
--
-- Same shape as every earlier scope addition (042a review, 052 test_run, 062a
-- workflow_sequence_edit, 064 resolve_conflict, 075a chat): SQLite cannot alter a CHECK,
-- so the table is rebuilt with the same columns and the widened constraint.

ALTER TABLE tokens RENAME TO tokens_before_base_dirty_scope;
CREATE TABLE tokens (
    token_id VARCHAR(191) PRIMARY KEY, hash VARCHAR(191) NOT NULL UNIQUE, pepper_id TEXT NOT NULL,
    project VARCHAR(191) NOT NULL REFERENCES projects(project_id), group_id VARCHAR(191) REFERENCES groups(group_id),
    doc_ref TEXT, action_scope TEXT NOT NULL CHECK (action_scope IN ('new', 'edit', 'workflow_decide', 'review', 'test_run', 'workflow_sequence_edit', 'resolve_conflict', 'chat', 'resolve_base_dirty')),
    issued_to VARCHAR(191) NOT NULL REFERENCES users(user_id), created_at TEXT NOT NULL,
    expires_at VARCHAR(191) NOT NULL, consumed_at TEXT, revoked_at TEXT, scratch_dir TEXT,
    dry_run_count INTEGER NOT NULL DEFAULT 0, continuation_target_seq INTEGER,
    continuation_review_mode INTEGER NOT NULL DEFAULT 0, continuation_locale TEXT,
    merge_id INTEGER, continuation_instruction_mode TEXT,
    provider_id VARCHAR(191) REFERENCES ai_providers(provider_id) ON DELETE SET NULL, ai_run_id TEXT,
    continuation_auto_approve_item_seqs TEXT, revoke_claim TEXT
);
INSERT INTO tokens (token_id, hash, pepper_id, project, group_id, doc_ref, action_scope, issued_to,
    created_at, expires_at, consumed_at, revoked_at, scratch_dir, dry_run_count,
    continuation_target_seq, continuation_review_mode, continuation_locale, merge_id,
    continuation_instruction_mode, provider_id, ai_run_id,
    continuation_auto_approve_item_seqs, revoke_claim)
SELECT token_id, hash, pepper_id, project, group_id, doc_ref, action_scope, issued_to,
    created_at, expires_at, consumed_at, revoked_at, scratch_dir, dry_run_count,
    continuation_target_seq, continuation_review_mode, continuation_locale, merge_id,
    continuation_instruction_mode, provider_id, ai_run_id,
    continuation_auto_approve_item_seqs, revoke_claim FROM tokens_before_base_dirty_scope;
DROP TABLE tokens_before_base_dirty_scope;
CREATE UNIQUE INDEX ux_tokens_hash ON tokens(hash);
CREATE INDEX idx_tokens_expires_at ON tokens(expires_at);
CREATE INDEX idx_tokens_issued_to ON tokens(issued_to);
CREATE INDEX idx_tokens_project ON tokens(project);
SET FOREIGN_KEY_CHECKS=1;
