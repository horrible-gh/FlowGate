-- 060_git_commit_message.sql
-- Git commit-message improvement (flowgate.default.0173: R0001 -> D0002 -> P0003 -> L0004).
-- Two nullable, additive columns:
--   documents.commit_message        — English one-line commit subject drafted by the TR
--                                      worker at report time (TR docs only; L0004 §6).
--   project_git_config.translate_url — LibreTranslate base URL used to translate a
--                                      non-ASCII group title into an English subject
--                                      ("" normalized to NULL = disabled by the app layer).
-- Additive only, no index (the resolver filters by group_id + type_code, a few dozen
-- rows per group — existing access paths suffice; L0004 §6). No CHECK.

BEGIN;

ALTER TABLE documents          ADD COLUMN commit_message TEXT;
ALTER TABLE project_git_config ADD COLUMN translate_url  TEXT;

COMMIT;
