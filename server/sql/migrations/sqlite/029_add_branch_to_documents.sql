-- T564: add the branch column to the documents table (reflect the target path structure)
-- Existing data is migrated automatically with DEFAULT 'main'.
ALTER TABLE documents ADD COLUMN branch TEXT NOT NULL DEFAULT 'main';
