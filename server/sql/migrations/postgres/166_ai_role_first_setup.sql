-- 166_ai_role_first_setup.sql — flowgate.v02.0113 T#2 (0030-M §2, 0002-D 개정 §3.2-A: the first AI setup's bulk apply) (PostgreSQL)
-- Additive only. graph_node_role_slots.source admits 'first_setup' (a role marker of the first AI setup's bulk apply);
-- ai_role_setup_requests keeps the answer of each applied first-setup request (account, request id) so a request sent
-- again answers once more and writes nothing. No REFERENCES on the new table (a history row).
-- The CHECKs that read graph_node_role_slots.source are found in the catalog and dropped (the column's value list and
-- the pending_fill / set_by pairing, which names source too), then added again with the list widened (the same shape
-- as 161 for operation_job.kind). Rows are not touched; safe to run again.
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT DISTINCT c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_attribute a
          ON a.attrelid = t.oid
         AND a.attnum = ANY (c.conkey)
        WHERE n.nspname = current_schema()
          AND t.relname = 'graph_node_role_slots'
          AND c.contype = 'c'
          AND a.attname = 'source'
    LOOP
        EXECUTE format('ALTER TABLE %I.graph_node_role_slots DROP CONSTRAINT %I', current_schema(), r.conname);
    END LOOP;
END $$;

ALTER TABLE graph_node_role_slots
    ADD CONSTRAINT graph_node_role_slots_source_check
    CHECK (source IN ('creation_copy', 'wp_plan', 'pending_fill', 'card_explicit', 'first_setup'));
ALTER TABLE graph_node_role_slots
    ADD CONSTRAINT graph_node_role_slots_setter_check
    CHECK ((source = 'pending_fill' AND set_by IS NULL) OR (source <> 'pending_fill' AND set_by IS NOT NULL));

CREATE TABLE IF NOT EXISTS ai_role_setup_requests (
    user_id      TEXT NOT NULL,
    request_id   TEXT NOT NULL,
    flow_id      TEXT NOT NULL,
    result_json  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (user_id, request_id)
);

-- ── DOWN (rollback) ───────────────────────────────────────────────────────────
-- Not advised: loses the request answers. Default rollback = revert the code only.
-- DROP TABLE IF EXISTS ai_role_setup_requests;
-- ── END DOWN ──────────────────────────────────────────────────────────────────
