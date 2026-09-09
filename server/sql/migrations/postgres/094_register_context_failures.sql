-- 094_register_context_failures.sql
-- flowgate.default.0492 T0018 (DB0011 §2.1, L0010 §2.4).
--
-- Structured register-context binding failures, one row per (correlation_id, boundary).
-- Until now the only durable trace of a context-binding 403 was a free-text element in
-- ai_invoke_runs.register_errors: `{"status":403,"reason":"<the whole 403 body>","turn":N}`.
-- That shape cannot answer "which of the four axes was wrong", which is the only question
-- worth asking about a binding rejection (B0001 -> NR0013).
--
-- Deliberate deviations from DB0011, both forced by the schema that actually exists:
--   * the FK is run_id TEXT -> ai_invoke_runs(run_id), not a UUID `id` column: ai_invoke_runs
--     is keyed on the TEXT run_id and has no UUID PK. Rows are therefore written in the same
--     finalize flow that upserts the run, never mid-run (a live run has no row to point at).
--   * ai_invoke_runs.register_errors is NOT dropped here. It stays as the backfill source and
--     the rollback safety net; removing it is a separate compatibility T.
--
-- No BEGIN/COMMIT: the migration runner owns the transaction, so this file can also be
-- applied and rolled back inside a caller-owned transaction (T0018 measurement step 5).
CREATE TABLE IF NOT EXISTS register_context_failures (
    id                    BIGSERIAL PRIMARY KEY,
    recorded_at           TEXT    NOT NULL,
    run_id                TEXT    NOT NULL REFERENCES ai_invoke_runs(run_id) ON DELETE CASCADE,
    correlation_id        TEXT    NOT NULL,
    -- 'legacy_unclassified' is the honest label for a backfilled register_errors element
    -- that never carried axis information. Inventing an axis for it would fabricate the
    -- one fact this table exists to record.
    boundary              TEXT    NOT NULL CHECK (boundary IN ('register_dispatch', 'inbox', 'legacy_unclassified')),
    action_scope_run      TEXT,
    action_scope_token    TEXT,
    action_scope_request  TEXT,
    project_run           TEXT    NOT NULL,
    project_token         TEXT,
    group_run             TEXT    NOT NULL,
    group_token_db        TEXT,
    group_token_resolved  TEXT,
    doc_ref_run           TEXT    NOT NULL,
    doc_ref_token         TEXT,
    prev_doc_id_request   TEXT,
    target_doc_id_request TEXT,
    ai_run_id             TEXT,
    axis_first_mismatch   TEXT    CHECK (axis_first_mismatch IS NULL OR axis_first_mismatch IN ('action', 'project', 'group', 'doc')),
    axes_all_mismatches   TEXT,
    token_id_hash         TEXT,
    expected_fingerprint  TEXT,
    actual_fingerprint    TEXT,
    -- INTEGER 0/1, not BOOLEAN: every hand-written CRUD in db/ binds these flags as 0/1
    -- (one statement, three dialects), and PostgreSQL will not coerce an integer into a
    -- boolean column. ai_invoke_runs.resumable / turn_limit_exhausted / oracle_mismatch are
    -- INTEGER on the live PostgreSQL for exactly this reason; this column follows them.
    binding_relaxed       INTEGER NOT NULL DEFAULT 0 CHECK (binding_relaxed IN (0, 1)),
    relaxed_axis          TEXT,
    status                INTEGER,
    code                  TEXT,
    reason                TEXT,
    turn                  INTEGER,
    notes                 TEXT,
    CONSTRAINT uq_rcf_correlation_boundary UNIQUE (correlation_id, boundary),
    CONSTRAINT ck_rcf_axis_pair CHECK ((axis_first_mismatch IS NULL) = (axes_all_mismatches IS NULL)),
    -- A row written by a live boundary must be fully classified; only the legacy label may
    -- leave the axis columns empty. This is what enforces "new writes carry all four axes".
    CONSTRAINT ck_rcf_live_rows_are_classified CHECK (
        boundary = 'legacy_unclassified' OR (
            axis_first_mismatch IS NOT NULL
            AND action_scope_run IS NOT NULL AND action_scope_token IS NOT NULL
            AND project_token IS NOT NULL AND group_token_resolved IS NOT NULL
            AND doc_ref_token IS NOT NULL
        )
    ),
    -- Stated as two NULL-free equivalences on purpose. The obvious spelling
    --   CHECK (binding_relaxed = 0 OR relaxed_axis = 'doc')
    -- evaluates to NULL for (1, NULL) and SQL treats a NULL CHECK as satisfied, so the one
    -- row it exists to stop -- relaxed with no axis recorded -- went straight in. Measured
    -- on the live PostgreSQL (T0018 rollback run), not reasoned about.
    CONSTRAINT ck_rcf_relaxed_requires_axis CHECK ((binding_relaxed = 1) = (relaxed_axis IS NOT NULL)),
    CONSTRAINT ck_rcf_relaxed_axis_requires_flag CHECK (relaxed_axis IS NULL OR relaxed_axis = 'doc')
);
CREATE INDEX IF NOT EXISTS idx_rcf_run_time ON register_context_failures(run_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_rcf_boundary_axis ON register_context_failures(boundary, axis_first_mismatch);
CREATE INDEX IF NOT EXISTS idx_rcf_recorded ON register_context_failures(recorded_at DESC);
