-- T157: projects category SQL queries (replaces allowed_projects per D009)
-- FlowGate projects table CRUD operations

-- Get all active projects (replaces allowed_projects per D009)
-- key: get_allowed_projects
SELECT project_id AS project, project_name, '' AS module
FROM projects WHERE is_active = 1 ORDER BY project_id;

-- Get active project IDs
-- key: get_allowed_project_names
SELECT project_id AS project FROM projects WHERE is_active = 1;

-- Add project or reactivate (module param ignored - not in D009 schema)
-- key: add_allowed_project
INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at)
VALUES (?, ?, 1, datetime('now'), datetime('now'))
ON CONFLICT(project_id) DO UPDATE SET
    is_active = 1,
    updated_at = datetime('now')
WHERE projects.is_active = 0;

-- Soft-delete project (module param ignored - not in D009 schema)
-- key: remove_allowed_project
UPDATE projects SET is_active = 0, updated_at = datetime('now')
WHERE project_id = ?;
