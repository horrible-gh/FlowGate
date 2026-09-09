-- 110_repair_tokens_after_base_dirty_rebuild.sql
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
INSERT INTO tokens (token_id,hash,pepper_id,project,group_id,doc_ref,action_scope,issued_to,created_at,expires_at,consumed_at,revoked_at,scratch_dir,dry_run_count,continuation_target_seq,continuation_review_mode,continuation_locale,merge_id,continuation_instruction_mode,provider_id,ai_run_id,continuation_auto_approve_item_seqs,revoke_claim,source_access)
SELECT token_id,hash,pepper_id,project,group_id,doc_ref,action_scope,issued_to,created_at,expires_at,consumed_at,revoked_at,scratch_dir,dry_run_count,continuation_target_seq,continuation_review_mode,continuation_locale,merge_id,continuation_instruction_mode,provider_id,ai_run_id,continuation_auto_approve_item_seqs,revoke_claim,source_access FROM tokens_before_110_repair;
DROP TABLE tokens_before_110_repair;
CREATE UNIQUE INDEX ux_tokens_hash ON tokens(hash);
CREATE INDEX idx_tokens_expires_at ON tokens(expires_at);
CREATE INDEX idx_tokens_issued_to ON tokens(issued_to);
CREATE INDEX idx_tokens_project ON tokens(project);
COMMIT;
PRAGMA foreign_keys=ON;
