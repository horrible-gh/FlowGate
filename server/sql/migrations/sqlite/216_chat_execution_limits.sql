BEGIN;
-- F108-5: preserve existing chat durations and add per-user command limits.
ALTER TABLE user_conversation_settings ADD COLUMN timeout_seconds INTEGER NULL CHECK (timeout_seconds BETWEEN 60 AND 604800);
ALTER TABLE user_conversation_settings ADD COLUMN command_timeout_seconds INTEGER NULL CHECK (command_timeout_seconds BETWEEN 0 AND 1800);
UPDATE user_conversation_settings SET timeout_seconds = timeout_minutes * 60;

ALTER TABLE chat_command_requests RENAME TO chat_command_requests_before_216;
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
    timeout_seconds  INTEGER NOT NULL CHECK (timeout_seconds >= 0),
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
INSERT INTO chat_command_requests (request_id,ai_run_id,doc_id,project_id,group_id,token_id,issued_to,provider_name,program,args_json,cwd_relative,timeout_seconds,category,policy,status,decision_source,decided_by,decided_at,started_at,finished_at,duration_ms,exit_code,timed_out,stdout_tail,stderr_tail,stdout_truncated,stderr_truncated,error_code,created_at,updated_at,run_start_seq,anchor_seq,anchor_position,anchor_state)
SELECT request_id,ai_run_id,doc_id,project_id,group_id,token_id,issued_to,provider_name,program,args_json,cwd_relative,timeout_seconds,category,policy,status,decision_source,decided_by,decided_at,started_at,finished_at,duration_ms,exit_code,timed_out,stdout_tail,stderr_tail,stdout_truncated,stderr_truncated,error_code,created_at,updated_at,run_start_seq,anchor_seq,anchor_position,anchor_state FROM chat_command_requests_before_216;
DROP TABLE chat_command_requests_before_216;
CREATE INDEX idx_chat_cmd_run ON chat_command_requests (ai_run_id, created_at);
CREATE INDEX idx_chat_cmd_doc ON chat_command_requests (doc_id, created_at);
CREATE INDEX idx_chat_cmd_status ON chat_command_requests (status);
CREATE INDEX idx_chat_cmd_anchor ON chat_command_requests (doc_id, anchor_seq);
COMMIT;
