-- 141_graph_layout_identity.sql — flowgate.v02.0045 DB (S2)
-- Graph Workflow: Flow guard row, Canvas layout (positions per node identity, viewport per user),
-- node identity mapping record for v0.1 sequence rows, retired node keys. Additive only.
BEGIN;

CREATE TABLE IF NOT EXISTS graph_flow_guards (
    group_id    TEXT PRIMARY KEY REFERENCES groups(group_id) ON DELETE CASCADE,
    guard_seq   INTEGER NOT NULL DEFAULT 0 CHECK (guard_seq >= 0),
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS graph_node_positions (
    group_id    TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    node_key    TEXT NOT NULL,
    x           REAL NOT NULL,
    y           REAL NOT NULL,
    updated_at  TEXT NOT NULL,
    updated_by  TEXT,
    PRIMARY KEY (group_id, node_key)
);

CREATE TABLE IF NOT EXISTS graph_viewports (
    group_id    TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    x           REAL NOT NULL,
    y           REAL NOT NULL,
    zoom        REAL NOT NULL CHECK (zoom > 0),
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (group_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_graph_viewports_user ON graph_viewports(user_id);

CREATE TABLE IF NOT EXISTS graph_identity_records (
    group_id      TEXT PRIMARY KEY REFERENCES groups(group_id) ON DELETE CASCADE,
    sequence_tag  TEXT,
    recorded_at   TEXT NOT NULL,
    recorded_by   TEXT
);

CREATE TABLE IF NOT EXISTS graph_identity_rows (
    group_id          TEXT NOT NULL REFERENCES graph_identity_records(group_id) ON DELETE CASCADE,
    node_key          TEXT NOT NULL,
    identity_source   TEXT NOT NULL CHECK (identity_source IN ('document', 'sequence_row', 'sequence_slot')),
    item_id           INTEGER NOT NULL,
    item_seq          INTEGER NOT NULL,
    item_type         TEXT NOT NULL,
    label             TEXT NOT NULL,
    sort_order        INTEGER NOT NULL,
    source_wp_card_id TEXT,
    result_doc_id     TEXT,
    is_protected      INTEGER NOT NULL DEFAULT 0 CHECK (is_protected IN (0, 1)),
    PRIMARY KEY (group_id, node_key),
    CHECK (identity_source <> 'document' OR result_doc_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS graph_retired_node_keys (
    group_id    TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    node_key    TEXT NOT NULL,
    reason      TEXT NOT NULL CHECK (reason IN ('rematch', 'supersede', 'root_binding', 'draft_removed', 'revision_removed')),
    retired_at  TEXT NOT NULL,
    PRIMARY KEY (group_id, node_key)
);

-- SQLite only (§5.3): FK-independent cleanup for hard deletes run on a connection with foreign_keys OFF.
CREATE TRIGGER IF NOT EXISTS trg_graph_layout_group_deleted AFTER DELETE ON groups BEGIN DELETE FROM graph_identity_rows WHERE group_id = OLD.group_id; DELETE FROM graph_identity_records WHERE group_id = OLD.group_id; DELETE FROM graph_retired_node_keys WHERE group_id = OLD.group_id; DELETE FROM graph_viewports WHERE group_id = OLD.group_id; DELETE FROM graph_node_positions WHERE group_id = OLD.group_id; DELETE FROM graph_flow_guards WHERE group_id = OLD.group_id; END;
CREATE TRIGGER IF NOT EXISTS trg_graph_viewports_user_deleted AFTER DELETE ON users BEGIN DELETE FROM graph_viewports WHERE user_id = OLD.user_id; END;

COMMIT;
