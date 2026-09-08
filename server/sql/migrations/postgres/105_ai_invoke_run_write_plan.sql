-- 105_ai_invoke_run_write_plan.sql
-- flowgate.default.0481 T0008 item 1 / L0007 §2.5-§2.6, Q&A on 0009-TR: the
-- anchored write-plan engine consumed by the merge review's explicit [수정 적용]
-- turn (git_service.apply_write_plan) needs a durable, restart-safe home for the
-- plan an AI run produces, matching the bound contract -- `GET /ai-invoke/{run_id}`
-- top-level `write_plan` / `write_requested_by_human` / `allow_test_edits`, with
-- the memory-run and DB-restored-run shapes identical. `write_requested_by_human`
-- and `allow_test_edits` are the run-start POLICY (was this conversation turn a
-- write turn, and was [테스트 편집 포함 재지시] used); `write_plan_json` is the
-- plan itself, written once by the worker-token submission endpoint before the
-- run exits. NULL on all three = this hop predates the migration, or was never a
-- review-message write turn (the overwhelming majority of resolve_conflict runs).
-- Additive only, no default, no CHECK (SQLite ADD COLUMN cannot carry one, same
-- as every prior ai_invoke_runs column added this way).

ALTER TABLE ai_invoke_runs ADD COLUMN write_requested_by_human INTEGER;
ALTER TABLE ai_invoke_runs ADD COLUMN allow_test_edits INTEGER;
ALTER TABLE ai_invoke_runs ADD COLUMN write_plan_json TEXT;