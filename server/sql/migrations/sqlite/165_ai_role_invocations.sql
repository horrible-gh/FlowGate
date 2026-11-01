-- 165_ai_role_invocations.sql — flowgate.v02.0113 DB-1 (role invocation records)
-- One row per provider launch (and one per admission refusal). History: no REFERENCES at all, so a run, provider,
-- attempt or Flow delete leaves the rows (158 principle), and the ai_invoke_runs retention sweep does not touch them.
-- No secret column: the CLI command contributes only its model value and a keyed hash. Additive only.
-- create_seq is the creation order fixed in memory at T-1/T-2; record_seq is only the storage surrogate key and
-- is never used for ordering. CHECKs are NULL-safe (see 163 header).
--   ai_invoke_runs.invocation_origin_kind / graph_exec_id  the run's origin kind and Graph ledger link (NULL = none).
BEGIN;

CREATE TABLE IF NOT EXISTS ai_role_invocations (
    record_seq                      INTEGER PRIMARY KEY AUTOINCREMENT,
    inv_id                          TEXT NOT NULL,
    create_seq                      INTEGER NOT NULL CHECK (create_seq >= 1),
    ai_run_id                       TEXT,
    launch_no                       INTEGER CHECK (launch_no IS NULL OR launch_no >= 1),
    hop_no                          INTEGER CHECK (hop_no IS NULL OR hop_no >= 1),
    graph_exec_id                   TEXT,
    origin_kind                     TEXT NOT NULL CHECK (origin_kind IN ('card_assigned', 'card_legacy_attempt', 'card_qa', 'non_card')),
    inv_role                        TEXT CHECK (inv_role IS NULL OR inv_role IN ('instruction', 'execution', 'review', 'instruction_review', 'rework', 'qa_response')),
    purpose                         TEXT,
    target_group_id                 TEXT,
    target_graph_run_id             TEXT,
    target_node_key                 TEXT,
    target_attempt_id               TEXT,
    target_stage                    TEXT,
    target_seq                      INTEGER,
    target_doc_id                   TEXT,
    target_doc_revision_no          INTEGER,
    target_unresolved_reason        TEXT,
    qa_item_id                      TEXT,
    qa_question_id                  TEXT,
    qa_asker_ai_run_id              TEXT,
    qa_asker_inv_id                 TEXT,
    qa_card_class                   TEXT CHECK (qa_card_class IS NULL OR qa_card_class IN ('qa_legacy', 'qa_new')),
    round_no                        INTEGER CHECK (round_no IS NULL OR round_no >= 1),
    requested_ai_id                 TEXT,
    role_source                     TEXT,
    legacy_selected_provider_source TEXT,
    actual_ai_id                    TEXT,
    actual_ai_name                  TEXT,
    provider_exec_type              TEXT CHECK (provider_exec_type IS NULL OR provider_exec_type IN ('cli', 'api')),
    provider_kind                   TEXT,
    provider_shell_kind             TEXT,
    provider_fingerprint            TEXT CHECK (provider_fingerprint IS NULL
                                        OR (length(provider_fingerprint) = 64 AND provider_fingerprint NOT GLOB '*[^0-9a-f]*')),
    requested_model_state           TEXT NOT NULL CHECK (requested_model_state IN ('value', 'none', 'unreadable', 'not_parsed')),
    requested_model_value           TEXT,
    requested_model_reason          TEXT,
    confirmed_model_state           TEXT CHECK (confirmed_model_state IS NULL OR confirmed_model_state IN ('confirmed', 'unverifiable')),
    confirmed_model_values_json     TEXT,
    confirmed_model_truncated       INTEGER NOT NULL DEFAULT 0 CHECK (confirmed_model_truncated IN (0, 1)),
    confirmed_coverage              TEXT CHECK (confirmed_coverage IS NULL OR confirmed_coverage IN ('full', 'partial')),
    calls_total                     INTEGER CHECK (calls_total IS NULL OR calls_total >= 0),
    calls_reported                  INTEGER CHECK (calls_reported IS NULL OR calls_reported >= 0),
    calls_absent                    INTEGER CHECK (calls_absent IS NULL OR calls_absent >= 0),
    calls_unreadable                INTEGER CHECK (calls_unreadable IS NULL OR calls_unreadable >= 0),
    calls_failed                    INTEGER CHECK (calls_failed IS NULL OR calls_failed >= 0),
    bad_lines                       INTEGER CHECK (bad_lines IS NULL OR bad_lines >= 0),
    unverifiable_reason             TEXT,
    model_capability                TEXT,
    result                          TEXT NOT NULL CHECK (result IN ('running', 'succeeded', 'admission_refused', 'start_failed', 'call_failed', 'user_stopped')),
    result_detail                   TEXT,
    exit_code                       INTEGER,
    timeout_kind                    TEXT,
    model_call_failure_kind         TEXT,
    model_call_no                   INTEGER,
    model_turn_no                   INTEGER,
    model_http_status               INTEGER,
    run_end_reason                  TEXT,
    run_stop_code                   TEXT,
    link_kind                       TEXT NOT NULL,
    prev_inv_id                     TEXT,
    unit_kind                       TEXT NOT NULL CHECK (unit_kind IN ('assignment', 'legacy', 'qa', 'run')),
    unit_key_hash                   TEXT NOT NULL CHECK (length(unit_key_hash) = 64 AND unit_key_hash NOT GLOB '*[^0-9a-f]*'),
    chain_root_id                   TEXT,
    started_at                      TEXT,
    ended_at                        TEXT,
    created_at                      TEXT NOT NULL,
    updated_at                      TEXT NOT NULL,
    -- CK-V1 … CK-V9 (DB-1 §2.6), written out in full
    CHECK ((ai_run_id IS NULL AND launch_no IS NULL AND result = 'admission_refused')
        OR (ai_run_id IS NOT NULL AND launch_no IS NOT NULL AND result <> 'admission_refused')),
    CHECK ((origin_kind = 'non_card' AND inv_role IS NULL AND purpose IS NULL AND target_group_id IS NULL)
        OR (origin_kind <> 'non_card' AND inv_role IS NOT NULL AND purpose IS NOT NULL AND target_group_id IS NOT NULL)),
    CHECK (origin_kind <> 'card_assigned' OR (target_attempt_id IS NOT NULL AND target_stage IS NOT NULL AND target_seq IS NOT NULL)),
    CHECK ((origin_kind = 'card_qa' AND inv_role IS NOT NULL AND inv_role = 'qa_response'
            AND qa_item_id IS NOT NULL AND qa_card_class IS NOT NULL)
        OR (origin_kind <> 'card_qa' AND (inv_role IS NULL OR inv_role <> 'qa_response')
            AND qa_item_id IS NULL AND qa_question_id IS NULL AND qa_asker_ai_run_id IS NULL
            AND qa_asker_inv_id IS NULL AND qa_card_class IS NULL)),
    CHECK (result <> 'admission_refused'
        OR (actual_ai_id IS NULL AND requested_model_state = 'not_parsed'
            AND confirmed_model_state IS NOT NULL AND confirmed_model_state = 'unverifiable')),
    CHECK ((requested_model_state = 'value' AND requested_model_value IS NOT NULL AND requested_model_reason IS NULL)
        OR (requested_model_state <> 'value' AND requested_model_value IS NULL AND requested_model_reason IS NOT NULL)),
    CHECK ((result = 'running' AND confirmed_model_state IS NULL AND confirmed_model_values_json IS NULL
            AND confirmed_model_truncated = 0 AND confirmed_coverage IS NULL AND unverifiable_reason IS NULL
            AND calls_total IS NULL AND calls_reported IS NULL AND calls_absent IS NULL
            AND calls_unreadable IS NULL AND calls_failed IS NULL AND bad_lines IS NULL)
        OR (result <> 'running' AND confirmed_model_state IS NOT NULL AND confirmed_model_state = 'confirmed'
            AND confirmed_model_values_json IS NOT NULL AND confirmed_coverage IS NOT NULL AND unverifiable_reason IS NULL
            AND (confirmed_model_truncated = 0 OR confirmed_coverage = 'partial'))
        OR (result <> 'running' AND confirmed_model_state IS NOT NULL AND confirmed_model_state = 'unverifiable'
            AND confirmed_model_values_json IS NULL AND confirmed_model_truncated = 0 AND confirmed_coverage IS NULL
            AND unverifiable_reason IS NOT NULL AND TRIM(unverifiable_reason) <> '')),
    CHECK ((result = 'running' AND ended_at IS NULL) OR (result <> 'running' AND ended_at IS NOT NULL)),
    CHECK ((model_call_failure_kind IS NULL AND model_call_no IS NULL AND model_turn_no IS NULL AND model_http_status IS NULL)
        OR (model_call_failure_kind IS NOT NULL AND model_call_no IS NOT NULL AND model_turn_no IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ari_inv ON ai_role_invocations(inv_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ari_run_launch ON ai_role_invocations(ai_run_id, launch_no);
CREATE INDEX IF NOT EXISTS idx_ari_unit ON ai_role_invocations(unit_key_hash, create_seq, inv_id);
CREATE INDEX IF NOT EXISTS idx_ari_exec ON ai_role_invocations(graph_exec_id, create_seq);
CREATE INDEX IF NOT EXISTS idx_ari_target_node ON ai_role_invocations(target_group_id, target_node_key, create_seq);

ALTER TABLE ai_invoke_runs ADD COLUMN invocation_origin_kind TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN graph_exec_id TEXT;
CREATE INDEX IF NOT EXISTS idx_air_graph_exec ON ai_invoke_runs(graph_exec_id);

COMMIT;

-- ── DOWN (rollback, DB-1 §3.5) ────────────────────────────────────────────────
-- Not advised: loses every role invocation record. Default rollback = revert the code only. Run first (165 → 164 → 163).
-- DROP INDEX IF EXISTS idx_air_graph_exec;
-- DROP TABLE IF EXISTS ai_role_invocations;
-- ── END DOWN ──────────────────────────────────────────────────────────────────
-- ── RE-APPLY PREP (only before running this file again; SQLite 3.35+; then drop its row from migrations) ──
-- ALTER TABLE ai_invoke_runs DROP COLUMN graph_exec_id;
-- ALTER TABLE ai_invoke_runs DROP COLUMN invocation_origin_kind;
-- ── END RE-APPLY PREP ─────────────────────────────────────────────────────────
