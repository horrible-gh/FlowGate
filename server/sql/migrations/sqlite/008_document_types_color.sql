-- T183: add the color column to document_types + set initial values
-- Idempotency guaranteed (if the color column already exists, ALTER TABLE is ignored)

-- SQLite does not support IF NOT EXISTS here, so execute only after checking column existence in Python.
-- This file includes only ALTER TABLE and UPDATE statements.

ALTER TABLE document_types ADD COLUMN color TEXT;

-- requirements series
UPDATE document_types SET color = '#2563eb' WHERE type_code = 'R'  AND series = 'requirements' AND project_id IS NULL;
UPDATE document_types SET color = '#64748b' WHERE type_code = 'M'  AND series = 'requirements' AND project_id IS NULL;
UPDATE document_types SET color = '#d97706' WHERE type_code = 'Q'  AND series = 'requirements' AND project_id IS NULL;
UPDATE document_types SET color = '#16a34a' WHERE type_code = 'A'  AND series = 'requirements' AND project_id IS NULL;
UPDATE document_types SET color = '#7c3aed' WHERE type_code = 'L'  AND series = 'requirements' AND project_id IS NULL;
UPDATE document_types SET color = '#dc2626' WHERE type_code = 'B'  AND series = 'requirements' AND project_id IS NULL;

-- instruction series
UPDATE document_types SET color = '#7c3aed' WHERE type_code = 'DS' AND series = 'instruction'  AND project_id IS NULL;
UPDATE document_types SET color = '#0284c7' WHERE type_code = 'N'  AND series = 'instruction'  AND project_id IS NULL;
UPDATE document_types SET color = '#0891b2' WHERE type_code = 'T'  AND series = 'instruction'  AND project_id IS NULL;
UPDATE document_types SET color = '#db2777' WHERE type_code = 'TS' AND series = 'instruction'  AND project_id IS NULL;

-- design series
UPDATE document_types SET color = '#ea580c' WHERE type_code = 'D'  AND series = 'design'       AND project_id IS NULL;
UPDATE document_types SET color = '#0d9488' WHERE type_code = 'P'  AND series = 'design'       AND project_id IS NULL;
UPDATE document_types SET color = '#ca8a04' WHERE type_code = 'DB' AND series = 'design'       AND project_id IS NULL;

-- work series
UPDATE document_types SET color = '#6366f1' WHERE type_code = 'NR' AND series = 'work'         AND project_id IS NULL;
UPDATE document_types SET color = '#0284c7' WHERE type_code = 'TR' AND series = 'work'         AND project_id IS NULL;
UPDATE document_types SET color = '#9333ea' WHERE type_code = 'TSR'AND series = 'work'         AND project_id IS NULL;
UPDATE document_types SET color = '#f59e0b' WHERE type_code = 'V'  AND series = 'work'         AND project_id IS NULL;
UPDATE document_types SET color = '#059669' WHERE type_code = 'C'  AND series = 'work'         AND project_id IS NULL;

-- action series
UPDATE document_types SET color = '#16a34a' WHERE type_code = 'AC' AND series = 'action'       AND project_id IS NULL;
UPDATE document_types SET color = '#dc2626' WHERE type_code = 'RJ' AND series = 'action'       AND project_id IS NULL;
