-- 110_repair_tokens_after_base_dirty_rebuild.sql
-- 0533 TR0005 rev1: the copy step must not name `source_access` alongside
-- `failure_origin_target_run_id`/`failure_origin_before_marker`. All three are
-- "maybe missing" on the pre-repair table -- on a DB where 108 (numbered
-- before 109) was actually *applied* after 109 already added the column
-- (parallel branches merged out of chronological order; sqloader tracks only
-- filename, not merge time), 108's rebuild already dropped `source_access`
-- along with the failure_origin columns, and SELECTing it here fails with
-- "no such column: source_access". Tokens are short-lived, so resetting it
-- to NULL for pre-existing rows (same as the two failure_origin columns
-- already do) is an acceptable one-time reset, not a real capability
-- downgrade -- callers already treat a missing/deferred source_access as
-- "resolve it again" rather than as an implicit grant (token_service.py
-- `_resolve_chat_source_access`, tool_registry.py `resolve_source_access`).
PRAGMA foreign_keys=OFF;
BEGIN;
ALTER TABLE tokens RENAME TO tokens_before_110_repair;
CREATE TABLE tokens (
 token_id TEXT PRIMARY KEY, hash TEXT NOT NULL UNIQUE, pepper_id TEXT NOT NULL,
 project TEXT NOT NULL REFERENCES projects(project_id), group_id TEXT REFERENCES groups(group_id), doc_ref TEXT,
 action_scope TEXT NOT NULL CHECK (action_scope IN ('new','edit','workflow_decide','review','test_run','workflow_sequence_edit','resolve_conflict','chat','failure_origin_review','resolve_base_dirty')),
 issued_to TEXT NOT NULL REFERENCES users(user_id), created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
 consumed_at TEXT, revoked_at TEXT, scratch_dir TEXT, dry_run_count INTEGER NOT NULL DEFAULT 0,
 continuation_target_seq INTEGER, continuation_review_mode INTEGER NOT NULL DEFAULT 0, continuation_locale TEXT,
 merge_id INTEGER, continuation_instruction_mode TEXT, provider_id TEXT REFERENCES ai_providers(provider_id) ON DELETE SET NULL,
 ai_run_id TEXT, continuation_auto_approve_item_seqs TEXT, revoke_claim TEXT,
 failure_origin_target_run_id TEXT, failure_origin_before_marker TEXT,
 source_access TEXT CHECK (source_access IN ('read','read_write'))
);
INSERT INTO tokens (token_id,hash,pepper_id,project,group_id,doc_ref,action_scope,issued_to,created_at,expires_at,consumed_at,revoked_at,scratch_dir,dry_run_count,continuation_target_seq,continuation_review_mode,continuation_locale,merge_id,continuation_instruction_mode,provider_id,ai_run_id,continuation_auto_approve_item_seqs,revoke_claim)
SELECT token_id,hash,pepper_id,project,group_id,doc_ref,action_scope,issued_to,created_at,expires_at,consumed_at,revoked_at,scratch_dir,dry_run_count,continuation_target_seq,continuation_review_mode,continuation_locale,merge_id,continuation_instruction_mode,provider_id,ai_run_id,continuation_auto_approve_item_seqs,revoke_claim FROM tokens_before_110_repair;
DROP TABLE tokens_before_110_repair;
CREATE UNIQUE INDEX ux_tokens_hash ON tokens(hash);
CREATE INDEX idx_tokens_expires_at ON tokens(expires_at);
CREATE INDEX idx_tokens_issued_to ON tokens(issued_to);
CREATE INDEX idx_tokens_project ON tokens(project);
COMMIT;
PRAGMA foreign_keys=ON;
