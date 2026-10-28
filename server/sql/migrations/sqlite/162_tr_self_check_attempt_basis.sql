-- 162_tr_self_check_attempt_basis.sql
-- flowgate.v02.0110 T#3 (NR0003 §3 T-B): a Self-check run of a Graph attempt token executes in that
-- attempt's own worktree, not in the group worktree. The run records which attempt tree it ran in and
-- the commit that tree started from, next to the HEAD it probed (source_head_before / _after):
--
--   attempt_id    graph_node_attempts.attempt_id of the tree the run executed in. No FK: an attempt's
--                 tree is removed once it is integrated or invalidated, its run evidence stays.
--   input_commit  graph_node_attempts.input_commit when the run was admitted.
--
-- A group worktree run (an unmapped v0.1 token) keeps both NULL. Nothing is backfilled.
ALTER TABLE tr_self_check_runs ADD COLUMN attempt_id TEXT NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN input_commit TEXT NULL;
