-- 055_project_test_commands.sql
-- flowgate.default.0152 (R0001 → D0002 → P0003 → L0004 → T0005): per-project verified
-- test-command registry. Backs the Settings > Project > "Test commands" CRUD, the TS-mention
-- "Verified test commands" block, and auto-reflection from passed remote test runs.
-- DELETE is soft (status='suppressed', a tombstone); identity is UNIQUE(project, command).
-- Storage detail was DEFERRED to a DB doc by L; settled here following project_messages (042).
--
-- command is VARCHAR(500) (= COMMAND_MAX_LEN) so it can participate in UNIQUE(project, command);
-- MySQL/MariaDB cannot index a TEXT column without a prefix length. origin/status are plain
-- columns (the app enforces the enum domain, matching the CHECK-less project_messages precedent).

CREATE TABLE IF NOT EXISTS project_test_commands (
    id              INTEGER PRIMARY KEY AUTO_INCREMENT,
    project         VARCHAR(191)    NOT NULL
                        REFERENCES projects(project_id) ON DELETE CASCADE,
    command         VARCHAR(500)    NOT NULL,
    description     TEXT            NOT NULL,
    origin          VARCHAR(16)     NOT NULL DEFAULT 'manual',
    status          VARCHAR(16)     NOT NULL DEFAULT 'active',
    last_success_at TEXT,
    created_at      TEXT            NOT NULL DEFAULT (UTC_TIMESTAMP()),
    updated_at      TEXT            NOT NULL DEFAULT (UTC_TIMESTAMP()),
    UNIQUE(project, command)
);

CREATE INDEX IF NOT EXISTS idx_project_test_commands_lookup
    ON project_test_commands(project, status);
