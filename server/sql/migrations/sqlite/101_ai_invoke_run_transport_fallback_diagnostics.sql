-- 101_ai_invoke_run_transport_fallback_diagnostics.sql
-- flowgate.default.0496 T0006 §3.2: `_resolve_transport_api_base` (provider_api.py)
-- has always known WHY it returned the value it did -- the try succeeded outright, an
-- override that failed to parse was retried away (safe: loopback+FLOWGATE_PORT), or
-- nothing parsed at all so the operator base rode through unchanged (the one branch
-- NR0003/0496 T0004 traced the original cross-instance 401 "Token is invalid" to) --
-- but until now that distinction lived only in a `logger.warning` line, gone at the
-- next restart and only found by grep. This column persists it as a string, not a
-- boolean, because the two exception branches are NOT equally safe and collapsing
-- them into one flag would erase the exact distinction this migration exists to keep:
-- NULL = this hop predates the migration or never resolved a transport base;
-- 'none' = no override involved, or the configured override worked as given;
-- 'override_ignored' = a configured-but-broken FLOWGATE_AGENT_API_BASE was retried
--   with ignore_configured_override=True and landed on the safe loopback answer;
-- 'operator_base_unsafe' = even the operator base itself could not be parsed, so it
--   was returned unchanged -- the unsafe branch.
-- Additive only, NULL-allowed with no default and no CHECK (SQLite ADD COLUMN cannot
-- carry one, same as 095's ten columns).

ALTER TABLE ai_invoke_runs ADD COLUMN transport_fallback_kind TEXT;
