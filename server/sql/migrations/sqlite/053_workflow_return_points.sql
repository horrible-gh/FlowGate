-- 053_workflow_return_points.sql
-- Reverse time-machine return point snapshots.

BEGIN;

CREATE TABLE IF NOT EXISTS workflow_return_points (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id   TEXT    NOT NULL UNIQUE REFERENCES groups(group_id) ON DELETE CASCADE,
    front_seq  INTEGER NOT NULL,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_return_point_docs (
    return_point_id INTEGER NOT NULL REFERENCES workflow_return_points(id) ON DELETE CASCADE,
    doc_id          TEXT    NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    prev_status     TEXT    NOT NULL,
    fingerprint     TEXT    NOT NULL,
    PRIMARY KEY (return_point_id, doc_id)
);

CREATE INDEX IF NOT EXISTS idx_wrpd_rp_seq
    ON workflow_return_point_docs(return_point_id, seq);

COMMIT;
