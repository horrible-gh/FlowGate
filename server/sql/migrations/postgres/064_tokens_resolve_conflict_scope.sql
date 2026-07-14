-- 064_tokens_resolve_conflict_scope.sql
-- flowgate.default.0233 T0011: allow tokens issued for AI conflict-resolution.
--
-- Migration 063 added tokens.merge_id for resolve_conflict worker tokens but omitted
-- updating the action_scope CHECK, so issuing a resolve_conflict token (copy-mention
-- [멘트복사] via /token/issue and [AI호출] via /ai-invoke/start) violated
-- tokens_action_scope_check -> CheckViolation -> bodyless HTTP 500 (NR0010). 063 is
-- already applied in prod (forward-only), so this NEW migration carries the fix.
--
-- Postgres names the inline column CHECK from migration 062 'tokens_action_scope_check'
-- (confirmed by the production violation log), so drop it and re-add the 7-value list.
ALTER TABLE tokens DROP CONSTRAINT tokens_action_scope_check;
ALTER TABLE tokens ADD CONSTRAINT tokens_action_scope_check
    CHECK (action_scope IN ('new', 'edit', 'workflow_decide', 'review', 'test_run', 'workflow_sequence_edit', 'resolve_conflict'));
