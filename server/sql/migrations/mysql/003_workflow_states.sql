SET FOREIGN_KEY_CHECKS=0;
-- 003_workflow_states.sql
-- D017 r1: expand the groups.status CHECK constraint (add draft/in_progress/clarifying/approved/closed)
-- In SQLite, changing a CHECK constraint requires recreating the table.
-- Existing record status values are preserved (OPEN/CLOSED/CANCELLED -> not used in the new app).

-- 1. Preserve data in a temporary table
CREATE TABLE IF NOT EXISTS _groups_bak AS SELECT * FROM groups;
-- 2. Drop the existing table (foreign keys must be disabled)
DROP TABLE groups;
-- 3. Recreate with the expanded CHECK constraint
CREATE TABLE groups (
    group_id    VARCHAR(191) PRIMARY KEY,
    project_id  VARCHAR(191) NOT NULL REFERENCES projects(project_id),
    module      VARCHAR(191) NOT NULL DEFAULT '__ALL__',
    parent_id   VARCHAR(191) REFERENCES groups(group_id),
    title       TEXT NOT NULL,
    priority    TEXT,
    status      VARCHAR(191) NOT NULL DEFAULT 'draft'
                    CHECK (status IN (
                        'draft', 'in_progress', 'clarifying',
                        'approved', 'closed',
                        'OPEN', 'CLOSED', 'CANCELLED'
                    )),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    closed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_groups_project_module ON groups(project_id, module);
CREATE INDEX IF NOT EXISTS idx_groups_parent         ON groups(parent_id);
CREATE INDEX IF NOT EXISTS idx_groups_status         ON groups(status);
-- 4. Restore data
INSERT INTO groups SELECT * FROM _groups_bak;
DROP TABLE _groups_bak;
SET FOREIGN_KEY_CHECKS=1;
