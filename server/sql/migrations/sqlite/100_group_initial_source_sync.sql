-- 100_group_initial_source_sync.sql
-- Initial AI source-access worktree sync (flowgate.default.0511 T0004 / NR0003 v5).
-- Two nullable, additive columns on group_git_state marking the ONE forced
-- reset+clean sync of the group worktree to the current configured base-branch
-- HEAD, performed just before the group's FIRST raw source-capable
-- (tool_registry.kind_for_token() in {read, read_write}) AI invocation ever
-- uses that worktree as its cwd:
--   initial_source_sync_at  — when the sync (or legacy backfill) landed.
--   initial_source_sync_sha — the sha the worktree was reset to (base HEAD),
--                             or the group's own HEAD when backfilled for a
--                             legacy group with prior tr_commit_ledger history.
-- NULL/NULL = not yet synced. Additive only, no index, no CHECK — same
-- protocol as author_name/author_email (migration 065).

BEGIN;

ALTER TABLE group_git_state ADD COLUMN initial_source_sync_at  TEXT;
ALTER TABLE group_git_state ADD COLUMN initial_source_sync_sha TEXT;

COMMIT;
