-- 166_ai_role_first_setup.sql — flowgate.v02.0113 T#2 (0030-M §2, 0002-D 개정 §3.2-A: the first AI setup's bulk apply) (MySQL/MariaDB)
-- Additive only. graph_node_role_slots.source admits 'first_setup' (a role marker of the first AI setup's bulk apply);
-- ai_role_setup_requests keeps the answer of each applied first-setup request (account, request id) so a request sent
-- again answers once more and writes nothing. No foreign key on the new table (a history row).
-- The CHECK that holds the source value list is found by name, dropped and added again with the new value (the same
-- shape as 161 for operation_job.kind). The pending_fill / set_by CHECK does not change. Rows are not touched; safe to
-- run again.
SET @fg_chk := (
  SELECT tc.CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS tc
  JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME
  WHERE tc.CONSTRAINT_SCHEMA=DATABASE() AND tc.TABLE_NAME='graph_node_role_slots' AND tc.CONSTRAINT_TYPE='CHECK'
    AND cc.CHECK_CLAUSE LIKE '%source%' AND cc.CHECK_CLAUSE LIKE '%creation_copy%' LIMIT 1
);
SET @fg_sql := IF(@fg_chk IS NULL, 'SELECT 1', CONCAT('ALTER TABLE graph_node_role_slots DROP CHECK `', REPLACE(@fg_chk,'`','``'), '`'));
PREPARE fg_stmt FROM @fg_sql; EXECUTE fg_stmt; DEALLOCATE PREPARE fg_stmt;

ALTER TABLE graph_node_role_slots ADD CONSTRAINT graph_node_role_slots_source_check CHECK (source IN ('creation_copy', 'wp_plan', 'pending_fill', 'card_explicit', 'first_setup'));

CREATE TABLE IF NOT EXISTS ai_role_setup_requests (
    user_id      VARCHAR(191) NOT NULL,
    request_id   VARCHAR(64)  NOT NULL,
    flow_id      VARCHAR(191) NOT NULL,
    result_json  LONGTEXT     NOT NULL,
    created_at   VARCHAR(40)  NOT NULL,
    PRIMARY KEY (user_id, request_id)
);

-- ── DOWN (rollback) ───────────────────────────────────────────────────────────
-- Not advised: loses the request answers. Default rollback = revert the code only.
-- DROP TABLE IF EXISTS ai_role_setup_requests;
-- ── END DOWN ──────────────────────────────────────────────────────────────────
