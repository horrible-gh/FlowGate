-- 095_ai_invoke_run_transport_diagnostics.sql
-- flowgate.default.0505 DB0005 / T0006: API provider server-mediated self-HTTP
-- diagnostics. NR0003 §13 found that when the mediated tool/register round-trip inside
-- an API-provider hop fails, nothing durable records which internal call failed, with
-- what status, or which base URL it used -- the only trace was process memory, gone at
-- the next restart. These ten columns give a finished hop's ai_invoke_runs row that
-- trace: the operator-facing and internal-transport API bases (sanitized snapshots,
-- see _sanitize_diagnostic_base), the last mediated self-HTTP call's name/status/error,
-- and the hop's turn/model-call/tool-call counters.
-- Additive only, all ten NULL-allowed with no default and no CHECK (same as 086c's
-- timeout_kind/stdout_tail/stderr_tail/source_dirty_files): NULL means "this hop
-- predates the migration or never touched this path", never "zero". No IF NOT EXISTS
-- (this deployment's MySQL baseline does not support it on ADD COLUMN, same as 086c)
-- and no AFTER clause -- column order here is physical only; the logical order lives
-- in ai_invoke_runs._BOUND_COLUMNS (DB0005 §2).

ALTER TABLE ai_invoke_runs ADD COLUMN operator_api_base TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN transport_api_base TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN last_tool_name VARCHAR(64);
ALTER TABLE ai_invoke_runs ADD COLUMN last_tool_status INTEGER;
ALTER TABLE ai_invoke_runs ADD COLUMN last_tool_error TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN api_turns_used INTEGER;
ALTER TABLE ai_invoke_runs ADD COLUMN model_http_calls INTEGER;
ALTER TABLE ai_invoke_runs ADD COLUMN model_last_http_status INTEGER;
ALTER TABLE ai_invoke_runs ADD COLUMN tool_calls_received INTEGER;
ALTER TABLE ai_invoke_runs ADD COLUMN tool_calls_executed INTEGER;
