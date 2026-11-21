-- 076b_ai_invoke_runs.sql
-- flowgate.default.0359 (B0001 → NR0003 → P0006 → L0007 → DB0008).
-- MySQL/MariaDB version of the SQLite original (sql/migrations/sqlite/076b_ai_invoke_runs.sql).
-- See the original comments and DB0008 §3.4 for rationale and columns. Four dialect differences:
--
-- 1. Convert key columns (run_id/group_id/project_id/provider_id/issued_to/started_at/finished_at)
--    from TEXT to VARCHAR(191). MySQL cannot index/reference TEXT without a prefix length.
-- 2. Promote short enum columns (mode/status/outcome/end_reason/stop_code) to VARCHAR.
--    TEXT columns cannot have literal defaults (status defaults to 'finished').
-- 3. Write FKs as table-level CONSTRAINTs: InnoDB silently ignores inline REFERENCES
--    in column definitions (the problem in migration 067; follow 059).
-- 4. Replace CREATE INDEX IF NOT EXISTS with CREATE INDEX. MySQL 8 lacks IF NOT EXISTS,
--    and the migration ledger guarantees one application (precedent: 052). Also omit BEGIN/COMMIT
--    because DDL commits implicitly, making explicit transactions meaningless.
--
-- The four ALTER TABLE ... ADD COLUMN lines match the original (as in the MySQL versions of 051/059).

CREATE TABLE IF NOT EXISTS ai_invoke_runs (
    run_id               VARCHAR(191) PRIMARY KEY,
    group_id             VARCHAR(191) NOT NULL,
    project_id           VARCHAR(191) NOT NULL,
    doc_ref              TEXT         NOT NULL,
    mode                 VARCHAR(16)  NOT NULL CHECK (mode IN ('single', 'continuous')),
    status               VARCHAR(16)  NOT NULL DEFAULT 'finished' CHECK (status = 'finished'),
    outcome              VARCHAR(16)  CHECK (outcome IS NULL OR outcome IN ('complete', 'partial', 'none')),
    docs_reached         INTEGER      NOT NULL DEFAULT 0 CHECK (docs_reached >= 0),
    docs_target          INTEGER      CHECK (docs_target IS NULL OR docs_target >= 0),
    reached_doc_ids      TEXT,
    end_reason           VARCHAR(32),
    stop_code            VARCHAR(32),
    stop_reason          TEXT,
    resumable            INTEGER      NOT NULL DEFAULT 0 CHECK (resumable IN (0, 1)),
    exit_code            INTEGER,
    last_message         TEXT,
    last_message_excerpt TEXT,
    provider_id          VARCHAR(191),
    provider_name        TEXT,
    attempt_no           INTEGER      NOT NULL DEFAULT 0 CHECK (attempt_no >= 0),
    attempts_used        INTEGER      NOT NULL DEFAULT 0 CHECK (attempts_used >= 0),
    attempts_max         INTEGER      CHECK (attempts_max IS NULL OR attempts_max >= 1),
    fallback_history     TEXT,
    register_errors      TEXT,
    tool_call_misses     INTEGER      NOT NULL DEFAULT 0 CHECK (tool_call_misses >= 0),
    turn_limit_exhausted INTEGER      NOT NULL DEFAULT 0 CHECK (turn_limit_exhausted IN (0, 1)),
    oracle_mismatch      INTEGER      NOT NULL DEFAULT 0 CHECK (oracle_mismatch IN (0, 1)),
    source_dirty         INTEGER      CHECK (source_dirty IS NULL OR source_dirty IN (0, 1)),
    scratch_retained     TEXT,
    hop_item_seq         INTEGER      CHECK (hop_item_seq IS NULL OR hop_item_seq >= 1),
    token_id             TEXT,
    issued_to            VARCHAR(191),
    started_at           VARCHAR(191) NOT NULL,
    finished_at          VARCHAR(191) NOT NULL,
    duration_ms          INTEGER      CHECK (duration_ms IS NULL OR duration_ms >= 0),
    timeout_sec          INTEGER      CHECK (timeout_sec IS NULL OR timeout_sec > 0),
    deadline_at          TEXT,
    created_at           TEXT         NOT NULL,
    updated_at           TEXT         NOT NULL,
    CONSTRAINT fk_air_group
        FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE,
    CONSTRAINT fk_air_project
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    CONSTRAINT fk_air_provider
        FOREIGN KEY (provider_id) REFERENCES ai_providers(provider_id) ON DELETE SET NULL,
    CONSTRAINT fk_air_issued_to
        FOREIGN KEY (issued_to) REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE INDEX idx_air_group_started
    ON ai_invoke_runs(group_id, started_at DESC, run_id DESC);

CREATE INDEX idx_air_project_started
    ON ai_invoke_runs(project_id, started_at DESC, run_id DESC);

CREATE INDEX idx_air_finished_at
    ON ai_invoke_runs(finished_at);

ALTER TABLE ai_invoke_paused_chains ADD COLUMN stop_kind TEXT;
ALTER TABLE ai_invoke_paused_chains ADD COLUMN stop_code TEXT;
ALTER TABLE ai_invoke_paused_chains ADD COLUMN stop_run_id TEXT;
ALTER TABLE ai_invoke_paused_chains ADD COLUMN stop_last_message_excerpt TEXT;
