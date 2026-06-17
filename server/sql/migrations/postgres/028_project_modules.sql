-- T505: backend integration for module creation — add the project_modules table
-- Manage modules as first-class entities in the DB.
-- Coexists with the existing groups.module-derived approach: the tree merges both sides to build module nodes.
CREATE TABLE IF NOT EXISTS project_modules (
    module_id   TEXT PRIMARY KEY,              -- "{project_id}:{name}"
    project_id  TEXT NOT NULL REFERENCES projects(project_id),
    name        TEXT NOT NULL,                 -- Slug (matches the groups.module column value)
    title       TEXT,                          -- Display name (same as name when unspecified)
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (project_id, name)
);
CREATE INDEX IF NOT EXISTS idx_project_modules_project ON project_modules(project_id);