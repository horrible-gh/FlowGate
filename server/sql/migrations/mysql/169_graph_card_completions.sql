-- 169_graph_card_completions.sql — flowgate.v02.0119 T#2 (T-1: the v0.2 card completion service) (MySQL/MariaDB)
-- Additive only (new tables; no existing row or column is touched).
--   graph_card_completions       one row per completion of a card's node attempt: the only source of "this node is done"
--                                in a Graph run (``flowgate.card_completion``). Unique (run_id, node_key, attempt_id,
--                                result_doc_id, generation) -- the idempotency key (run_id, node_key, attempt_id,
--                                result_doc_id) holds one live row: a repeated review pass, a retried job or two run
--                                commands racing write one row and the second asker reads it back. generation counts the
--                                key's rows: a key whose latest row was superseded by a newer revision of the same result
--                                (a person's resubmission in the same attempt) takes the new submission's completion as
--                                its next generation; a revoked row (Time Machine) is never followed by another. attempt_id is '' for a
--                                node run opened before migration 151 (no attempt row). cause: ai_review_passed |
--                                no_review_submitted | human_submitted; actor_kind: system | ai | user; actor_id: the real
--                                provider id (an AI), 'system', or the user; requested_by: the run's issuer only.
--                                status: recorded -> applied (the node is done with it) | superseded (an older verdict:
--                                the node's result, attempt or revision moved, or the node is not in review) | revoked
--                                (a Time Machine rewind took the node back; the row is kept). No REFERENCES (a history
--                                row, like graph_attempt_report_baselines): a run or Flow delete leaves it.
--   graph_card_completion_meta   key/value of the service: installed_at = the first server start with the service (a run
--                                started before it is an existing run whose already approved result counts as done when
--                                read, without a migration of its rows).
-- Rows are not touched; safe to run again.
CREATE TABLE IF NOT EXISTS graph_card_completions (
    completion_id      VARCHAR(191) NOT NULL PRIMARY KEY,
    run_id             VARCHAR(191) NOT NULL,
    group_id           VARCHAR(191) NOT NULL,
    node_key           VARCHAR(191) NOT NULL,
    attempt_id         VARCHAR(191) NOT NULL,
    result_doc_id      VARCHAR(191) NOT NULL,
    generation         INT          NOT NULL DEFAULT 0,
    revision_no        INT,
    cause              VARCHAR(40)  NOT NULL,
    actor_kind         VARCHAR(16)  NOT NULL,
    actor_id           VARCHAR(191),
    requested_by       VARCHAR(191),
    verdict_id         VARCHAR(191),
    status             VARCHAR(16)  NOT NULL,
    status_reason      VARCHAR(191),
    created_at         VARCHAR(40)  NOT NULL,
    applied_at         VARCHAR(40),
    applied_event_seq  BIGINT,
    status_changed_at  VARCHAR(40),
    UNIQUE KEY ux_graph_card_completions_key (run_id, node_key, attempt_id, result_doc_id, generation),
    INDEX idx_graph_card_completions_node (run_id, node_key, status)
);
CREATE TABLE IF NOT EXISTS graph_card_completion_meta (
    meta_key    VARCHAR(64)  NOT NULL PRIMARY KEY,
    meta_value  VARCHAR(191) NOT NULL
);

-- ── DOWN (rollback) ───────────────────────────────────────────────────────────
-- Not advised: loses the completion records (the nodes of the runs since would no longer read as done). Default
-- rollback = revert the code only.
-- DROP TABLE IF EXISTS graph_card_completion_meta;
-- DROP TABLE IF EXISTS graph_card_completions;
-- ── END DOWN ──────────────────────────────────────────────────────────────────
