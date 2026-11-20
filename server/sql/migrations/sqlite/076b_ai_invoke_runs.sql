-- 076b_ai_invoke_runs.sql
-- flowgate.default.0359 (B0001 → NR0003 → P0006 → L0007 → DB0008):
-- Address continuous runs silently ending at the last hop without any retained record.
--
-- Add two things:
--  1) ai_invoke_runs: one row per finalized run, the only record surviving server restarts.
--     Written once by upsert at finalization (memory is authoritative for live runs),
--     read only by detail (one run_id) and list (group_id | project_id + newest-first LIMIT).
--  2) Four stop_* columns on ai_invoke_paused_chains: system-stopped chains appear in
--     the mini-player like user-stopped chains, keeping [이어서 진행] working unchanged.
--     Read NULL stop_kind as 'user' (all existing rows were user-stopped).
--
-- Additive only: do not rebuild existing tables or remove/rename existing columns.
-- No backfill. Leave tokens untouched (ai_run_id was already added in 075).

BEGIN;

CREATE TABLE IF NOT EXISTS ai_invoke_runs (
    run_id               TEXT    PRIMARY KEY,
    group_id             TEXT    NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    project_id           TEXT    NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    -- No FK on doc_ref: documents can be deleted by rollback (Time Machine),
    -- but deleting the spine document must not erase incident records.
    doc_ref              TEXT    NOT NULL,
    mode                 TEXT    NOT NULL CHECK (mode IN ('single', 'continuous')),
    -- Enforce the invariant that records are stored only once at finalization.
    status               TEXT    NOT NULL DEFAULT 'finished' CHECK (status = 'finished'),
    outcome              TEXT    CHECK (outcome IS NULL OR outcome IN ('complete', 'partial', 'none')),
    docs_reached         INTEGER NOT NULL DEFAULT 0 CHECK (docs_reached >= 0),
    docs_target          INTEGER CHECK (docs_target IS NULL OR docs_target >= 0),
    reached_doc_ids      TEXT,
    -- No CHECK on end_reason / stop_code: their vocabulary grows, and SQLite requires
    -- rebuilding the table to alter a CHECK (same rationale as ai_mode in 059).
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
    -- No FK on token_id either: tokens are hard-deleted 30 days after expiration,
    -- while this record lasts 90 days. Linking different lifetimes would let the shorter one win.
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

-- List query: WHERE <axis> = ? ORDER BY started_at DESC, run_id DESC LIMIT ?
-- The index contains sort order, enabling early termination for LIMIT (as with the
-- (project_id, group_id, updated_at) index created in 070 for documents lists; its exact name
-- is omitted because a guard checks that files after 070 do not redefine that index).
-- The run_id suffix stabilizes ordering for two runs started in the same second.
CREATE INDEX IF NOT EXISTS idx_air_group_started
    ON ai_invoke_runs(group_id, started_at DESC, run_id DESC);

CREATE INDEX IF NOT EXISTS idx_air_project_started
    ON ai_invoke_runs(project_id, started_at DESC, run_id DESC);

-- 90-day retention cleanup: DELETE ... WHERE finished_at < <cutoff>
CREATE INDEX IF NOT EXISTS idx_air_finished_at
    ON ai_invoke_runs(finished_at);

-- Mark system-stopped rows. No default or CHECK:
-- SQLite cannot attach CHECK via ADD COLUMN, and readers treat existing NULL values
-- as 'user' (L0007 2.8), so no backfill is needed.
ALTER TABLE ai_invoke_paused_chains ADD COLUMN stop_kind TEXT;
ALTER TABLE ai_invoke_paused_chains ADD COLUMN stop_code TEXT;
ALTER TABLE ai_invoke_paused_chains ADD COLUMN stop_run_id TEXT;
ALTER TABLE ai_invoke_paused_chains ADD COLUMN stop_last_message_excerpt TEXT;

COMMIT;
