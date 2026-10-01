-- 131_ai_owner_scope_project_policy.sql
-- flowgate.v02.0017 TR0003 rev7 (Q 361~363): AI is owned by the system or by one user, and a
-- project only keeps a usage policy.
--
--   ai_providers.owner_scope     'system' (managed by an administrator; runs on the server or a
--                                System Agent) / 'user' (a user's own AI; runs on that user's Agent).
--   ai_providers.owner_user_id   owner of a 'user' AI, NULL for 'system'.
--   ai_providers.project_access  'all' = every project whose policy allows all system AI may use it,
--                                'listed' = only projects that list it in their policy.
--   project_ai_policies          AI on/off, which system AI is allowed ('all' / 'selected'),
--                                whether members may use their own AI, default AI.
--   project_ai_policy_providers  system AI a project lists explicitly (order = fallback order in
--                                'selected').
--
-- Existing project-owned rows (project_id NOT NULL) keep their provider_id. They become system AI
-- with project_access='listed' and are listed only in the policy of the project that owned them,
-- so no other project gains access. A name that would collide in the system list gets the
-- former project id appended. project_settings.ai_mode / ai_default_provider_id are no longer read.

PRAGMA foreign_keys = OFF;
BEGIN;

ALTER TABLE ai_providers ADD COLUMN owner_scope TEXT NOT NULL DEFAULT 'system' CHECK (owner_scope IN ('system', 'user'));
ALTER TABLE ai_providers ADD COLUMN owner_user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE;
ALTER TABLE ai_providers ADD COLUMN project_access TEXT NOT NULL DEFAULT 'all' CHECK (project_access IN ('all', 'listed'));

CREATE TABLE project_ai_policies (
    project_id TEXT PRIMARY KEY REFERENCES projects(project_id) ON DELETE CASCADE,
    ai_enabled INTEGER NOT NULL DEFAULT 1 CHECK (ai_enabled IN (0, 1)),
    system_ai_scope TEXT NOT NULL DEFAULT 'all' CHECK (system_ai_scope IN ('all', 'selected')),
    allow_user_ai INTEGER NOT NULL DEFAULT 0 CHECK (allow_user_ai IN (0, 1)),
    default_provider_id TEXT REFERENCES ai_providers(provider_id) ON DELETE SET NULL,
    updated_by TEXT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE project_ai_policy_providers (
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL REFERENCES ai_providers(provider_id) ON DELETE CASCADE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, provider_id)
);
CREATE INDEX IF NOT EXISTS idx_project_ai_policy_providers_provider ON project_ai_policy_providers(provider_id);

-- Policy for every project that had a stored mode or its own rows.
INSERT INTO project_ai_policies (project_id, ai_enabled, system_ai_scope, allow_user_ai,
                                 default_provider_id, created_at, updated_at)
SELECT p.project_id,
       CASE WHEN ps.ai_mode = 'disabled' THEN 0 ELSE 1 END,
       CASE WHEN EXISTS (SELECT 1 FROM ai_providers o WHERE o.project_id = p.project_id)
                 AND COALESCE(ps.ai_mode, 'inherit') IN ('custom', 'disabled')
            THEN 'selected' ELSE 'all' END,
       0,
       CASE WHEN COALESCE(ps.ai_mode, 'inherit') IN ('custom', 'disabled')
                 AND EXISTS (SELECT 1 FROM ai_providers d
                             WHERE d.provider_id = ps.ai_default_provider_id
                               AND d.project_id = p.project_id)
            THEN ps.ai_default_provider_id END,
       COALESCE(ps.updated_at, (SELECT MAX(o.updated_at) FROM ai_providers o WHERE o.project_id = p.project_id)),
       COALESCE(ps.updated_at, (SELECT MAX(o.updated_at) FROM ai_providers o WHERE o.project_id = p.project_id))
FROM projects p
LEFT JOIN project_settings ps ON ps.project_id = p.project_id
WHERE ps.ai_mode IS NOT NULL
   OR EXISTS (SELECT 1 FROM ai_providers o WHERE o.project_id = p.project_id);

INSERT INTO project_ai_policy_providers (project_id, provider_id, sort_order, created_at)
SELECT project_id, provider_id, sort_order, updated_at FROM ai_providers WHERE project_id IS NOT NULL;

DROP INDEX IF EXISTS ux_ai_providers_global_name;
DROP INDEX IF EXISTS ux_ai_providers_project_name;

UPDATE ai_providers
SET name = name || ' · ' || project_id
WHERE project_id IS NOT NULL
  AND lower(name) IN (SELECT lower(name) FROM ai_providers GROUP BY lower(name) HAVING COUNT(*) > 1);

-- Promoted rows follow the existing system AI in the system list (the next save renumbers).
UPDATE ai_providers SET project_access = 'listed', sort_order = sort_order + 1000, project_id = NULL
WHERE project_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_providers_system_name
    ON ai_providers(name) WHERE owner_scope = 'system';
CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_providers_user_name
    ON ai_providers(owner_user_id, name) WHERE owner_scope = 'user';
CREATE INDEX IF NOT EXISTS idx_ai_providers_owner ON ai_providers(owner_scope, owner_user_id);

COMMIT;
PRAGMA foreign_keys = ON;
