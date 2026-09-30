-- 126: permit post-commit TR2 candidate registration while retaining row identity.
BEGIN;
CREATE TABLE project_test_commands_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    command TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'manual' CHECK (origin IN ('manual', 'auto', 'tr2')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suppressed')),
    last_success_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    verified_os TEXT,
    UNIQUE(project, command)
);
INSERT INTO project_test_commands_new
    (id, project, command, description, origin, status, last_success_at,
     created_at, updated_at, verified_os)
SELECT id, project, command, description, origin, status, last_success_at,
       created_at, updated_at, verified_os FROM project_test_commands;
DROP TABLE project_test_commands;
ALTER TABLE project_test_commands_new RENAME TO project_test_commands;
CREATE INDEX idx_project_test_commands_lookup ON project_test_commands(project, status);
COMMIT;
