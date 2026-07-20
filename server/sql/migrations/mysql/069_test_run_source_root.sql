-- 069_test_run_source_root.sql
-- flowgate.default.0280 (B0001 → N0002 → NR0003 → T0005): persist WHERE a test run
-- actually executed.
--
-- Background: users repeatedly reported "the tests run in main, not on the branch".
-- NR0003 found the runner already resolves the group worktree correctly, but nothing
-- recorded the root it used, so every report was unfalsifiable: a correct worktree run
-- and a silent fallback to base(main) left identical evidence (none). Worse, the TSR
-- report printed the *document* branch, which is always "main", so correct runs looked
-- wrong.
--
-- source_root      — storage-root-relative POSIX path of the tree the run executed in
--                    (same convention as every other path column, B0054/L0054.0002).
-- source_root_kind — 'worktree' when the group worktree was used, otherwise the reason
--                    the run fell back to the base project-branch tree, one of:
--                    git_integration_off / no_group_git_state / worktree_unregistered /
--                    state_branch_empty / project_name_missing / worktree_dir_missing /
--                    no_group_context / resolution_error / unknown.
--                    'worktree_unregistered' is the post-merge re-run case (NR0003 §4-B).
--
-- Both are nullable and additive: every row predating this migration has no evidence and
-- is rendered as "기록 없음" rather than being back-labelled with a guess.
-- No index, no CHECK (aligned with the SQLite authoring source).

ALTER TABLE test_runs ADD COLUMN source_root TEXT NULL;
ALTER TABLE test_runs ADD COLUMN source_root_kind TEXT NULL;
