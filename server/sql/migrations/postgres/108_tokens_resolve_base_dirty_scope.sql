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
    token_id TEXT PRIMARY KEY, hash TEXT NOT NULL UNIQUE, pepper_id TEXT NOT NULL,
    project TEXT NOT NULL REFERENCES projects(project_id), group_id TEXT REFERENCES groups(group_id),
    doc_ref TEXT, action_scope TEXT NOT NULL CHECK (action_scope IN ('new', 'edit', 'workflow_decide', 'review', 'test_run', 'workflow_sequence_edit', 'resolve_conflict', 'chat', 'resolve_base_dirty')),
    issued_to TEXT NOT NULL REFERENCES users(user_id), created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL, consumed_at TEXT, revoked_at TEXT, scratch_dir TEXT,
    dry_run_count INTEGER NOT NULL DEFAULT 0, continuation_target_seq INTEGER,
    continuation_review_mode INTEGER NOT NULL DEFAULT 0, continuation_locale TEXT,
    merge_id INTEGER, continuation_instruction_mode TEXT,
    provider_id TEXT REFERENCES ai_providers(provider_id) ON DELETE SET NULL, ai_run_id TEXT,
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
-- [pg-fk-rebuild] preserve inbound FOREIGN KEYs across the drop+recreate of "tokens_before_base_dirty_scope"
DO $$
DECLARE _stmt text;
BEGIN
    CREATE TEMP TABLE _fk_rb_tokens_before_base_dirty_scope ON COMMIT DROP AS
            SELECT 'ALTER TABLE ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
                   || ' ADD CONSTRAINT ' || quote_ident(con.conname) || ' ' || pg_get_constraintdef(con.oid) AS stmt
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE con.contype = 'f' AND con.confrelid = to_regclass('tokens_before_base_dirty_scope')
              AND con.conrelid <> con.confrelid;
    FOR _stmt IN
        SELECT 'ALTER TABLE ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
               || ' DROP CONSTRAINT ' || quote_ident(con.conname)
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE con.contype = 'f' AND con.confrelid = to_regclass('tokens_before_base_dirty_scope')
          AND con.conrelid <> con.confrelid
    LOOP
        EXECUTE _stmt;
    END LOOP;
END $$;
DROP TABLE tokens_before_base_dirty_scope;
CREATE UNIQUE INDEX ux_tokens_hash ON tokens(hash);
CREATE INDEX idx_tokens_expires_at ON tokens(expires_at);
CREATE INDEX idx_tokens_issued_to ON tokens(issued_to);
CREATE INDEX idx_tokens_project ON tokens(project);

-- [pg-fk-rebuild] restore inbound FOREIGN KEYs for "tokens_before_base_dirty_scope"
DO $$
DECLARE _stmt text;
BEGIN
    IF to_regclass('pg_temp._fk_rb_tokens_before_base_dirty_scope') IS NOT NULL THEN
        FOR _stmt IN SELECT stmt FROM _fk_rb_tokens_before_base_dirty_scope LOOP
            EXECUTE _stmt;
        END LOOP;
    END IF;
END $$;
