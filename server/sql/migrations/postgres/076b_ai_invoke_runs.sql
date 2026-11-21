-- 076b_ai_invoke_runs.sql
-- flowgate.default.0359 (B0001 → NR0003 → P0006 → L0007 → DB0008).
-- DDL matches the SQLite original (sql/migrations/sqlite/076b_ai_invoke_runs.sql). Only three distinctions:
-- No BEGIN/COMMIT (transactions apply per file, as in existing postgres files);
-- no INTEGER PRIMARY KEY AUTOINCREMENT, so no SERIAL substitution (this table has a string PK);
-- the rest (TEXT/CHECK/REFERENCES .. ON DELETE ../ordinary indexes/ALTER TABLE ADD COLUMN)
-- is valid PostgreSQL syntax as written. See the original comments and DB0008 §3.3 for rationale.

CREATE TABLE IF NOT EXISTS ai_invoke_runs (
    run_id               TEXT    PRIMARY KEY,
    group_id             TEXT    NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    project_id           TEXT    NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    doc_ref              TEXT    NOT NULL,
    mode                 TEXT    NOT NULL CHECK (mode IN ('single', 'continuous')),
    status               TEXT    NOT NULL DEFAULT 'finished' CHECK (status = 'finished'),
    outcome              TEXT    CHECK (outcome IS NULL OR outcome IN ('complete', 'partial', 'none')),
    docs_reached         INTEGER NOT NULL DEFAULT 0 CHECK (docs_reached >= 0),
    docs_target          INTEGER CHECK (docs_target IS NULL OR docs_target >= 0),
    reached_doc_ids      TEXT,
    end_reason           TEXT,
    stop_code            TEXT,
    stop_reason          TEXT,
    resumable            INTEGER NOT NULL DEFAULT 0 CHECK (resumable IN (0, 1)),
    exit_code            INTEGER,
    last_message         TEXT,
    last_message_excerpt TEXT,
    provider_id          TEXT    REFERENCES ai_providers(provider_id) ON DELETE SET NULL,
    provider_name        TEXT,
    attempt_no           INTEGER NOT NULL DEFAULT 0 CHECK (attempt_no >= 0),
    attempts_used        INTEGER NOT NULL DEFAULT 0 CHECK (attempts_used >= 0),
    attempts_max         INTEGER CHECK (attempts_max IS NULL OR attempts_max >= 1),
    fallback_history     TEXT,
    register_errors      TEXT,
    tool_call_misses     INTEGER NOT NULL DEFAULT 0 CHECK (tool_call_misses >= 0),
    turn_limit_exhausted INTEGER NOT NULL DEFAULT 0 CHECK (turn_limit_exhausted IN (0, 1)),
    oracle_mismatch      INTEGER NOT NULL DEFAULT 0 CHECK (oracle_mismatch IN (0, 1)),
    source_dirty         INTEGER CHECK (source_dirty IS NULL OR source_dirty IN (0, 1)),
    scratch_retained     TEXT,
    hop_item_seq         INTEGER CHECK (hop_item_seq IS NULL OR hop_item_seq >= 1),
    token_id             TEXT,
    issued_to            TEXT    REFERENCES users(user_id) ON DELETE SET NULL,
    started_at           TEXT    NOT NULL,
    finished_at          TEXT    NOT NULL,
    duration_ms          INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    timeout_sec          INTEGER CHECK (timeout_sec IS NULL OR timeout_sec > 0),
    deadline_at          TEXT,
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_air_group_started
    ON ai_invoke_runs(group_id, started_at DESC, run_id DESC);

CREATE INDEX IF NOT EXISTS idx_air_project_started
    ON ai_invoke_runs(project_id, started_at DESC, run_id DESC);

CREATE INDEX IF NOT EXISTS idx_air_finished_at
    ON ai_invoke_runs(finished_at);

ALTER TABLE ai_invoke_paused_chains ADD COLUMN stop_kind TEXT;
ALTER TABLE ai_invoke_paused_chains ADD COLUMN stop_code TEXT;
ALTER TABLE ai_invoke_paused_chains ADD COLUMN stop_run_id TEXT;
ALTER TABLE ai_invoke_paused_chains ADD COLUMN stop_last_message_excerpt TEXT;
