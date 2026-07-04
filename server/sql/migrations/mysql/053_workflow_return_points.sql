-- 053_workflow_return_points.sql
-- Reverse time-machine return point snapshots.

CREATE TABLE IF NOT EXISTS workflow_return_points (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    group_id   VARCHAR(191) NOT NULL UNIQUE,
    front_seq  INTEGER NOT NULL,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL,
    CONSTRAINT fk_wrp_group
        FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS workflow_return_point_docs (
    return_point_id INT NOT NULL,
    doc_id          VARCHAR(191) NOT NULL,
    seq             INTEGER NOT NULL,
    prev_status     TEXT    NOT NULL,
    fingerprint     TEXT    NOT NULL,
    PRIMARY KEY (return_point_id, doc_id),
    CONSTRAINT fk_wrpd_return_point
        FOREIGN KEY (return_point_id) REFERENCES workflow_return_points(id) ON DELETE CASCADE,
    CONSTRAINT fk_wrpd_doc
        FOREIGN KEY (doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
);

CREATE INDEX idx_wrpd_rp_seq
    ON workflow_return_point_docs(return_point_id, seq);
