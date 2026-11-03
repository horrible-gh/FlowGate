-- 0126: immutable links from a document rejection generation to ended effects.
CREATE TABLE IF NOT EXISTS graph_document_recovery_items (
    operation_id VARCHAR(64) NOT NULL,
    group_id VARCHAR(255) NOT NULL,
    node_key VARCHAR(128) NOT NULL,
    generation INTEGER NOT NULL,
    item_kind VARCHAR(32) NOT NULL,
    item_id VARCHAR(255) NOT NULL,
    disposition VARCHAR(32) NOT NULL,
    detail_json TEXT NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    PRIMARY KEY (operation_id, item_kind, item_id)
);
