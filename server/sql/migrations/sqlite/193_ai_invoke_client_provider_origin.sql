-- 193_ai_invoke_client_provider_origin.sql (sqlite)
-- flowgate.v02test.0030 (0001-B): a chat AI call's own account of how the browser picked its AI --
-- user_pick / stored / last_answered / default_fallback (NULL = not told: an older browser, any other scope).
-- selected_provider_source says only that the request named the AI ('request'); this says why the screen did,
-- so a run on an AI the user did not choose can be traced to that path from the row alone.
-- DOWN: ALTER TABLE ai_invoke_runs DROP COLUMN client_provider_origin;
ALTER TABLE ai_invoke_runs ADD COLUMN client_provider_origin TEXT NULL;
