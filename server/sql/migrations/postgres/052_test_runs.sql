-- 052_test_runs.sql
-- Remote TS test execution: run/case child records, test_run token scope, RBAC permission.

ALTER TABLE tokens RENAME TO tokens_before_test_run_scope;
CREATE TABLE tokens (
    token_id     TEXT PRIMARY KEY,
    hash         TEXT NOT NULL UNIQUE,
    pepper_id    TEXT NOT NULL,
    project      TEXT NOT NULL REFERENCES projects(project_id),
    group_id     TEXT REFERENCES groups(group_id),
    doc_ref      TEXT,
    action_scope TEXT NOT NULL
                     CHECK (action_scope IN ('new', 'edit', 'workflow_decide', 'review', 'test_run')),
    issued_to    TEXT NOT NULL REFERENCES users(user_id),
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    consumed_at  TEXT,
    revoked_at   TEXT,
    scratch_dir  TEXT,
    dry_run_count INTEGER NOT NULL DEFAULT 0,
    continuation_target_seq INTEGER,
    continuation_review_mode INTEGER NOT NULL DEFAULT 0,
    continuation_locale TEXT
);
INSERT INTO tokens (
    token_id, hash, pepper_id, project, group_id, doc_ref, action_scope,
    issued_to, created_at, expires_at, consumed_at, revoked_at, scratch_dir,
    dry_run_count, continuation_target_seq, continuation_review_mode, continuation_locale
)
SELECT
    token_id, hash, pepper_id, project, group_id, doc_ref, action_scope,
    issued_to, created_at, expires_at, consumed_at, revoked_at, scratch_dir,
    COALESCE(dry_run_count, 0), continuation_target_seq,
    COALESCE(continuation_review_mode, 0), continuation_locale
FROM tokens_before_test_run_scope;
DO $$
DECLARE _stmt text;
BEGIN
    CREATE TEMP TABLE _fk_rb_tokens_before_test_run_scope ON COMMIT DROP AS
            SELECT 'ALTER TABLE ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
                   || ' ADD CONSTRAINT ' || quote_ident(con.conname) || ' ' || pg_get_constraintdef(con.oid) AS stmt
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE con.contype = 'f' AND con.confrelid = to_regclass('tokens_before_test_run_scope')
              AND con.conrelid <> con.confrelid;
    FOR _stmt IN
        SELECT 'ALTER TABLE ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
               || ' DROP CONSTRAINT ' || quote_ident(con.conname)
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE con.contype = 'f' AND con.confrelid = to_regclass('tokens_before_test_run_scope')
          AND con.conrelid <> con.confrelid
    LOOP
        EXECUTE _stmt;
    END LOOP;
END $$;
DROP TABLE tokens_before_test_run_scope;
CREATE UNIQUE INDEX ux_tokens_hash ON tokens(hash);
CREATE INDEX idx_tokens_expires_at ON tokens(expires_at);
CREATE INDEX idx_tokens_issued_to ON tokens(issued_to);
CREATE INDEX idx_tokens_project ON tokens(project);
DO $$
DECLARE _stmt text;
BEGIN
    IF to_regclass('pg_temp._fk_rb_tokens_before_test_run_scope') IS NOT NULL THEN
        FOR _stmt IN SELECT stmt FROM _fk_rb_tokens_before_test_run_scope LOOP
            EXECUTE _stmt;
        END LOOP;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS test_runs (
    run_id       TEXT PRIMARY KEY,
    doc_id       TEXT    NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    revision_no  INTEGER NOT NULL,
    status       TEXT    NOT NULL CHECK (status IN ('running','passed','failed')),
    triggered_via TEXT   NOT NULL CHECK (triggered_via IN ('ui','token')),
    runner_id    TEXT    NOT NULL,
    case_total   INTEGER NOT NULL DEFAULT 0,
    case_passed  INTEGER NOT NULL DEFAULT 0,
    case_failed  INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    picked_at    TEXT,
    started_at   TEXT,
    finished_at  TEXT,
    port         INTEGER,
    tsr_doc_id   TEXT REFERENCES documents(doc_id) ON DELETE SET NULL,
    created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_test_runs_doc
    ON test_runs(doc_id, created_at DESC);

CREATE TABLE IF NOT EXISTS test_run_cases (
    id           SERIAL PRIMARY KEY,
    run_id       TEXT    NOT NULL REFERENCES test_runs(run_id) ON DELETE CASCADE,
    kind         TEXT    NOT NULL DEFAULT 'case'
                         CHECK (kind IN ('case','setup','service','wait','teardown')),
    case_no      TEXT    NOT NULL,
    case_title   TEXT    NOT NULL,
    cmd          TEXT    NOT NULL,
    expect       TEXT    NOT NULL,
    result       TEXT    CHECK (result IN ('pass','fail','timeout')),
    exit_code    INTEGER,
    duration_ms  INTEGER,
    output_tail  TEXT,
    finished_at  TEXT,
    UNIQUE(run_id, case_no)
);
CREATE INDEX IF NOT EXISTS idx_test_run_cases_run
    ON test_run_cases(run_id, id);

INSERT INTO permissions (permission_id, permission_name, description, created_at)
VALUES ('perm_test_run', '테스트 실행', 'TS 문서의 원격 테스트 실행 권한', CURRENT_TIMESTAMP)
ON CONFLICT DO NOTHING;

INSERT INTO role_permissions (role_id, permission_id)
SELECT DISTINCT role_id, 'perm_test_run'
FROM role_permissions
WHERE permission_id = 'perm_document_approve'
ON CONFLICT DO NOTHING;
