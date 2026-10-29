-- 165_ai_role_invocations.sql — flowgate.v02.0113 DB-1 (role invocation records) (MySQL/MariaDB)
-- One row per provider launch (and one per admission refusal). History: no REFERENCES at all, so a run, provider,
-- attempt or Flow delete leaves the rows (158 principle), and the ai_invoke_runs retention sweep does not touch them.
-- No secret column: the CLI command contributes only its model value and a keyed hash. Additive only.
-- create_seq is the creation order fixed in memory at T-1/T-2; record_seq is only the storage surrogate key and
-- is never used for ordering. CHECKs are NULL-safe (see 163 header). The two hashes are ascii_bin so REGEXP is
-- case-sensitive (127 / 144 MySQL form).
--   ai_invoke_runs.invocation_origin_kind / graph_exec_id  the run's origin kind and Graph ledger link (NULL = none).

CREATE TABLE IF NOT EXISTS ai_role_invocations (
    record_seq                      BIGINT PRIMARY KEY AUTO_INCREMENT,
    inv_id                          VARCHAR(255) NOT NULL,
    create_seq                      BIGINT NOT NULL CHECK (create_seq >= 1),
    ai_run_id                       VARCHAR(191) NULL,
    launch_no                       INTEGER NULL CHECK (launch_no IS NULL OR launch_no >= 1),
    hop_no                          INTEGER NULL CHECK (hop_no IS NULL OR hop_no >= 1),
    graph_exec_id                   VARCHAR(64) NULL,
    origin_kind                     VARCHAR(24) NOT NULL CHECK (origin_kind IN ('card_assigned', 'card_legacy_attempt', 'card_qa', 'non_card')),
    inv_role                        VARCHAR(24) NULL CHECK (inv_role IS NULL OR inv_role IN ('instruction', 'execution', 'review', 'instruction_review', 'rework', 'qa_response')),
    purpose                         VARCHAR(32) NULL,
    target_group_id                 VARCHAR(191) NULL,
    target_graph_run_id             VARCHAR(64) NULL,
    target_node_key                 VARCHAR(200) NULL,
    target_attempt_id               VARCHAR(64) NULL,
    target_stage                    VARCHAR(24) NULL,
    target_seq                      INTEGER NULL,
    target_doc_id                   VARCHAR(191) NULL,
    target_doc_revision_no          INTEGER NULL,
    target_unresolved_reason        VARCHAR(32) NULL,
    qa_item_id                      VARCHAR(191) NULL,
    qa_question_id                  VARCHAR(191) NULL,
    qa_asker_ai_run_id              VARCHAR(191) NULL,
    qa_asker_inv_id                 VARCHAR(255) NULL,
    qa_card_class                   VARCHAR(16) NULL CHECK (qa_card_class IS NULL OR qa_card_class IN ('qa_legacy', 'qa_new')),
    round_no                        INTEGER NULL CHECK (round_no IS NULL OR round_no >= 1),
    requested_ai_id                 VARCHAR(191) NULL,
    role_source                     VARCHAR(32) NULL,
    legacy_selected_provider_source VARCHAR(32) NULL,
    actual_ai_id                    VARCHAR(191) NULL,
    actual_ai_name                  VARCHAR(100) NULL,
    provider_exec_type              VARCHAR(8) NULL CHECK (provider_exec_type IS NULL OR provider_exec_type IN ('cli', 'api')),
    provider_kind                   VARCHAR(32) NULL,
    provider_shell_kind             VARCHAR(32) NULL,
    provider_fingerprint            CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL
                                        CHECK (provider_fingerprint IS NULL
                                            OR (CHAR_LENGTH(provider_fingerprint) = 64 AND provider_fingerprint REGEXP '^[0-9a-f]{64}$')),
    requested_model_state           VARCHAR(16) NOT NULL CHECK (requested_model_state IN ('value', 'none', 'unreadable', 'not_parsed')),
    requested_model_value           VARCHAR(200) NULL,
    requested_model_reason          VARCHAR(32) NULL,
    confirmed_model_state           VARCHAR(16) NULL CHECK (confirmed_model_state IS NULL OR confirmed_model_state IN ('confirmed', 'unverifiable')),
    confirmed_model_values_json     TEXT NULL,
    confirmed_model_truncated       TINYINT(1) NOT NULL DEFAULT 0 CHECK (confirmed_model_truncated IN (0, 1)),
    confirmed_coverage              VARCHAR(8) NULL CHECK (confirmed_coverage IS NULL OR confirmed_coverage IN ('full', 'partial')),
    calls_total                     INTEGER NULL CHECK (calls_total IS NULL OR calls_total >= 0),
    calls_reported                  INTEGER NULL CHECK (calls_reported IS NULL OR calls_reported >= 0),
    calls_absent                    INTEGER NULL CHECK (calls_absent IS NULL OR calls_absent >= 0),
    calls_unreadable                INTEGER NULL CHECK (calls_unreadable IS NULL OR calls_unreadable >= 0),
    calls_failed                    INTEGER NULL CHECK (calls_failed IS NULL OR calls_failed >= 0),
    bad_lines                       INTEGER NULL CHECK (bad_lines IS NULL OR bad_lines >= 0),
    unverifiable_reason             VARCHAR(32) NULL,
    model_capability                VARCHAR(24) NULL,
    result                          VARCHAR(24) NOT NULL CHECK (result IN ('running', 'succeeded', 'admission_refused', 'start_failed', 'call_failed', 'user_stopped')),
    result_detail                   VARCHAR(64) NULL,
    exit_code                       INTEGER NULL,
    timeout_kind                    VARCHAR(32) NULL,
    model_call_failure_kind         VARCHAR(32) NULL,
    model_call_no                   INTEGER NULL,
    model_turn_no                   INTEGER NULL,
    model_http_status               INTEGER NULL,
    run_end_reason                  VARCHAR(64) NULL,
    run_stop_code                   VARCHAR(64) NULL,
    link_kind                       VARCHAR(32) NOT NULL,
    prev_inv_id                     VARCHAR(255) NULL,
    unit_kind                       VARCHAR(16) NOT NULL CHECK (unit_kind IN ('assignment', 'legacy', 'qa', 'run')),
    unit_key_hash                   CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
                                        CHECK (CHAR_LENGTH(unit_key_hash) = 64 AND unit_key_hash REGEXP '^[0-9a-f]{64}$'),
    chain_root_id                   VARCHAR(255) NULL,
    started_at                      VARCHAR(40) NULL,
    ended_at                        VARCHAR(40) NULL,
    created_at                      VARCHAR(40) NOT NULL,
    updated_at                      VARCHAR(40) NOT NULL,
    UNIQUE KEY ux_ari_inv (inv_id),
    UNIQUE KEY ux_ari_run_launch (ai_run_id, launch_no),
    INDEX idx_ari_unit (unit_key_hash, create_seq, inv_id),
    INDEX idx_ari_exec (graph_exec_id, create_seq),
    INDEX idx_ari_target_node (target_group_id, target_node_key, create_seq),
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

ALTER TABLE ai_invoke_runs ADD COLUMN invocation_origin_kind VARCHAR(24) NULL;
ALTER TABLE ai_invoke_runs ADD COLUMN graph_exec_id VARCHAR(64) NULL;
CREATE INDEX idx_air_graph_exec ON ai_invoke_runs(graph_exec_id);

-- ── DOWN (rollback, DB-1 §3.5) ────────────────────────────────────────────────
-- Not advised: loses every role invocation record. Default rollback = revert the code only. Run first (165 → 164 → 163).
-- MySQL needs the table on DROP INDEX and has no DROP INDEX IF EXISTS (8.0).
-- DROP INDEX idx_air_graph_exec ON ai_invoke_runs;
-- DROP TABLE IF EXISTS ai_role_invocations;
-- ── END DOWN ──────────────────────────────────────────────────────────────────
-- ── RE-APPLY PREP (only before running this file again; then drop its row from migrations) ──
-- ALTER TABLE ai_invoke_runs DROP COLUMN graph_exec_id;
-- ALTER TABLE ai_invoke_runs DROP COLUMN invocation_origin_kind;
-- ── END RE-APPLY PREP ─────────────────────────────────────────────────────────
