-- 075_tokens_chat_scope.sql
-- flowgate.default.0351 T0007: chat-scoped worker identity and restored continuation mode.

ALTER TABLE tokens RENAME TO tokens_before_chat_scope;
CREATE TABLE tokens (
    token_id     TEXT PRIMARY KEY,
    hash         TEXT NOT NULL UNIQUE,
    pepper_id    TEXT NOT NULL,
    project      TEXT NOT NULL REFERENCES projects(project_id),
    group_id     TEXT REFERENCES groups(group_id),
    doc_ref      TEXT,
    action_scope TEXT NOT NULL CHECK (action_scope IN ('new', 'edit', 'workflow_decide', 'review', 'test_run', 'workflow_sequence_edit', 'resolve_conflict', 'chat')),
    issued_to    TEXT NOT NULL REFERENCES users(user_id),
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    consumed_at  TEXT,
    revoked_at   TEXT,
    scratch_dir  TEXT,
    dry_run_count INTEGER NOT NULL DEFAULT 0,
    continuation_target_seq INTEGER,
    continuation_review_mode INTEGER NOT NULL DEFAULT 0,
    continuation_locale TEXT,
    merge_id INTEGER,
    continuation_instruction_mode TEXT,
    provider_id TEXT REFERENCES ai_providers(provider_id) ON DELETE SET NULL,
    ai_run_id TEXT
);
INSERT INTO tokens (
    token_id, hash, pepper_id, project, group_id, doc_ref, action_scope,
    issued_to, created_at, expires_at, consumed_at, revoked_at, scratch_dir,
    dry_run_count, continuation_target_seq, continuation_review_mode,
    continuation_locale, merge_id
)
SELECT
    token_id, hash, pepper_id, project, group_id, doc_ref, action_scope,
    issued_to, created_at, expires_at, consumed_at, revoked_at, scratch_dir,
    COALESCE(dry_run_count, 0), continuation_target_seq,
    COALESCE(continuation_review_mode, 0), continuation_locale, merge_id
FROM tokens_before_chat_scope;
-- [pg-fk-rebuild] preserve inbound FOREIGN KEYs across the drop+recreate of "tokens_before_chat_scope"
DO $$
DECLARE _stmt text;
BEGIN
    CREATE TEMP TABLE _fk_rb_tokens_before_chat_scope ON COMMIT DROP AS
            SELECT 'ALTER TABLE ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
                   || ' ADD CONSTRAINT ' || quote_ident(con.conname) || ' ' || pg_get_constraintdef(con.oid) AS stmt
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE con.contype = 'f' AND con.confrelid = to_regclass('tokens_before_chat_scope')
              AND con.conrelid <> con.confrelid;
    FOR _stmt IN
        SELECT 'ALTER TABLE ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
               || ' DROP CONSTRAINT ' || quote_ident(con.conname)
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE con.contype = 'f' AND con.confrelid = to_regclass('tokens_before_chat_scope')
          AND con.conrelid <> con.confrelid
    LOOP
        EXECUTE _stmt;
    END LOOP;
END $$;
DROP TABLE tokens_before_chat_scope;
CREATE UNIQUE INDEX ux_tokens_hash ON tokens(hash);
CREATE INDEX idx_tokens_expires_at ON tokens(expires_at);
CREATE INDEX idx_tokens_issued_to ON tokens(issued_to);
CREATE INDEX idx_tokens_project ON tokens(project);

-- [pg-fk-rebuild] restore inbound FOREIGN KEYs for "tokens_before_chat_scope"
DO $$
DECLARE _stmt text;
BEGIN
    IF to_regclass('pg_temp._fk_rb_tokens_before_chat_scope') IS NOT NULL THEN
        FOR _stmt IN SELECT stmt FROM _fk_rb_tokens_before_chat_scope LOOP
            EXECUTE _stmt;
        END LOOP;
    END IF;
END $$;
