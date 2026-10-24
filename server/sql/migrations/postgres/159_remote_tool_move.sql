-- 159 — preserve the audit table, rows, indexes and sequence while widening op.
DO $$
DECLARE constraint_name text;
BEGIN
  FOR constraint_name IN
    SELECT c.conname FROM pg_constraint c
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
    WHERE c.conrelid = 'remote_tool_op_log'::regclass AND c.contype = 'c'
      AND a.attname = 'op'
  LOOP
    EXECUTE format('ALTER TABLE remote_tool_op_log DROP CONSTRAINT %I', constraint_name);
  END LOOP;
END $$;
ALTER TABLE remote_tool_op_log ADD CONSTRAINT remote_tool_op_log_op_check_move
  CHECK (op IN ('read', 'write', 'grep', 'glob', 'remove', 'patch', 'stat', 'move'));
