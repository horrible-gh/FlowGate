-- 166_ai_role_first_setup.sql — flowgate.v02.0113 T#2 (0030-M §2, 0002-D 개정 §3.2-A: the first AI setup's bulk apply)
-- Additive only. Two things:
--   graph_node_role_slots.source  admits 'first_setup': a role marker written when the account's first AI setup was
--                                 saved and applied to every AI-settings card of the Flow it was saved for.
--   ai_role_setup_requests        one row per applied first-setup request (account, request id): the answer of the
--                                 request, so the same request sent again answers it once more and writes nothing.
--                                 No REFERENCES (a history row, like ai_role_invocations): a Flow or account delete
--                                 leaves it.
--
-- SQLite cannot widen a CHECK in place, so graph_node_role_slots is rebuilt: every column, every row is kept. Nothing
-- references graph_node_role_slots; its cleanup trigger sits on groups and names the table in its body, so it is
-- dropped before the old table goes and created again (same name, same body) after the replacement is renamed into
-- place. Safe to run again: the replacement name is cleared first and a second run rebuilds the same table.
PRAGMA foreign_keys=OFF;
BEGIN;
DROP TABLE IF EXISTS graph_node_role_slots_new;

CREATE TABLE graph_node_role_slots_new (
    group_id       TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    node_key       TEXT NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('instruction', 'execution', 'review')),
    kind           TEXT NOT NULL CHECK (kind IN ('assigned', 'not_used')),
    provider_id    TEXT,
    provider_name  TEXT,
    source         TEXT NOT NULL CHECK (source IN ('creation_copy', 'wp_plan', 'pending_fill', 'card_explicit', 'first_setup')),
    set_by         TEXT,
    set_at         TEXT NOT NULL,
    PRIMARY KEY (group_id, node_key, role),
    CHECK ((kind = 'assigned' AND provider_id IS NOT NULL AND TRIM(provider_id) <> '')
        OR (kind = 'not_used' AND provider_id IS NULL AND provider_name IS NULL)),
    CHECK ((source = 'pending_fill' AND set_by IS NULL) OR (source <> 'pending_fill' AND set_by IS NOT NULL))
);

INSERT INTO graph_node_role_slots_new (group_id, node_key, role, kind, provider_id, provider_name, source, set_by, set_at)
SELECT group_id, node_key, role, kind, provider_id, provider_name, source, set_by, set_at FROM graph_node_role_slots;

DROP TRIGGER IF EXISTS trg_graph_node_role_slots_group_deleted;
DROP TABLE graph_node_role_slots;
ALTER TABLE graph_node_role_slots_new RENAME TO graph_node_role_slots;
CREATE TRIGGER IF NOT EXISTS trg_graph_node_role_slots_group_deleted AFTER DELETE ON groups BEGIN DELETE FROM graph_node_role_slots WHERE group_id = OLD.group_id; END;

CREATE TABLE IF NOT EXISTS ai_role_setup_requests (
    user_id      TEXT NOT NULL,
    request_id   TEXT NOT NULL,
    flow_id      TEXT NOT NULL,
    result_json  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (user_id, request_id)
);

COMMIT;
PRAGMA foreign_keys=ON;

-- ── DOWN (rollback) ───────────────────────────────────────────────────────────
-- Not advised: loses the request answers. Default rollback = revert the code only (a 'first_setup' slot keeps
-- resolving as a store error until its source changes; the widened CHECK stays).
-- DROP TABLE IF EXISTS ai_role_setup_requests;
-- ── END DOWN ──────────────────────────────────────────────────────────────────
