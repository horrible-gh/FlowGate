-- 167_attempt_report_baselines.sql — flowgate.v02.0113 0038-T T#3 (0030-M §3.1, 0002-D 개정 §3.6-0-2: the report baseline) (MySQL/MariaDB)
-- Additive only.
--   graph_attempt_report_baselines  one row per attempt: the card's report as it was when the attempt of a card that
--                                   uses an instruction opened with none written (its report line document, revision,
--                                   body digest and last body save). The result stage takes only a report saved after
--                                   it; the baseline report itself stays as the card's history. carried_from: the
--                                   earlier attempt of the same run whose baseline a replacing attempt kept.
--                                   No foreign key (a history row): a run or Flow delete leaves it.
-- Rows are not touched; safe to run again.
CREATE TABLE IF NOT EXISTS graph_attempt_report_baselines (
    attempt_id     VARCHAR(191) NOT NULL PRIMARY KEY,
    run_id         VARCHAR(191) NOT NULL,
    group_id       VARCHAR(191) NOT NULL,
    node_key       VARCHAR(191) NOT NULL,
    doc_id         VARCHAR(191),
    revision_no    INT,
    body_sha256    VARCHAR(64),
    body_event_id  BIGINT,
    carried_from   VARCHAR(191),
    recorded_at    VARCHAR(40)  NOT NULL,
    INDEX idx_graph_attempt_report_baselines_node (run_id, node_key, recorded_at)
);

-- ── DOWN (rollback) ───────────────────────────────────────────────────────────
-- Not advised: loses the baselines (an open attempt would then take its baseline report). Default rollback = revert
-- the code only.
-- DROP TABLE IF EXISTS graph_attempt_report_baselines;
-- ── END DOWN ──────────────────────────────────────────────────────────────────
