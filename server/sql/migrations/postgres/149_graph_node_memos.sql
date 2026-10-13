-- 149_graph_node_memos.sql — flowgate.v02.0056 T#3 (v0.2 card memos) (PostgreSQL)
-- The memos a person keeps on a Designer card (Node), keyed by the card like its settings (146): a title, a
-- body and whether the card's AI reads it (ai_ref, off by default). Not documents: no documents row, no file,
-- no numbering, no workflow event, so writing one never closes an R / B (the v0.1 M memo did). Additive only.

CREATE TABLE IF NOT EXISTS graph_node_memos (
    memo_id     TEXT PRIMARY KEY,
    group_id    TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    node_key    TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    ai_ref      INTEGER NOT NULL DEFAULT 0 CHECK (ai_ref IN (0, 1)),
    created_by  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    updated_by  TEXT
);

CREATE INDEX IF NOT EXISTS idx_graph_node_memos_card ON graph_node_memos(group_id, node_key);
