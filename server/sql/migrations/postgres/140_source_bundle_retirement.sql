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

DO $$
DECLARE
    r record;
BEGIN
    -- Replace only the CHECK that governs holder_kind; discover its name from the catalog.
    FOR r IN
        SELECT c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_attribute a
          ON a.attrelid = t.oid
         AND a.attnum = ANY (c.conkey)
        WHERE n.nspname = current_schema()
          AND t.relname = 'resource_lock'
          AND c.contype = 'c'
          AND a.attname = 'holder_kind'
    LOOP
        EXECUTE format('ALTER TABLE %I.resource_lock DROP CONSTRAINT %I', current_schema(), r.conname);
    END LOOP;
END $$;

UPDATE resource_lock SET holder_kind = 'run_prepare' WHERE holder_kind = 'bundle';

ALTER TABLE resource_lock
    ADD CONSTRAINT resource_lock_holder_kind_check
    CHECK (holder_kind IN ('selfcheck', 'run_prepare', 'tr2_apply', 'source_mutation', 'tr_commit', 'time_machine', 'tr_conflict', 'approval_freeze', 'publish', 'worktree_provision', 'worktree_cleanup', 'base_mutation', 'branch_meta', 'branch_merge', 'archive', 'rerere', 'merge_resolve', 'review_action', 'project_provision', 'sweeper', 'legacy_bridge'));
