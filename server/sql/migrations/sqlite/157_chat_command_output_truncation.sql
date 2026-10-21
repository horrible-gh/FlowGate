-- 157_chat_command_output_truncation.sql
-- flowgate.v02.0081: chat_command_requests.stdout_truncated / stderr_truncated record that
-- the stored 64 KiB tail is already shorter than what the process wrote.
-- The table itself comes from main's 136_chat_command_execution.sql. v0.2 used to carry its
-- own copy of that file and of 137_chat_activity_anchor.sql (as 137/138), which re-created
-- the same table, indexes and columns and stopped every boot on
-- "index idx_chat_cmd_run already exists". Those copies are gone; these two columns were
-- the only thing they added.
-- SQLite has no ADD COLUMN IF NOT EXISTS, and a DB that ran v0.2's 137 between 10-06 and
-- 10-08 already has both columns (its 137/138 rows are carried over to 136/137 by
-- migration_rename_repair). So the table is rebuilt, like 110 does for tokens, and the copy
-- step names only the columns every history has. On that 10-06~10-08 history the two flags
-- of existing rows reset to 0; the stored stdout/stderr tails themselves are kept.

BEGIN;
ALTER TABLE chat_command_requests RENAME TO chat_command_requests_before_157;
CREATE TABLE chat_command_requests (
    request_id       TEXT PRIMARY KEY,
    ai_run_id        TEXT NOT NULL,
    doc_id           TEXT NOT NULL,
    project_id       TEXT NOT NULL,
    group_id         TEXT NOT NULL,
    token_id         TEXT NULL,
    issued_to        TEXT NULL,
    provider_name    TEXT NULL,
    program          TEXT NOT NULL,
    args_json        TEXT NOT NULL,
    cwd_relative     TEXT NOT NULL,
    timeout_seconds  INTEGER NOT NULL CHECK (timeout_seconds > 0),
    category         TEXT NOT NULL,
    policy           TEXT NOT NULL
                     CHECK (policy IN ('always_approve', 'user_approval', 'reject')),
    status           TEXT NOT NULL
                     CHECK (status IN ('pending_approval', 'approved', 'rejected', 'running', 'succeeded', 'failed', 'timed_out', 'cancelled')),
    decision_source  TEXT NULL,
    decided_by       TEXT NULL,
    decided_at       TEXT NULL,
    started_at       TEXT NULL,
    finished_at      TEXT NULL,
    duration_ms      INTEGER NULL,
    exit_code        INTEGER NULL,
    timed_out        INTEGER NOT NULL DEFAULT 0 CHECK (timed_out IN (0, 1)),
    stdout_tail      TEXT NULL,
    stderr_tail      TEXT NULL,
    stdout_truncated INTEGER NOT NULL DEFAULT 0 CHECK (stdout_truncated IN (0, 1)),
    stderr_truncated INTEGER NOT NULL DEFAULT 0 CHECK (stderr_truncated IN (0, 1)),
    error_code       TEXT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    run_start_seq    INTEGER NULL,
    anchor_seq       INTEGER NULL,
    anchor_position  VARCHAR(8) NULL
                     CHECK (anchor_position IS NULL OR anchor_position IN ('before', 'after')),
    anchor_state     VARCHAR(16) NULL
                     CHECK (anchor_state IS NULL OR anchor_state IN ('reply', 'run_start', 'ambiguous', 'unresolved'))
);
INSERT INTO chat_command_requests (request_id,ai_run_id,doc_id,project_id,group_id,token_id,issued_to,provider_name,program,args_json,cwd_relative,timeout_seconds,category,policy,status,decision_source,decided_by,decided_at,started_at,finished_at,duration_ms,exit_code,timed_out,stdout_tail,stderr_tail,error_code,created_at,updated_at,run_start_seq,anchor_seq,anchor_position,anchor_state)
SELECT request_id,ai_run_id,doc_id,project_id,group_id,token_id,issued_to,provider_name,program,args_json,cwd_relative,timeout_seconds,category,policy,status,decision_source,decided_by,decided_at,started_at,finished_at,duration_ms,exit_code,timed_out,stdout_tail,stderr_tail,error_code,created_at,updated_at,run_start_seq,anchor_seq,anchor_position,anchor_state FROM chat_command_requests_before_157;
DROP TABLE chat_command_requests_before_157;
CREATE INDEX idx_chat_cmd_run ON chat_command_requests (ai_run_id, created_at);
CREATE INDEX idx_chat_cmd_doc ON chat_command_requests (doc_id, created_at);
CREATE INDEX idx_chat_cmd_status ON chat_command_requests (status);
CREATE INDEX idx_chat_cmd_anchor ON chat_command_requests (doc_id, anchor_seq);
COMMIT;
