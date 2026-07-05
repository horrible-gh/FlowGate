-- 055_project_test_commands.sql
-- flowgate.default.0152 (R0001 → D0002 → P0003 → L0004 → T0005): per-project verified
-- test-command registry. Backs the Settings > Project > "Test commands" CRUD, the TS-mention
-- "Verified test commands" block, and auto-reflection from passed remote test runs.
-- DELETE is soft (status='suppressed', a tombstone); identity is UNIQUE(project, command).
-- Storage detail was DEFERRED to a DB doc by L; settled here following project_messages (042).

CREATE TABLE IF NOT EXISTS project_test_commands (
    id              SERIAL PRIMARY KEY,
    project         TEXT    NOT NULL
                        REFERENCES projects(project_id) ON DELETE CASCADE,
    command         TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    origin          TEXT    NOT NULL DEFAULT 'manual'
                        CHECK (origin IN ('manual', 'auto')),
    status          TEXT    NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'suppressed')),
    last_success_at TEXT,
    created_at      TEXT    NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at      TEXT    NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    UNIQUE(project, command)
);

CREATE INDEX IF NOT EXISTS idx_project_test_commands_lookup
    ON project_test_commands(project, status);
