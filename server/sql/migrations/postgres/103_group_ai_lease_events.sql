-- 103_group_ai_lease_events.sql — flowgate.default.0502 T0004
-- Append-only forensic history behind group_ai_leases (a current-state table with no
-- ownership audit trail). One row per lifecycle transition: acquired / transferred /
-- activated / handoff-begin / released / expired-reclaimed / startup-reclaimed /
-- admission-rejected. Rows are never UPDATEd or DELETEd by normal operation.
CREATE TABLE IF NOT EXISTS group_ai_lease_events (
    id                  SERIAL PRIMARY KEY,
    event_id            TEXT    NOT NULL UNIQUE,
    event_type          TEXT    NOT NULL,
    group_id            TEXT    NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    project_id          TEXT,
    run_id              TEXT,
    token_id            TEXT,
    chain_id            TEXT,
    action_scope        TEXT,
    lease_generation    INTEGER,
    reason              TEXT,
    requested_snapshot  TEXT,
    blocking_snapshot   TEXT,
    detail              TEXT,
    created_at          TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_group_ai_lease_events_group_created
    ON group_ai_lease_events(group_id, created_at);
CREATE INDEX IF NOT EXISTS idx_group_ai_lease_events_type_created
    ON group_ai_lease_events(event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_group_ai_lease_events_run
    ON group_ai_lease_events(run_id);
CREATE INDEX IF NOT EXISTS idx_group_ai_lease_events_token
    ON group_ai_lease_events(token_id);
