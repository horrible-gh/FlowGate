-- 099_test_runs_locale.sql
-- Test Report locale persistence — flowgate.default.0520 T0004 (NR0003 root cause).
--
-- The UI's chosen locale reached the test-run mention/token builder (051
-- continuation_locale) but was never carried onto the run itself: validate_and_create_run
-- / insert_run had no locale parameter, so the background TSR assembly step — which runs
-- outside any request context, in the async worker — could never recover it and always fell
-- back to assemble_tsr()'s "ko" default. An English-locale Test Run therefore always
-- produced a Korean Test Report.
--
-- This additive column lets the run own its locale as execution metadata for its whole
-- lifetime, so the worker can read it back from the DB row instead of relying on a
-- caller-supplied default. Same additive pattern as 051 tokens.continuation_locale — no
-- table rebuild, no CHECK constraint (mirrors that column exactly).

ALTER TABLE test_runs ADD COLUMN locale TEXT;
