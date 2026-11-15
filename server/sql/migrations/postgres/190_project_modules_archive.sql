-- 190_project_modules_archive.sql (postgres)
-- flowgate.v02gui.0027 (0006-T): display-only Group archive. A Group of the v0.2 tree is a module
-- (project_modules row / groups.module). archived_at set = hidden from the default project tree;
-- its Flows, documents, runs and Git are never touched. Restore clears both columns.
ALTER TABLE project_modules ADD COLUMN IF NOT EXISTS archived_at TEXT;
ALTER TABLE project_modules ADD COLUMN IF NOT EXISTS archived_by TEXT;
