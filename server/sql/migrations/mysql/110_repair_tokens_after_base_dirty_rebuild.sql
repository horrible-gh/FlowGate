-- 110_repair_tokens_after_base_dirty_rebuild.sql
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS failure_origin_target_run_id TEXT;
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS failure_origin_before_marker TEXT;
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS source_access TEXT;
SET @fg_chk := (
  SELECT tc.CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS tc
  JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME
  WHERE tc.CONSTRAINT_SCHEMA=DATABASE() AND tc.TABLE_NAME='tokens' AND tc.CONSTRAINT_TYPE='CHECK'
    AND cc.CHECK_CLAUSE LIKE '%action_scope%' LIMIT 1
);
SET @fg_sql := IF(@fg_chk IS NULL, 'SELECT 1', CONCAT('ALTER TABLE tokens DROP CHECK `', REPLACE(@fg_chk,'`','``'), '`'));
PREPARE fg_stmt FROM @fg_sql; EXECUTE fg_stmt; DEALLOCATE PREPARE fg_stmt;
ALTER TABLE tokens ADD CONSTRAINT tokens_action_scope_check CHECK (action_scope IN ('new','edit','workflow_decide','review','test_run','workflow_sequence_edit','resolve_conflict','chat','failure_origin_review','resolve_base_dirty'));
