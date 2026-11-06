-- Flow archive is display state, independent of lifecycle and Git preservation.
ALTER TABLE groups ADD COLUMN archived_at TEXT NULL;
ALTER TABLE groups ADD COLUMN archived_by TEXT NULL;
-- A restored completed Flow stays visible without changing final approval or DC.
ALTER TABLE groups ADD COLUMN archive_restored_at TEXT NULL;
