-- 086_ai_invoke_run_diagnostics.sql
-- flowgate.default.0446 (B0001 -> CH0012 -> WP0013 -> T0016 §2):
-- Persist why an AI run stopped beyond the process lifetime.
--
-- The no-progress watchdog added by T0014 recorded its decision only in run["watchdog_kill"]. stdout/stderr
-- tails and unfinished changed-file lists likewise appeared only in the completion response or in-memory run.
-- After a server restart, the next session cannot tell whether the previous run timed out during work
-- or which files remain unfinished. To pass this information to a worker invoked for rejection rework,
-- it must be retained on the completed row.
--
-- Add five columns, all on ai_invoke_runs and queryable by run_id.
--   timeout_kind        No progress (no_progress) or the four-hour absolute limit (absolute_cap).
--                       Existing/legacy timeout rows without a watchdog marker remain NULL and distinguishable.
--                       The next step must select only runs stopped for lack of progress,
--                       without parsing human-readable sentences.
--   timeout_diagnosis   One human-readable diagnostic line including total elapsed time and the final
--                       no-progress interval. Like stop_reason in the same table,
--                       it follows the convention of one English sentence.
--   stdout_tail         The value already truncated by _cli_execute() to OUTPUT_TAIL_BYTES.
--   stderr_tail         Likewise. Do not store additional full output or prompts.
--   source_dirty_files  JSON array of at most 20 paths calculated by finalize.
--
-- **Do not store the full original text.** Keep truncation limits and the 90-day retention policy unchanged.
--
-- Additive only: do not remove or rename existing columns or backfill. Runs completed before
-- Previously completed runs read as NULL, accurately indicating that this was not recorded then.

-- SQLite cannot attach CHECK via ADD COLUMN (same rationale as 076b/080). The service layer enforces
-- timeout_kind vocabulary (no_progress / absolute_cap / NULL).
ALTER TABLE ai_invoke_runs ADD COLUMN timeout_kind TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN timeout_diagnosis TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN stdout_tail TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN stderr_tail TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN source_dirty_files TEXT;
