-- 155_graph_node_title_states.sql — flowgate.v02.0064 T-B4b (DB0007 §2.1 / §3.2) (PostgreSQL)
-- The title state of a Designer card (Node), keyed by the card (Flow, node_key) like its settings and memos:
-- the title a person or the card's AI gave it, where it came from, whether a person ever saved one, the AI title
-- opportunity and the state version. No row = not judged yet (U). node_key has no FK (nodes change with the
-- draft / revisions; a deleted node's row stays, 0062 L0006 §5). close_reason is an open key (no value CHECK).
-- Additive; rollback: DROP TABLE graph_node_title_states (only with the feature off, DB0007 §3.4).

CREATE TABLE IF NOT EXISTS graph_node_title_states (
    group_id           TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    node_key           TEXT NOT NULL,
    title              TEXT NULL,
    title_source       TEXT NOT NULL DEFAULT 'none',
    human_edited       INTEGER NOT NULL DEFAULT 0 CHECK (human_edited IN (0, 1)),
    opportunity_state  TEXT NOT NULL DEFAULT 'unused' CHECK (opportunity_state IN ('unused', 'claimed', 'closed')),
    close_reason       TEXT NULL,
    claimed_token_id   TEXT NULL,
    claimed_ai_run_id  TEXT NULL,
    claimed_at         TEXT NULL,
    closed_at          TEXT NULL,
    version            INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    updated_by         TEXT NULL,
    PRIMARY KEY (group_id, node_key),
    CONSTRAINT ck_gnts_title_len CHECK (title IS NULL OR (char_length(title) BETWEEN 1 AND 30)),
    CONSTRAINT ck_gnts_source CHECK (
        (title_source = 'none'  AND title IS NULL     AND human_edited = 0) OR
        (title_source = 'ai'    AND title IS NOT NULL AND human_edited = 0) OR
        (title_source = 'human' AND title IS NOT NULL AND human_edited = 1)),
    CONSTRAINT ck_gnts_opportunity CHECK (
        (opportunity_state = 'unused'  AND close_reason IS NULL     AND claimed_token_id IS NULL) OR
        (opportunity_state = 'claimed' AND close_reason IS NULL     AND claimed_token_id IS NOT NULL) OR
        (opportunity_state = 'closed'  AND close_reason IS NOT NULL AND closed_at IS NOT NULL))
);
