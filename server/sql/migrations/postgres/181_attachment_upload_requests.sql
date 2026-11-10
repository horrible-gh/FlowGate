-- 181_attachment_upload_requests.sql
-- AI attachment upload requests (flowgate.chat.0017 CH0002 final design).
-- See the sqlite file for the rationale. Additive only: dropping the two tables is a
-- complete rollback.

CREATE TABLE IF NOT EXISTS attachment_upload_requests (
    request_id          TEXT    PRIMARY KEY,
    project_id          TEXT    NOT NULL,
    group_id            TEXT    NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    origin              TEXT    NOT NULL CHECK (origin IN ('chat', 'card')),
    source_doc_id       TEXT,
    target_flow_id      TEXT    NOT NULL,
    target_node_key     TEXT    NOT NULL,
    target_doc_id       TEXT,
    target_title        TEXT,
    ai_run_id           TEXT,
    graph_run_id        TEXT,
    token_id            TEXT,
    requested_by        TEXT,
    provider_name       TEXT,
    original_filename   TEXT    NOT NULL,
    filename            TEXT    NOT NULL,
    size                BIGINT  NOT NULL CHECK (size >= 0),
    content_type        TEXT    NOT NULL,
    content_sha256      TEXT    NOT NULL CHECK (length(content_sha256) = 64),
    staging_path        TEXT    NOT NULL,
    note                TEXT,
    status              TEXT    NOT NULL CHECK (status IN ('pending', 'approving', 'approved', 'rejected', 'expired', 'failed')),
    decision            TEXT    CHECK (decision IS NULL OR decision IN ('keep_both', 'overwrite')),
    decided_by          TEXT,
    decided_at          TEXT,
    final_doc_id        TEXT,
    final_filename      TEXT,
    approve_phase       TEXT,
    approve_temp_name   TEXT,
    backup_path         TEXT,
    backup_expires_at   TEXT,
    error_code          TEXT,
    run_start_seq       INTEGER,
    expires_at          TEXT    NOT NULL,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_aur_status_expires ON attachment_upload_requests(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_aur_source_doc     ON attachment_upload_requests(source_doc_id, created_at);
CREATE INDEX IF NOT EXISTS idx_aur_target_node    ON attachment_upload_requests(target_flow_id, target_node_key, status);
CREATE INDEX IF NOT EXISTS idx_aur_graph_run      ON attachment_upload_requests(graph_run_id, status);
CREATE INDEX IF NOT EXISTS idx_aur_project_status ON attachment_upload_requests(project_id, status);
CREATE INDEX IF NOT EXISTS idx_aur_backup_expires ON attachment_upload_requests(backup_expires_at);

CREATE TABLE IF NOT EXISTS project_attachment_request_settings (
    project_id      TEXT PRIMARY KEY REFERENCES projects(project_id) ON DELETE CASCADE,
    settings_json   TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    updated_by      TEXT
);
