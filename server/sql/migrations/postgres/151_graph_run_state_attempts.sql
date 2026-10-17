-- 151_graph_run_state_attempts.sql — flowgate.v02.0061 T#2 (persistent run state, step / full command, node attempts) (PostgreSQL)
-- A Flow keeps one run (active_group_id): running, paused or idle with a reason. A finished run stays as idle and goes
-- on with the next command; it no longer releases its Flow.
-- graph_runs:
--   run_mode        the command the run goes on with: step | full. NULL on rows before this migration (read as full).
--   idle_reason     why an idle run stopped opening nodes: completed | no_targets | waiting_approval |
--                   blocked_rejected | error. NULL while running / paused. An open key, like status.
--   step_node_keys  a step command's targets: the JSON array of the node keys it opened. NULL for a full command.
-- graph_node_attempts: one opening of a node (open -> done, or invalidated). Only one active attempt per
--   (run_id, node_key): active_node_key is the node key while the attempt is active and NULL after, so the unique
--   index (run_id, active_node_key) holds one active attempt per node (as graph_runs.active_group_id does per Flow).
--   status          active | ended | invalidated (an open key, no CHECK).
--   input_commit    the commit the attempt starts from; empty until the attempt worktree sets it.
-- graph_run_token_map:
--   attempt_id      the attempt a node token was issued for. NULL for tokens mapped before this migration.
-- Additive only; nothing is backfilled. Rows before this migration are read as: completed -> idle (completed),
-- stopped -> paused (its open nodes keep their tokens; a paused run keeps its Flow).
ALTER TABLE graph_runs ADD COLUMN run_mode TEXT;
ALTER TABLE graph_runs ADD COLUMN idle_reason TEXT;
ALTER TABLE graph_runs ADD COLUMN step_node_keys TEXT;

CREATE TABLE IF NOT EXISTS graph_node_attempts (
    attempt_id          TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES graph_runs(run_id) ON DELETE CASCADE,
    node_key            TEXT NOT NULL,
    revision_no         INTEGER NOT NULL,
    status              TEXT NOT NULL,
    active_node_key     TEXT,
    invalidated_reason  TEXT,
    input_commit        TEXT,
    created_at          TEXT NOT NULL,
    ended_at            TEXT,
    CHECK (active_node_key IS NULL OR active_node_key = node_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_graph_node_attempts_active ON graph_node_attempts(run_id, active_node_key);
CREATE INDEX IF NOT EXISTS idx_graph_node_attempts_node ON graph_node_attempts(run_id, node_key, created_at);

ALTER TABLE graph_run_token_map ADD COLUMN attempt_id TEXT;
CREATE INDEX IF NOT EXISTS idx_graph_token_map_attempt ON graph_run_token_map(attempt_id);
