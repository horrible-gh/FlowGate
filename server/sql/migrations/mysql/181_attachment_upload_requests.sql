-- 181_attachment_upload_requests.sql
-- AI attachment upload requests (flowgate.chat.0017 CH0002 final design).
-- See the sqlite file for the rationale. Additive only: dropping the two tables is a
-- complete rollback. MySQL has no CREATE INDEX IF NOT EXISTS, so the indexes are declared
-- inside CREATE TABLE IF NOT EXISTS (same names and columns as the other dialects).

CREATE TABLE IF NOT EXISTS attachment_upload_requests (
    request_id          VARCHAR(64)   NOT NULL,
    project_id          VARCHAR(191)  NOT NULL,
    group_id            VARCHAR(191)  NOT NULL,
    origin              VARCHAR(16)   NOT NULL,
    source_doc_id       VARCHAR(191)  NULL,
    target_flow_id      VARCHAR(191)  NOT NULL,
    target_node_key     VARCHAR(191)  NOT NULL,
    target_doc_id       VARCHAR(191)  NULL,
    target_title        VARCHAR(512)  NULL,
    ai_run_id           VARCHAR(64)   NULL,
    graph_run_id        VARCHAR(64)   NULL,
    token_id            VARCHAR(191)  NULL,
    requested_by        VARCHAR(191)  NULL,
    provider_name       VARCHAR(255)  NULL,
    original_filename   VARCHAR(512)  NOT NULL,
    filename            VARCHAR(255)  NOT NULL,
    size                BIGINT        NOT NULL,
    content_type        VARCHAR(255)  NOT NULL,
    content_sha256      VARCHAR(64)   NOT NULL,
    staging_path        VARCHAR(1024) NOT NULL,
    note                TEXT          NULL,
    status              VARCHAR(16)   NOT NULL,
    decision            VARCHAR(16)   NULL,
    decided_by          VARCHAR(191)  NULL,
    decided_at          VARCHAR(40)   NULL,
    final_doc_id        VARCHAR(191)  NULL,
    final_filename      VARCHAR(255)  NULL,
    approve_phase       VARCHAR(32)   NULL,
    approve_temp_name   VARCHAR(255)  NULL,
    backup_path         VARCHAR(1024) NULL,
    backup_expires_at   VARCHAR(40)   NULL,
    error_code          VARCHAR(64)   NULL,
    run_start_seq       INTEGER       NULL,
    expires_at          VARCHAR(40)   NOT NULL,
    created_at          VARCHAR(40)   NOT NULL,
    updated_at          VARCHAR(40)   NOT NULL,
    PRIMARY KEY (request_id),
    KEY idx_aur_status_expires (status, expires_at),
    KEY idx_aur_source_doc (source_doc_id, created_at),
    KEY idx_aur_target_node (target_flow_id, target_node_key, status),
    KEY idx_aur_graph_run (graph_run_id, status),
    KEY idx_aur_project_status (project_id, status),
    KEY idx_aur_backup_expires (backup_expires_at),
    CONSTRAINT fk_aur_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE,
    CONSTRAINT ck_aur_origin CHECK (origin IN ('chat', 'card')),
    CONSTRAINT ck_aur_size CHECK (size >= 0),
    CONSTRAINT ck_aur_sha CHECK (length(content_sha256) = 64),
    CONSTRAINT ck_aur_status CHECK (status IN ('pending', 'approving', 'approved', 'rejected', 'expired', 'failed')),
    CONSTRAINT ck_aur_decision CHECK (decision IS NULL OR decision IN ('keep_both', 'overwrite'))
) ROW_FORMAT=DYNAMIC;

CREATE TABLE IF NOT EXISTS project_attachment_request_settings (
    project_id      VARCHAR(191) NOT NULL,
    settings_json   TEXT         NOT NULL,
    updated_at      VARCHAR(40)  NOT NULL,
    updated_by      VARCHAR(191) NULL,
    PRIMARY KEY (project_id),
    CONSTRAINT fk_pars_project FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
) ROW_FORMAT=DYNAMIC;
