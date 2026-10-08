-- 140_source_bundle_retirement.sql
-- flowgate.default.0684 T#4 (D#1 §2-2, §7): Source Bundle final removal.
--   1. Drop the five Bundle tables. Children first: pins/usages/scratches reference
--      source_bundles; builds stands alone. Bundle rows, build/usage/Scratch history and
--      Retention Pins have no remaining reader. The on-disk source-bundles/ directories
--      are removed once at startup (startup.remove_retired_source_bundle_storage).
--   2. resource_lock.holder_kind: the long Group hold a spec run's preparing copy takes
--      (holder run_prepare, 0684 T#2) was stored under the Bundle label 'bundle'. It is
--      now stored as itself and 'bundle' leaves the CHECK; a row left by the prior
--      process is relabelled, not dropped.
-- TS meta test_basis / superseded_test_basis (v1/v2 Basis, Bundle binding included) is
-- document metadata, not a table here, and stays untouched (D#1 §7).
DROP TABLE IF EXISTS source_bundle_pins;
DROP TABLE IF EXISTS source_bundle_usages;
DROP TABLE IF EXISTS source_bundle_scratches;
DROP TABLE IF EXISTS source_bundle_builds;
DROP TABLE IF EXISTS source_bundles;

SET @fg_chk := (
  SELECT tc.CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS tc
  JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME
  WHERE tc.CONSTRAINT_SCHEMA=DATABASE() AND tc.TABLE_NAME='resource_lock' AND tc.CONSTRAINT_TYPE='CHECK'
    AND cc.CHECK_CLAUSE LIKE '%holder_kind%' LIMIT 1
);
SET @fg_sql := IF(@fg_chk IS NULL, 'SELECT 1', CONCAT('ALTER TABLE resource_lock DROP CHECK `', REPLACE(@fg_chk,'`','``'), '`'));
PREPARE fg_stmt FROM @fg_sql; EXECUTE fg_stmt; DEALLOCATE PREPARE fg_stmt;

UPDATE resource_lock SET holder_kind = 'run_prepare' WHERE holder_kind = 'bundle';

ALTER TABLE resource_lock ADD CONSTRAINT resource_lock_holder_kind_check CHECK (holder_kind IN ('selfcheck', 'run_prepare', 'tr2_apply', 'source_mutation', 'tr_commit', 'time_machine', 'tr_conflict', 'approval_freeze', 'publish', 'worktree_provision', 'worktree_cleanup', 'base_mutation', 'branch_meta', 'branch_merge', 'archive', 'rerere', 'merge_resolve', 'review_action', 'project_provision', 'sweeper', 'legacy_bridge'));
