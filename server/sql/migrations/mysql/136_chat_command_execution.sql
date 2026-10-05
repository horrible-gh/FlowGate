-- 136_chat_command_execution.sql
-- (136, not 130: main already carries 130-135 from flowgate.default.0669.)
-- flowgate.default.0670 T0004 (R0001 / NR0003): chat (CH) command execution and
-- per-run source change summaries. Additive only -- no existing table changes.
--   user_chat_command_policy  -- the per-user command policy (always_approve /
--                                user_approval / reject). Kept in its own table like
--                                user_chat_source_access so writing it never creates
--                                a user_chat_settings row (is_default stays meaningful).
--   chat_command_requests     -- one durable row per command an AI run asked for:
--                                argv, cwd, policy snapshot, decision, execution result.
--   ai_run_source_changes     -- the FlowGate-computed start-tree -> end-tree change
--                                summary of one CH AI run. Only written when >= 1 file changed.

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
    status           VARCHAR(64) NOT NULL CHECK (status IN ('pending_approval', 'approved', 'rejected', 'running', 'succeeded', 'failed', 'timed_out', 'cancelled')),
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
    error_code       VARCHAR(64) NULL,
    created_at       VARCHAR(64) NOT NULL,
    updated_at       VARCHAR(64) NOT NULL
);

CREATE INDEX idx_chat_cmd_run ON chat_command_requests (ai_run_id, created_at);
CREATE INDEX idx_chat_cmd_doc ON chat_command_requests (doc_id, created_at);
CREATE INDEX idx_chat_cmd_status ON chat_command_requests (status);

CREATE TABLE IF NOT EXISTS ai_run_source_changes (
    run_id         VARCHAR(191) PRIMARY KEY,
    doc_id         VARCHAR(191) NOT NULL,
    project_id     VARCHAR(191) NOT NULL,
    group_id       VARCHAR(191) NOT NULL,
    start_tree     VARCHAR(64) NOT NULL,
    end_tree       VARCHAR(64) NOT NULL,
    run_started_at VARCHAR(64) NULL,
    run_finished_at VARCHAR(64) NULL,
    files_changed  INTEGER NOT NULL CHECK (files_changed > 0),
    insertions     INTEGER NULL,
    deletions      INTEGER NULL,
    files_json     MEDIUMTEXT NOT NULL,
    created_at     VARCHAR(64) NOT NULL
);

CREATE INDEX idx_run_changes_doc ON ai_run_source_changes (doc_id, created_at);
