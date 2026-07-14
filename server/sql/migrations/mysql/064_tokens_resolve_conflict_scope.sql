SET FOREIGN_KEY_CHECKS=0;
-- 064_tokens_resolve_conflict_scope.sql
-- flowgate.default.0233 T0011: allow tokens issued for AI conflict-resolution.
--
-- Migration 063 added tokens.merge_id for resolve_conflict worker tokens but omitted
-- updating the action_scope CHECK, so issuing a resolve_conflict token (copy-mention
-- [멘트복사] via /token/issue and [AI호출] via /ai-invoke/start) violated
-- tokens_action_scope_check -> bodyless HTTP 500 (NR0010). The inline CHECK from 062
-- gets an auto-generated name on MySQL, so rebuild the table exactly like 062 did,
-- this time with the 7-value list AND preserving the merge_id column added by 063.

ALTER TABLE tokens RENAME TO tokens_before_resolve_conflict_scope;
CREATE TABLE tokens (
    token_id     VARCHAR(191) PRIMARY KEY,
    hash         VARCHAR(191) NOT NULL UNIQUE,
    pepper_id    TEXT NOT NULL,
    project      VARCHAR(191) NOT NULL REFERENCES projects(project_id),
    group_id     VARCHAR(191) REFERENCES groups(group_id),
    doc_ref      TEXT,
    action_scope TEXT NOT NULL
                     CHECK (action_scope IN ('new', 'edit', 'workflow_decide', 'review', 'test_run', 'workflow_sequence_edit', 'resolve_conflict')),
    issued_to    VARCHAR(191) NOT NULL REFERENCES users(user_id),
    created_at   TEXT NOT NULL,
    expires_at   VARCHAR(191) NOT NULL,
    consumed_at  TEXT,
    revoked_at   TEXT,
    scratch_dir  TEXT,
    dry_run_count INTEGER NOT NULL DEFAULT 0,
    continuation_target_seq INTEGER,
    continuation_review_mode INTEGER NOT NULL DEFAULT 0,
    continuation_locale TEXT,
    merge_id INTEGER
);
INSERT INTO tokens (
    token_id, hash, pepper_id, project, group_id, doc_ref, action_scope,
    issued_to, created_at, expires_at, consumed_at, revoked_at, scratch_dir,
    dry_run_count, continuation_target_seq, continuation_review_mode, continuation_locale, merge_id
)
SELECT
    token_id, hash, pepper_id, project, group_id, doc_ref, action_scope,
    issued_to, created_at, expires_at, consumed_at, revoked_at, scratch_dir,
    COALESCE(dry_run_count, 0), continuation_target_seq,
    COALESCE(continuation_review_mode, 0), continuation_locale, merge_id
FROM tokens_before_resolve_conflict_scope;
DROP TABLE tokens_before_resolve_conflict_scope;
CREATE UNIQUE INDEX ux_tokens_hash ON tokens(hash);
CREATE INDEX idx_tokens_expires_at ON tokens(expires_at);
CREATE INDEX idx_tokens_issued_to ON tokens(issued_to);
CREATE INDEX idx_tokens_project ON tokens(project);

SET FOREIGN_KEY_CHECKS=1;
