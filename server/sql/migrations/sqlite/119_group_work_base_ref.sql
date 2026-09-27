-- 119_group_work_base_ref.sql
-- flowgate.default.0613 T#1: the nullable value is the durable branch/ref from
-- which this group's first worktree must start.  NULL preserves legacy behavior:
-- resolve through project_git_config.base_branch.  It is intentionally separate
-- from project_git_config.default_merge_target and Git recovery start_point.

ALTER TABLE groups ADD COLUMN work_base_ref TEXT;