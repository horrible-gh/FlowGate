-- 071_tr_scope_stage.sql
-- Validate TR work scope (flowgate.default.0299: R0001 → D0004 → NR0006 → T0007).
--
-- project_git_config.tr_scope_stage — enforcement stage per project.
--   'observe': evaluate and record results without blocking submission (default).
--   'warn': allow submission but leave warnings in the response/document.
--   'enforce': reject submission according to the evaluation.
--
-- The default is 'observe' for the reason in D0004 §3.6: already-issued work in progress
-- lacks the `## 변경 파일` section, so immediate enforcement would block all of it.
-- Operators raise the stage through the UI.
--
-- This validation only makes sense in a worktree, so projects with Git integration disabled
-- skip it regardless of stage. Thus project_git_config is the natural place for the setting;
-- a project with no row has no integration.
-- Additive migration, without indexes/CHECKs (values are validated in the application layer).

BEGIN;

ALTER TABLE project_git_config ADD COLUMN tr_scope_stage TEXT DEFAULT 'observe';

COMMIT;
