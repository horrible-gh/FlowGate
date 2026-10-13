-- 151_graph_run_state_attempts.sql — flowgate.v02.0061 T#2 (persistent run state, step / full command, node attempts) (MySQL/MariaDB)
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
ALTER TABLE graph_runs ADD COLUMN run_mode VARCHAR(16) NULL;
ALTER TABLE graph_runs ADD COLUMN idle_reason VARCHAR(32) NULL;
ALTER TABLE graph_runs ADD COLUMN step_node_keys TEXT NULL;

CREATE TABLE IF NOT EXISTS graph_node_attempts (
    attempt_id          VARCHAR(64) PRIMARY KEY,
    run_id              VARCHAR(64) NOT NULL,
    node_key            VARCHAR(200) NOT NULL,
    revision_no         INTEGER NOT NULL,
    status              VARCHAR(32) NOT NULL,
    active_node_key     VARCHAR(200) NULL,
    invalidated_reason  VARCHAR(64) NULL,
    input_commit        VARCHAR(64) NULL,
    created_at          VARCHAR(40) NOT NULL,
    ended_at            VARCHAR(40) NULL,
    UNIQUE KEY idx_graph_node_attempts_active (run_id, active_node_key),
    INDEX idx_graph_node_attempts_node (run_id, node_key, created_at),
    CHECK (active_node_key IS NULL OR active_node_key = node_key),
    CONSTRAINT fk_graph_attempt_run FOREIGN KEY (run_id) REFERENCES graph_runs(run_id) ON DELETE CASCADE
);

ALTER TABLE graph_run_token_map ADD COLUMN attempt_id VARCHAR(64) NULL;
CREATE INDEX idx_graph_token_map_attempt ON graph_run_token_map(attempt_id);
