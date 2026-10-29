-- 164_graph_stage_assignments.sql — flowgate.v02.0113 DB-1 (stage assignments)
-- One row per (attempt, stage, seq), first writer wins. Only stage_state / fail_cause / verdict / intent_kind /
-- intent_exec_id change after the INSERT. An invalidated attempt keeps its rows. Additive only; an attempt opened
-- before this migration has no row and finishes on the current path. CHECKs are NULL-safe (see 163 header).
--   graph_attempt_runs.assignment_stage / assignment_seq  the assignment that made a ledger request (NULL = none).
BEGIN;

CREATE TABLE IF NOT EXISTS graph_stage_assignments (
    attempt_id              TEXT NOT NULL REFERENCES graph_node_attempts(attempt_id) ON DELETE CASCADE,
    stage                   TEXT NOT NULL CHECK (stage IN ('instruction', 'result', 'result_review', 'instruction_review')),
    seq                     INTEGER NOT NULL,
    run_id                  TEXT NOT NULL REFERENCES graph_runs(run_id) ON DELETE CASCADE,
    group_id                TEXT NOT NULL,
    node_key                TEXT NOT NULL,
    role                    TEXT NOT NULL CHECK (role IN ('instruction', 'execution', 'review')),
    actor                   TEXT NOT NULL CHECK (actor IN ('ai', 'person', 'no_review', 'wait_setup', 'wait_unavailable')),
    actor_reason            TEXT,
    provider_id             TEXT,
    provider_name           TEXT,
    role_value_kind         TEXT CHECK (role_value_kind IS NULL OR role_value_kind IN ('assigned', 'not_used')),
    role_value_provider_id  TEXT,
    source                  TEXT NOT NULL,
    cause                   TEXT,
    plan_instruction_type   TEXT,
    plan_result_type        TEXT,
    reused_doc_id           TEXT,
    review_rounds           INTEGER,
    rework_provider_id      TEXT,
    author                  TEXT CHECK (author IS NULL OR author IN ('ai', 'person', 'unknown')),
    author_provider_id      TEXT,
    basis_doc_id            TEXT,
    basis_revision_no       INTEGER,
    basis_rejection_count   INTEGER,
    decided_at              TEXT NOT NULL,
    stage_state             TEXT NOT NULL CHECK (stage_state IN ('active', 'waiting', 'done', 'failed')),
    fail_cause              TEXT,
    verdict                 TEXT,
    intent_kind             TEXT CHECK (intent_kind IS NULL OR intent_kind IN ('execution', 'approval')),
    intent_exec_id          TEXT,
    updated_at              TEXT NOT NULL,
    PRIMARY KEY (attempt_id, stage, seq),
    -- CK-G1 … CK-G14 (DB-1 §2.4), written out in full
    CHECK ((stage IN ('instruction', 'result') AND seq = 0) OR (stage IN ('result_review', 'instruction_review') AND seq >= 1)),
    CHECK ((stage = 'instruction' AND role = 'instruction') OR (stage = 'result' AND role = 'execution')
        OR (stage IN ('result_review', 'instruction_review') AND role = 'review')),
    CHECK ((actor = 'ai' AND provider_id IS NOT NULL AND TRIM(provider_id) <> '')
        OR (actor = 'wait_unavailable' AND provider_id IS NOT NULL)
        OR (actor NOT IN ('ai', 'wait_unavailable') AND provider_id IS NULL)),
    CHECK (actor <> 'wait_unavailable' OR cause IS NOT NULL),
    CHECK ((actor <> 'person' OR stage IN ('instruction', 'result'))
        AND (actor <> 'no_review' OR stage IN ('result_review', 'instruction_review'))),
    CHECK ((role_value_kind IS NULL AND role_value_provider_id IS NULL)
        OR (role_value_kind IS NOT NULL AND role_value_kind = 'assigned'
            AND role_value_provider_id IS NOT NULL AND TRIM(role_value_provider_id) <> '')
        OR (role_value_kind IS NOT NULL AND role_value_kind = 'not_used' AND role_value_provider_id IS NULL)),
    CHECK ((plan_result_type IS NULL AND plan_instruction_type IS NULL)
        OR (plan_result_type IS NOT NULL AND stage IN ('instruction', 'result'))),
    CHECK (reused_doc_id IS NULL OR stage = 'instruction'),
    CHECK ((stage IN ('result_review', 'instruction_review') AND actor = 'ai' AND review_rounds IS NOT NULL
            AND (review_rounds >= 1 OR review_rounds = -1))
        OR (NOT (stage IN ('result_review', 'instruction_review') AND actor = 'ai')
            AND review_rounds IS NULL AND rework_provider_id IS NULL)),
    CHECK ((stage IN ('result_review', 'instruction_review') AND author IS NOT NULL AND basis_doc_id IS NOT NULL)
        OR (stage IN ('instruction', 'result') AND author IS NULL AND author_provider_id IS NULL AND basis_doc_id IS NULL)),
    CHECK (author_provider_id IS NULL OR (author IS NOT NULL AND author = 'ai')),
    CHECK ((basis_doc_id IS NULL AND basis_revision_no IS NULL AND basis_rejection_count IS NULL)
        OR (basis_doc_id IS NOT NULL AND basis_revision_no IS NOT NULL AND basis_rejection_count IS NOT NULL
            AND basis_rejection_count >= 0)),
    CHECK ((stage_state = 'failed' AND fail_cause IS NOT NULL) OR (stage_state <> 'failed' AND fail_cause IS NULL)),
    CHECK (verdict IS NULL OR stage IN ('result_review', 'instruction_review')),
    CHECK ((intent_kind IS NULL AND intent_exec_id IS NULL)
        OR (intent_kind IS NOT NULL AND intent_kind = 'execution'
            AND intent_exec_id IS NOT NULL AND TRIM(intent_exec_id) <> '')
        OR (intent_kind IS NOT NULL AND intent_kind = 'approval' AND intent_exec_id IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_gsa_run_node ON graph_stage_assignments(run_id, node_key, attempt_id);
CREATE INDEX IF NOT EXISTS idx_gsa_intent ON graph_stage_assignments(intent_exec_id);

ALTER TABLE graph_attempt_runs ADD COLUMN assignment_stage TEXT;
ALTER TABLE graph_attempt_runs ADD COLUMN assignment_seq INTEGER;
CREATE INDEX IF NOT EXISTS idx_graph_exec_assignment ON graph_attempt_runs(attempt_id, assignment_stage, assignment_seq);

-- SQLite only (152 pattern): an attempt deleted by the run / group triggers takes its assignments.
-- The trigger is attached to graph_node_attempts, so DROP TABLE graph_stage_assignments does not remove it:
-- the rollback drops it explicitly before the table.
CREATE TRIGGER IF NOT EXISTS trg_graph_stage_assignments_attempt_deleted AFTER DELETE ON graph_node_attempts BEGIN DELETE FROM graph_stage_assignments WHERE attempt_id = OLD.attempt_id; END;

COMMIT;

-- ── DOWN (rollback, DB-1 §3.5) ────────────────────────────────────────────────
-- Only while no Graph run is running: an attempt with assignments reads as a pre-feature attempt afterwards.
-- Run after 165 is rolled back. The trigger first: it sits on graph_node_attempts and survives DROP TABLE.
-- DROP TRIGGER IF EXISTS trg_graph_stage_assignments_attempt_deleted;
-- DROP INDEX IF EXISTS idx_graph_exec_assignment;
-- DROP TABLE IF EXISTS graph_stage_assignments;
-- ── END DOWN ──────────────────────────────────────────────────────────────────
-- ── RE-APPLY PREP (only before running this file again; SQLite 3.35+; then drop its row from migrations) ──
-- ALTER TABLE graph_attempt_runs DROP COLUMN assignment_seq;
-- ALTER TABLE graph_attempt_runs DROP COLUMN assignment_stage;
-- ── END RE-APPLY PREP ─────────────────────────────────────────────────────────
