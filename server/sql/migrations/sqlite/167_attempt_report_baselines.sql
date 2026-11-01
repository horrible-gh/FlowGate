-- 167_attempt_report_baselines.sql — flowgate.v02.0113 0038-T T#3 (0030-M §3.1, 0002-D 개정 §3.6-0-2: the report baseline) (SQLite)
-- Additive only.
--   graph_attempt_report_baselines  one row per attempt: the card's report as it was when the attempt of a card that
--                                   uses an instruction opened with none written (its report line document, revision,
--                                   body digest and last body save). The result stage takes only a report saved after
--                                   it; the baseline report itself stays as the card's history. carried_from: the
--                                   earlier attempt of the same run whose baseline a replacing attempt kept.
--                                   No REFERENCES (a history row, like ai_role_invocations): a run or Flow delete
--                                   leaves it.
-- Rows are not touched; safe to run again.
CREATE TABLE IF NOT EXISTS graph_attempt_report_baselines (
    attempt_id     TEXT NOT NULL PRIMARY KEY,
    run_id         TEXT NOT NULL,
    group_id       TEXT NOT NULL,
    node_key       TEXT NOT NULL,
    doc_id         TEXT,
    revision_no    INTEGER,
    body_sha256    TEXT,
    body_event_id  INTEGER,
    carried_from   TEXT,
    recorded_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_graph_attempt_report_baselines_node
    ON graph_attempt_report_baselines (run_id, node_key, recorded_at);

-- ── DOWN (rollback) ───────────────────────────────────────────────────────────
-- Not advised: loses the baselines (an open attempt would then take its baseline report). Default rollback = revert
-- the code only.
-- DROP TABLE IF EXISTS graph_attempt_report_baselines;
-- ── END DOWN ──────────────────────────────────────────────────────────────────
