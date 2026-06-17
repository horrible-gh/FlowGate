-- 051_tokens_continuation_locale.sql
-- Continuous-work locale persistence — group 0099 B0001 (NR0003 cause #2).
--
-- The unmanned continuous chain auto-creates + auto-approves instruction-series heads
-- (N/T/TS) server-side. Their generated title/body must honor the locale the user chose
-- in the continuous-work dialog. But the locale was NOT persisted on the continuation
-- token: the inbox self-chain re-derived it from each request's x-locale header, which the
-- unmanned AI worker does not send → it always folded to 'ko'. So even after the
-- generator was localized, the chosen locale was lost on every self-chain hop.
--
-- This additive column carries the chosen locale on the continuation token so the
-- self-chain can prefer it over the (absent) request header. NULL for ordinary tokens and
-- for legacy continuation tokens (the self-chain then falls back to the request header /
-- 'ko', exactly the prior behavior — fully backward compatible). Same additive pattern as
-- 050 continuation flags / 043 dry_run_count — no table rebuild, no CHECK change.

ALTER TABLE tokens ADD COLUMN continuation_locale TEXT;
