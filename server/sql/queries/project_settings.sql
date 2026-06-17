-- T157: project_settings category SQL queries
-- FlowGate project settings CRUD operations

-- Get all project settings
-- key: get_project_settings
SELECT project, docs_root, project_root, updated_at
FROM project_settings
ORDER BY project;

-- Get project settings by project_id
-- key: get_project_settings_by_project
SELECT * FROM project_settings
WHERE project = ?;

-- Insert or update project settings
-- key: upsert_project_settings
INSERT INTO project_settings
(project, docs_root, project_root, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(project) DO UPDATE SET
    docs_root = excluded.docs_root,
    project_root = excluded.project_root,
    updated_at = excluded.updated_at;

-- Remove project settings
-- key: remove_project_settings
DELETE FROM project_settings
WHERE project = ?;
