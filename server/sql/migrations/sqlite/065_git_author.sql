-- 065_git_author.sql
-- Configurable git author (flowgate.default.0237: R0001 -> N0002 -> NR0003 -> T0004).
-- Two nullable, additive columns on project_git_config:
--   author_name  — git author name for commits the server makes on this project's
--                  behalf. NULL = fall back to the built-in "FlowGate" identity.
--   author_email — git author email; paired with author_name (both set or both NULL,
--                  enforced by the app layer — an empty ident makes `git commit` fail
--                  with "Author identity unknown").
-- The COMMITTER stays "FlowGate" regardless: these commits really are made by the
-- server, and the contribution graphs of GitHub/GitLab key off the AUTHOR, which is
-- what R0001 asks for.
-- Additive only, no index, no CHECK ("" normalized to NULL by the app layer —
-- same protocol as translate_url, migration 060).

BEGIN;

ALTER TABLE project_git_config ADD COLUMN author_name  TEXT;
ALTER TABLE project_git_config ADD COLUMN author_email TEXT;

COMMIT;
