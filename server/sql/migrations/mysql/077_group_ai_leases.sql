-- 077_group_ai_leases.sql — flowgate.default.0378 (MySQL/MariaDB)
CREATE TABLE IF NOT EXISTS group_ai_leases (
    group_id        VARCHAR(191) PRIMARY KEY,
    project_id      VARCHAR(191) NOT NULL,
    run_id          VARCHAR(191) NOT NULL,
    chain_id        VARCHAR(191),
    token_id        VARCHAR(191),
    action_scope    VARCHAR(64) NOT NULL,
    worker_identity VARCHAR(191),
    state           VARCHAR(16) NOT NULL CHECK (state IN ('acquiring', 'active', 'releasing')),
    generation      INTEGER NOT NULL DEFAULT 1 CHECK (generation >= 1),
    acquired_at     VARCHAR(191) NOT NULL,
    heartbeat_at    VARCHAR(191) NOT NULL,
    expires_at      VARCHAR(191) NOT NULL,
    updated_at      VARCHAR(191) NOT NULL,
    CONSTRAINT fk_gal_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE,
    CONSTRAINT fk_gal_project FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX idx_group_ai_leases_run ON group_ai_leases(run_id);
CREATE INDEX idx_group_ai_leases_project ON group_ai_leases(project_id, expires_at);
CREATE INDEX idx_group_ai_leases_expiry ON group_ai_leases(expires_at);