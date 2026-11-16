-- 190_project_modules_archive.sql (mysql)
-- flowgate.v02gui.0027 (0006-T): display-only Group archive. A Group of the v0.2 tree is a module
-- (project_modules row / groups.module). archived_at set = hidden from the default project tree;
-- its Flows, documents, runs and Git are never touched. Restore clears both columns.
-- Applied once by the filename-keyed migration ledger (SQLite/MySQL ADD COLUMN).
ALTER TABLE project_modules ADD COLUMN archived_at VARCHAR(64) NULL;
ALTER TABLE project_modules ADD COLUMN archived_by VARCHAR(191) NULL;
