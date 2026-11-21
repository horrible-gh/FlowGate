-- 071_tr_scope_stage.sql
-- Validate TR work scope (flowgate.default.0299: R0001 → D0004 → NR0006 → T0007).
--
-- project_git_config.tr_scope_stage — enforcement stage per project.
--   'observe' / 'warn' / 'enforce'. Default: 'observe'.
-- See the matching SQLite file for details.

ALTER TABLE project_git_config ADD COLUMN tr_scope_stage VARCHAR(16) DEFAULT 'observe';
