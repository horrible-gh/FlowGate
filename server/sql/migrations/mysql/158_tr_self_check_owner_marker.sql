-- 158_tr_self_check_owner_marker.sql
-- flowgate.v02.0080 T#1 (NR0007 §5-0, §5 item 4): who owns a Self-check run, recorded at
-- create_pending -- before the run's process is spawned -- so restart recovery can prove the
-- owner dead and the run's process tree empty without any PID having been written yet.
--
--   owner_instance_id  server_instance.instance_id of the server whose worker runs it. No FK:
--                      instance rows are retired independently of run evidence.
--   owner_marker       JSON, the run-unit ownership record: the named Windows Job and its
--                      session, or the POSIX environment marker the run's processes inherit.
--
-- Existing rows keep NULL: recovery treats them as "ownership not recorded" and stays on the
-- protective side. Nothing is backfilled.
ALTER TABLE tr_self_check_runs ADD COLUMN owner_instance_id VARCHAR(191) NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN owner_marker MEDIUMTEXT NULL;
