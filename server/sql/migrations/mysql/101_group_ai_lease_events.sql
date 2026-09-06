-- 101_group_ai_lease_events.sql — flowgate.default.0502 T0004 (MySQL/MariaDB)
-- Append-only forensic history behind group_ai_leases (a current-state table with no
-- ownership audit trail). One row per lifecycle transition: acquired / transferred /
-- activated / handoff-begin / released / expired-reclaimed / startup-reclaimed /
-- admission-rejected. Rows are never UPDATEd or DELETEd by normal operation.
CREATE TABLE IF NOT EXISTS group_ai_lease_events (
    id                  INTEGER PRIMARY KEY AUTO_INCREMENT,
    event_id            VARCHAR(191) NOT NULL UNIQUE,
    event_type          VARCHAR(64)  NOT NULL,
    group_id            VARCHAR(191) NOT NULL,
    project_id          VARCHAR(191),
    run_id              VARCHAR(191),
    token_id            VARCHAR(191),
    chain_id            VARCHAR(191),
    action_scope        VARCHAR(64),
    lease_generation    INTEGER,
    reason              VARCHAR(191),
    requested_snapshot  TEXT,
    blocking_snapshot   TEXT,
    detail              TEXT,
    created_at          VARCHAR(191) NOT NULL,
    CONSTRAINT fk_gale_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);
CREATE INDEX idx_group_ai_lease_events_group_created ON group_ai_lease_events(group_id, created_at);
CREATE INDEX idx_group_ai_lease_events_type_created ON group_ai_lease_events(event_type, created_at);
CREATE INDEX idx_group_ai_lease_events_run ON group_ai_lease_events(run_id);
CREATE INDEX idx_group_ai_lease_events_token ON group_ai_lease_events(token_id);
