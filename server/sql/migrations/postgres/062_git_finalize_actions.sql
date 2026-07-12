-- 062_git_finalize_actions.sql
-- Record the original finalize action on conflict sessions so resolve can
-- preserve merge_only semantics after manual conflict resolution.

ALTER TABLE git_merge_session ADD COLUMN finalize_action TEXT;
