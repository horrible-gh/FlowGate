-- T229 Phase 2: add projects.source_path (D021 §4-4 / §3-1)
ALTER TABLE projects ADD COLUMN source_path TEXT;
