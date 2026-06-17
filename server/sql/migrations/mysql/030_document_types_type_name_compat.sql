-- Test/runtime compatibility: restore nullable document_types.type_name column for legacy inserts.
ALTER TABLE document_types ADD COLUMN type_name TEXT;
UPDATE document_types
SET type_name = COALESCE(type_name, type_code)
WHERE type_name IS NULL OR TRIM(type_name) = '';