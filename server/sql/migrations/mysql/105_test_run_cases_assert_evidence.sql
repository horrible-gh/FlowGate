-- 102_test_run_cases_assert_evidence.sql
-- flowgate.default.0503 (R0001 -> NR0003 -> T0007): native assertion evidence columns.
--
-- assert_mode        -- the case's raw `assert: <mode>:<spec>` field value, or NULL for a
--                       legacy TC with no assert field (exit-code-only judging, unchanged).
-- actual             -- the value the comparator actually observed at grading time, rendered
--                       as a human-readable string. NULL until the case finishes, and stays
--                       NULL forever for legacy (assert_mode IS NULL) and timeout cases.
-- comparison_result  -- 'match' / 'mismatch', or NULL under the same conditions as actual.
--
-- All three are nullable and additive: every row predating this migration keeps assert_mode/
-- actual/comparison_result NULL and renders exactly as before.
-- No index, no CHECK (aligned with the SQLite authoring source).

ALTER TABLE test_run_cases ADD COLUMN assert_mode TEXT NULL;
ALTER TABLE test_run_cases ADD COLUMN actual TEXT NULL;
ALTER TABLE test_run_cases ADD COLUMN comparison_result TEXT NULL;
