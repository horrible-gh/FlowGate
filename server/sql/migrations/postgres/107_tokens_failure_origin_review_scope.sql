-- 104_tokens_failure_origin_review_scope.sql
ALTER TABLE tokens DROP CONSTRAINT tokens_action_scope_check;
ALTER TABLE tokens ADD CONSTRAINT tokens_action_scope_check
CHECK (action_scope IN ('new', 'edit', 'workflow_decide', 'review', 'test_run', 'workflow_sequence_edit', 'resolve_conflict', 'chat', 'failure_origin_review'));
ALTER TABLE tokens ADD COLUMN failure_origin_target_run_id TEXT;
ALTER TABLE tokens ADD COLUMN failure_origin_before_marker TIMESTAMP;
