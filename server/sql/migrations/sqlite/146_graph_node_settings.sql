-- 146_graph_node_settings.sql — flowgate.v02.0053 T#2 (card run settings)
-- The settings of each Designer card (Node), one row per (Flow, node, field): a card keeps its
-- settings whether it is only in the draft or in confirmed revisions, and each value is saved on
-- its own. value_json is the JSON value; no row = the engine default. Additive only.
BEGIN;

CREATE TABLE IF NOT EXISTS graph_node_settings (
    group_id    TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    node_key    TEXT NOT NULL,
    field       TEXT NOT NULL,
    value_json  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    updated_by  TEXT,
    PRIMARY KEY (group_id, node_key, field)
);

-- SQLite only (DB0010 §5.3 pattern): FK-independent cleanup for hard deletes run with foreign_keys OFF.
CREATE TRIGGER IF NOT EXISTS trg_graph_node_settings_group_deleted AFTER DELETE ON groups BEGIN DELETE FROM graph_node_settings WHERE group_id = OLD.group_id; END;

COMMIT;
