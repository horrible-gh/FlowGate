-- 145_graph_node_documents.sql — flowgate.v02.0053 T#1 (card documents) (MySQL/MariaDB)
CREATE TABLE IF NOT EXISTS graph_node_documents (
    group_id    VARCHAR(191) NOT NULL,
    node_key    VARCHAR(200) NOT NULL,
    slot        VARCHAR(32) NOT NULL CHECK (slot IN ('requirement', 'delegation', 'plan', 'deploy',
                                                     'instruction', 'scenario', 'report')),
    doc_type    VARCHAR(16) NOT NULL,
    doc_id      VARCHAR(191) NOT NULL,
    owned       INTEGER NOT NULL DEFAULT 1 CHECK (owned IN (0, 1)),
    ordinal     INTEGER NOT NULL DEFAULT 0,
    created_at  VARCHAR(40) NOT NULL,
    created_by  VARCHAR(191) NULL,
    PRIMARY KEY (group_id, node_key, slot, doc_type),
    INDEX idx_graph_node_documents_doc (doc_id),
    CONSTRAINT fk_graph_node_docs_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);
