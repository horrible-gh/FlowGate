-- 055_project_test_commands.sql
-- flowgate.default.0152 (R0001 → D0002 → P0003 → L0004 → T0005): per-project verified
-- test-command registry. Backs the Settings > Project > "Test commands" CRUD, the TS-mention
-- "Verified test commands" block, and auto-reflection from passed remote test runs.
--
-- Physical delete never happens (L §2-2): a user DELETE flips status to 'suppressed', a tombstone
-- that keeps the (project, command) slot so auto-reflection cannot re-register it; a manual re-add
-- of the same command revives the same row. Identity is the normalized command string
-- (trim + whitespace-collapse, case-sensitive) → L §2-1, enforced by UNIQUE(project, command).
--
-- Storage detail was DEFERRED to a DB doc by L; settled here at implementation time following the
-- project_messages (042) precedent (project-scoped table, project.settings.* RBAC).

BEGIN;

CREATE TABLE IF NOT EXISTS project_test_commands (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project         TEXT    NOT NULL
                        REFERENCES projects(project_id) ON DELETE CASCADE,
    command         TEXT    NOT NULL,                       -- normalized (trim + collapse ws)
    description     TEXT    NOT NULL DEFAULT '',
    origin          TEXT    NOT NULL DEFAULT 'manual'
                        CHECK (origin IN ('manual', 'auto')),
    status          TEXT    NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'suppressed')),
    last_success_at TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(project, command)
);

CREATE INDEX IF NOT EXISTS idx_project_test_commands_lookup
    ON project_test_commands(project, status);

COMMIT;
