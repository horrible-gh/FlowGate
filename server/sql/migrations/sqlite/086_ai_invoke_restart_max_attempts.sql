-- 086_ai_invoke_restart_max_attempts.sql
-- flowgate.default.0443 T0002 (R0001): ContinuousWorkDialog's 기본 설정 탭 gained a
-- 재시작 횟수 select (-1/0/1/2/3, default 1) — the number of times a no-output hop
-- retries on the SAME step-assigned provider (never a different one) before giving up,
-- replacing the fixed NO_OUTPUT_MAX_ATTEMPTS for chains that want more (or fewer, or
-- unlimited via -1) retries.
-- Additive, nullable: NULL on a paused row means "no pick was made", and the engine
-- falls back to NO_OUTPUT_MAX_ATTEMPTS exactly like it always did — pre-migration rows
-- keep today's behavior.

BEGIN;

ALTER TABLE ai_invoke_paused_chains ADD COLUMN continuation_restart_max_attempts INTEGER;

-- TR0005 review: -1 ("될 때까지") is a real, chosen value for a FINISHED run too, not just
-- a live/paused one, and _persist_run_record (L0007 2.10.1) writes run["attempts_max"]
-- straight through to ai_invoke_runs.attempts_max unchanged. The 076b CHECK only allowed
-- NULL or >= 1, so every -1 run's durable upsert violated the CHECK; the IntegrityError
-- was swallowed by _persist_run_record's own try/except (L0007 §5: a storage failure must
-- never turn a finished hop into a crashed one), so the run finished but left no row in
-- history. Widen the CHECK to accept -1 as well, so the finished run's real pick survives
-- exactly as chosen — collapsing it to NULL instead would make it indistinguishable from
-- "no pick was made" on read-back. ai_invoke_runs is a leaf table (nothing references it),
-- so this is the same rewrite shape as 039/042a's tokens rebuild. SQLite cannot ALTER a
-- CHECK constraint in place, so the table is recreated.
ALTER TABLE ai_invoke_runs RENAME TO ai_invoke_runs_before_unlimited_attempts;

CREATE TABLE ai_invoke_runs (
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
    attempts_max         INTEGER CHECK (attempts_max IS NULL OR attempts_max >= 1 OR attempts_max = -1),
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
    worker_document_type TEXT,
    continuation_instruction_mode_requested TEXT,
    continuation_instruction_mode_normalized TEXT,
    continuation_instruction_mode_fallback_applied INTEGER NOT NULL DEFAULT 0,
    auto_handled_item_seqs TEXT,
    prompt_message_source TEXT,
    prompt_common_default_applied INTEGER NOT NULL DEFAULT 0,
    prompt_user_message_length INTEGER NOT NULL DEFAULT 0,
    prompt_user_message_sha256 TEXT,
    prompt_final_length  INTEGER NOT NULL DEFAULT 0,
    prompt_final_sha256  TEXT,
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL
);

INSERT INTO ai_invoke_runs (
    run_id, group_id, project_id, doc_ref, mode, status, outcome, docs_reached, docs_target,
    reached_doc_ids, end_reason, stop_code, stop_reason, resumable, exit_code, last_message,
    last_message_excerpt, provider_id, provider_name, attempt_no, attempts_used, attempts_max,
    fallback_history, register_errors, tool_call_misses, turn_limit_exhausted, oracle_mismatch,
    source_dirty, scratch_retained, hop_item_seq, token_id, issued_to, started_at, finished_at,
    duration_ms, timeout_sec, deadline_at, worker_document_type,
    continuation_instruction_mode_requested, continuation_instruction_mode_normalized,
    continuation_instruction_mode_fallback_applied, auto_handled_item_seqs, prompt_message_source,
    prompt_common_default_applied, prompt_user_message_length, prompt_user_message_sha256,
    prompt_final_length, prompt_final_sha256, created_at, updated_at
)
SELECT
    run_id, group_id, project_id, doc_ref, mode, status, outcome, docs_reached, docs_target,
    reached_doc_ids, end_reason, stop_code, stop_reason, resumable, exit_code, last_message,
    last_message_excerpt, provider_id, provider_name, attempt_no, attempts_used, attempts_max,
    fallback_history, register_errors, tool_call_misses, turn_limit_exhausted, oracle_mismatch,
    source_dirty, scratch_retained, hop_item_seq, token_id, issued_to, started_at, finished_at,
    duration_ms, timeout_sec, deadline_at, worker_document_type,
    continuation_instruction_mode_requested, continuation_instruction_mode_normalized,
    continuation_instruction_mode_fallback_applied, auto_handled_item_seqs, prompt_message_source,
    prompt_common_default_applied, prompt_user_message_length, prompt_user_message_sha256,
    prompt_final_length, prompt_final_sha256, created_at, updated_at
FROM ai_invoke_runs_before_unlimited_attempts;

DROP TABLE ai_invoke_runs_before_unlimited_attempts;

CREATE INDEX IF NOT EXISTS idx_air_group_started
    ON ai_invoke_runs(group_id, started_at DESC, run_id DESC);

CREATE INDEX IF NOT EXISTS idx_air_project_started
    ON ai_invoke_runs(project_id, started_at DESC, run_id DESC);

CREATE INDEX IF NOT EXISTS idx_air_finished_at
    ON ai_invoke_runs(finished_at);

COMMIT;
