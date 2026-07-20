-- 068_test_commands_verified_os.sql
-- flowgate.default.0277 (B0001 → N0002 → NR0003 → T0004): host-OS awareness for the
-- verified test-command registry.
--
-- Background: FlowGate moved from a Linux host to a Windows host. The registry recorded
-- which commands passed a remote test run, but not WHICH OS they passed on, so POSIX-only
-- commands accumulated on Linux kept being offered to TS authors on Windows under the
-- "verified / last pass <date>" label — the strongest trust signal the mention carries.
--
-- verified_os is nullable and additive:
--   'nt' | 'posix' — the os.name of the host where a remote test run last passed this command
--   NULL           — no OS evidence: manual entries, and every row predating this migration
--                    (including auto rows from the old Linux host, which we cannot honestly
--                    relabel — the service layer renders these as "OS unverified" instead).
-- Only auto-reflection from a passed run sets this column; manual CRUD leaves it NULL.
-- No index: the existing (project, status) lookup already narrows to a few dozen rows.

BEGIN;

ALTER TABLE project_test_commands ADD COLUMN verified_os TEXT;

COMMIT;
