-- 137_chat_command_execution.sql
-- flowgate.v02.0032 T#1 (D0006 §4.3 step 1): chat (CH) command execution and per-run
-- source change summaries for the Conversation production contract. The table shapes
-- follow the main 0670 contract (re-implemented on v0.2 with a v0.2 ordinal; main's
-- 136/137 numbers are not reused). Additive only -- no existing table changes.
--   user_chat_command_policy  per-user command policy (always_approve / user_approval /
--                             reject). Own table so saving it never creates a
--                             user_chat_settings row (is_default stays meaningful).
--   chat_command_requests     one durable row per command a chat AI run asked for:
--                             argv, cwd, policy snapshot, decision, result, reason code.
--                             stdout/stderr_truncated record that the stored 64 KiB tail
--                             is already shorter than what the process wrote.
--   ai_run_source_changes     the start-tree -> end-tree change summary of one chat AI
--                             run; at most one row per run, only when >= 1 file changed.

CREATE TABLE IF NOT EXISTS user_chat_command_policy (
    user_id        VARCHAR(191) NOT NULL PRIMARY KEY,
    command_policy VARCHAR(64) NOT NULL
                   CHECK (command_policy IN ('always_approve', 'user_approval', 'reject')),
    created_at     VARCHAR(64) NOT NULL,
    updated_at     VARCHAR(64) NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_command_requests (
    request_id       VARCHAR(191) PRIMARY KEY,
    ai_run_id        VARCHAR(191) NOT NULL,
    doc_id           VARCHAR(191) NOT NULL,
    project_id       VARCHAR(191) NOT NULL,
    group_id         VARCHAR(191) NOT NULL,
    token_id         VARCHAR(191) NULL,
    issued_to        VARCHAR(191) NULL,
    provider_name    MEDIUMTEXT NULL,
    program          MEDIUMTEXT NOT NULL,
    args_json        MEDIUMTEXT NOT NULL,
    cwd_relative     MEDIUMTEXT NOT NULL,
    timeout_seconds  INTEGER NOT NULL CHECK (timeout_seconds > 0),
    category         VARCHAR(64) NOT NULL,
    policy           VARCHAR(64) NOT NULL
                     CHECK (policy IN ('always_approve', 'user_approval', 'reject')),
    status           VARCHAR(64) NOT NULL
                     CHECK (status IN ('pending_approval', 'approved', 'rejected', 'running', 'succeeded', 'failed', 'timed_out', 'cancelled')),
    decision_source  VARCHAR(64) NULL,
    decided_by       VARCHAR(191) NULL,
    decided_at       VARCHAR(64) NULL,
    started_at       VARCHAR(64) NULL,
    finished_at      VARCHAR(64) NULL,
    duration_ms      INTEGER NULL,
    exit_code        INTEGER NULL,
    timed_out        INTEGER NOT NULL DEFAULT 0 CHECK (timed_out IN (0, 1)),
    stdout_tail      MEDIUMTEXT NULL,
    stderr_tail      MEDIUMTEXT NULL,
    stdout_truncated INTEGER NOT NULL DEFAULT 0 CHECK (stdout_truncated IN (0, 1)),
    stderr_truncated INTEGER NOT NULL DEFAULT 0 CHECK (stderr_truncated IN (0, 1)),
    error_code       VARCHAR(64) NULL,
    created_at       VARCHAR(64) NOT NULL,
    updated_at       VARCHAR(64) NOT NULL
);

CREATE INDEX idx_chat_cmd_run ON chat_command_requests (ai_run_id, created_at);
CREATE INDEX idx_chat_cmd_doc ON chat_command_requests (doc_id, created_at);
CREATE INDEX idx_chat_cmd_status ON chat_command_requests (status);

CREATE TABLE IF NOT EXISTS ai_run_source_changes (
    run_id          VARCHAR(191) PRIMARY KEY,
    doc_id          VARCHAR(191) NOT NULL,
    project_id      VARCHAR(191) NOT NULL,
    group_id        VARCHAR(191) NOT NULL,
    start_tree      VARCHAR(64) NOT NULL,
    end_tree        VARCHAR(64) NOT NULL,
    run_started_at  VARCHAR(64) NULL,
    run_finished_at VARCHAR(64) NULL,
    files_changed   INTEGER NOT NULL CHECK (files_changed > 0),
    insertions      INTEGER NULL,
    deletions       INTEGER NULL,
    files_json      MEDIUMTEXT NOT NULL,
    created_at      VARCHAR(64) NOT NULL
);

CREATE INDEX idx_run_changes_doc ON ai_run_source_changes (doc_id, created_at);
