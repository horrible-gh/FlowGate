BEGIN;
-- 0132 T#3 / NR §4.4, §4.6: durable command intent, additive; no run or node state changes.
-- Rollback: server/sql/rollback/postgres/174_gate_accept_wake.sql
ALTER TABLE gate_decisions ADD COLUMN IF NOT EXISTS accept_wake INTEGER NOT NULL DEFAULT 0 CHECK (accept_wake IN (0, 1));
ALTER TABLE gate_decisions ADD COLUMN IF NOT EXISTS accept_event_seq BIGINT NULL;

-- Only the first run-status event at/after acceptance can prove the original wake.
-- A later Stop, Pause, Time Machine or explicit command invalidates that intent.
WITH wake AS (
    SELECT d.decision_id, MIN(e.event_seq) AS seq
    FROM gate_decisions d JOIN graph_run_events e ON e.run_id = d.run_id
    WHERE d.status IN ('accepted', 'failed', 'recovery_required')
      AND d.accept_event_seq IS NULL
      AND e.event_type = 'graph_run_status_changed' AND e.created_at >= d.accepted_at
      AND e.cause = 'work_resumed' AND e.reason = 'gate_publish'
      AND NOT EXISTS (
          SELECT 1 FROM graph_run_events earlier
          WHERE earlier.run_id = d.run_id AND earlier.event_type = 'graph_run_status_changed'
            AND earlier.created_at >= d.accepted_at AND earlier.event_seq < e.event_seq)
      AND NOT EXISTS (
          SELECT 1 FROM graph_run_events later
          WHERE later.run_id = d.run_id AND later.event_seq > e.event_seq
            AND later.event_type = 'graph_run_status_changed'
            AND later.cause IN ('user_stop', 'user_pause', 'graph_time_machine',
                                'user_start', 'user_command', 'user_resume'))
    GROUP BY d.decision_id
)
UPDATE gate_decisions
SET accept_wake = 1, accept_event_seq = (SELECT seq FROM wake WHERE wake.decision_id = gate_decisions.decision_id)
WHERE decision_id IN (SELECT decision_id FROM wake);
COMMIT;
