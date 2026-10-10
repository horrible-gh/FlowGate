-- 145_graph_node_documents.sql — flowgate.v02.0053 T#1 (card documents)
-- The documents of each card's lines (요구사항 / 작업계획 · 전개 / 지시서 · 레포트 / 테스트 시나리오):
-- created when a card is placed in a draft, rebound to a run result, removed with a draft-only card.
-- doc_id names a v0.1 document without a FK (the document may be deleted by v0.1). Additive only.
-- doc_type is part of the key: a node retyped in the draft keeps the lines its confirmed revision shows.
BEGIN;

CREATE TABLE IF NOT EXISTS graph_node_documents (
    group_id    TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    node_key    TEXT NOT NULL,
    slot        TEXT NOT NULL CHECK (slot IN ('requirement', 'delegation', 'plan', 'deploy',
                                              'instruction', 'scenario', 'report')),
    doc_type    TEXT NOT NULL,
    doc_id      TEXT NOT NULL,
    owned       INTEGER NOT NULL DEFAULT 1 CHECK (owned IN (0, 1)),
    ordinal     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    created_by  TEXT,
    PRIMARY KEY (group_id, node_key, slot, doc_type)
);

CREATE INDEX IF NOT EXISTS idx_graph_node_documents_doc ON graph_node_documents(doc_id);

-- SQLite only (DB0010 §5.3 pattern): FK-independent cleanup for hard deletes run with foreign_keys OFF.
CREATE TRIGGER IF NOT EXISTS trg_graph_node_documents_group_deleted AFTER DELETE ON groups BEGIN DELETE FROM graph_node_documents WHERE group_id = OLD.group_id; END;

COMMIT;
