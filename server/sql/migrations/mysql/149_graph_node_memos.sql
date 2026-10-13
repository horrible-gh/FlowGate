-- 149_graph_node_memos.sql — flowgate.v02.0056 T#3 (v0.2 card memos) (MySQL/MariaDB)
-- body is MEDIUMTEXT: a memo body holds up to 20,000 characters, 80,000 bytes in utf8mb4, over TEXT's 65,535.
-- A database that ran this file while body was TEXT is widened by 150_graph_node_memos_body_mediumtext.
CREATE TABLE IF NOT EXISTS graph_node_memos (
    memo_id     VARCHAR(64) NOT NULL,
    group_id    VARCHAR(191) NOT NULL,
    node_key    VARCHAR(200) NOT NULL,
    title       VARCHAR(200) NOT NULL,
    body        MEDIUMTEXT NOT NULL,
    ai_ref      INTEGER NOT NULL DEFAULT 0 CHECK (ai_ref IN (0, 1)),
    created_by  VARCHAR(191) NULL,
    created_at  VARCHAR(40) NOT NULL,
    updated_at  VARCHAR(40) NOT NULL,
    updated_by  VARCHAR(191) NULL,
    PRIMARY KEY (memo_id),
    INDEX idx_graph_node_memos_card (group_id, node_key),
    CONSTRAINT fk_graph_node_memos_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);
