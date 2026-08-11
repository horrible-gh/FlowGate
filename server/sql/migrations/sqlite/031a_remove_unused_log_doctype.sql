-- Remove the unused "Log" document type. (PM: Log is not a used doc type.)
--
-- Two global type_code='L' rows existed: series 'design' = Logic and
-- series 'general' (originally 'requirements') = Log. The label lookup keys by
-- type_code only (document_type_labels.py / docTypeStore), so the unused 'general'
-- Log row overwrote the 'design' Logic row and the workflow strip showed "Log"
-- for the Logic step. Dropping the unused Log type makes L resolve uniquely to
-- Logic.
--
-- Delete locale names first (robust whether or not FK cascade is enabled), then
-- the type rows. Covers both the post-023 'general' name and the legacy
-- 'requirements' name.
DELETE FROM document_type_names
WHERE document_type_id IN (
    SELECT id FROM document_types
    WHERE type_code = 'L' AND series IN ('general', 'requirements')
);

DELETE FROM document_types
WHERE type_code = 'L' AND series IN ('general', 'requirements');
