-- T257: add a UNIQUE constraint to projects.project_name
CREATE UNIQUE INDEX IF NOT EXISTS ux_projects_project_name ON projects(project_name);
