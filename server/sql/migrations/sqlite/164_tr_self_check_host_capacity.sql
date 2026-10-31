-- 164_tr_self_check_host_capacity.sql
-- flowgate.v02.0114 T#4 (D 0006 3.5, 7.2, 8-5): the host-wide Self-check resource cap.
--
-- tr_self_check_runs, two new columns. NULL on existing rows, nothing is backfilled.
--   cpu_reservation      R, the logical processors the run reserved when it was admitted (>= 1).
--                        The run's whole process tree is limited to R processors. An active or
--                        protected run without it (admitted before this migration) counts as the
--                        whole cap when a host's reservations are summed.
--   cpu_share_requested  the parallel_share the request asked for (default 1).
--
-- tr_self_check_capacity_lock: one row, locked as the first statement of every Self-check
-- admission transaction (SQLite: a no-op UPDATE takes the database write lock, PostgreSQL /
-- MySQL: SELECT ... FOR UPDATE), so the per-host sum, the reservation and the run row insert
-- are one unit across every server instance sharing the store. Nothing else writes it.
ALTER TABLE tr_self_check_runs ADD COLUMN cpu_reservation INTEGER NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN cpu_share_requested INTEGER NULL;

CREATE TABLE tr_self_check_capacity_lock (
    lock_id TEXT PRIMARY KEY,
    updated_at TEXT NULL
);

INSERT INTO tr_self_check_capacity_lock (lock_id, updated_at) VALUES ('host_capacity', NULL);
