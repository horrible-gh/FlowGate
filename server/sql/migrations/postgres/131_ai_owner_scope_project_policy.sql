-- 131_ai_owner_scope_project_policy.sql
-- flowgate.v02.0017 TR0003 rev7 (Q 361~363). Same meaning as sqlite/131:
-- ai_providers.owner_scope/owner_user_id/project_access, project_ai_policies,
-- project_ai_policy_providers; project-owned rows become 'listed' system AI allowed only by the
-- project that owned them (provider_id unchanged). A project CLI row that copies a system AI (same
-- kind and command) is folded into that system AI instead (see _ai_provider_copy below).

BEGIN;

ALTER TABLE ai_providers ADD COLUMN IF NOT EXISTS owner_scope TEXT NOT NULL DEFAULT 'system' CHECK (owner_scope IN ('system', 'user'));
ALTER TABLE ai_providers ADD COLUMN IF NOT EXISTS owner_user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE;
ALTER TABLE ai_providers ADD COLUMN IF NOT EXISTS project_access TEXT NOT NULL DEFAULT 'all' CHECK (project_access IN ('all', 'listed'));

CREATE TABLE IF NOT EXISTS project_ai_policies (
    project_id TEXT PRIMARY KEY REFERENCES projects(project_id) ON DELETE CASCADE,
    ai_enabled INTEGER NOT NULL DEFAULT 1 CHECK (ai_enabled IN (0, 1)),
    system_ai_scope TEXT NOT NULL DEFAULT 'all' CHECK (system_ai_scope IN ('all', 'selected')),
    allow_user_ai INTEGER NOT NULL DEFAULT 0 CHECK (allow_user_ai IN (0, 1)),
    default_provider_id TEXT REFERENCES ai_providers(provider_id) ON DELETE SET NULL,
    updated_by TEXT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_ai_policy_providers (
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL REFERENCES ai_providers(provider_id) ON DELETE CASCADE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, provider_id)
);
CREATE INDEX IF NOT EXISTS idx_project_ai_policy_providers_provider ON project_ai_policy_providers(provider_id);

-- A project CLI row that runs the same command as an existing system AI is a copy, not a new AI
-- (v0.1 custom mode made an operator re-register the system AI per project). Promoting it would
-- list the same AI twice as "<name> · <project>" in the shared system list (CH0006 #46), so it is
-- folded into the system row instead: AI-management references (policy list, document-type map,
-- groups, evaluations) move to the system row, and the copy stays behind as a disabled project
-- row that nothing lists — run / review / question history keeps resolving its id and name.
-- API rows are never folded: their key cannot be compared, and a project may bill its own key.
CREATE TEMP TABLE _ai_provider_copy ON COMMIT DROP AS
SELECT c.provider_id AS copy_id,
       (SELECT s.provider_id FROM ai_providers s
        WHERE s.project_id IS NULL AND s.exec_type = 'cli' AND s.kind = c.kind
          AND trim(COALESCE(s.cli_command, '')) = trim(c.cli_command)
        ORDER BY s.sort_order, s.provider_id LIMIT 1) AS system_id
FROM ai_providers c
WHERE c.project_id IS NOT NULL AND c.exec_type = 'cli' AND trim(COALESCE(c.cli_command, '')) <> '';
DELETE FROM _ai_provider_copy WHERE system_id IS NULL;

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
   OR EXISTS (SELECT 1 FROM ai_providers o WHERE o.project_id = p.project_id)
ON CONFLICT (project_id) DO NOTHING;

INSERT INTO project_ai_policy_providers (project_id, provider_id, sort_order, created_at)
SELECT project_id, COALESCE((SELECT system_id FROM _ai_provider_copy WHERE copy_id = provider_id), provider_id),
       sort_order, updated_at
FROM ai_providers WHERE project_id IS NOT NULL
ON CONFLICT (project_id, provider_id) DO NOTHING;

UPDATE project_ai_policies
SET default_provider_id = (SELECT system_id FROM _ai_provider_copy WHERE copy_id = default_provider_id)
WHERE default_provider_id IN (SELECT copy_id FROM _ai_provider_copy);
UPDATE project_settings
SET ai_default_provider_id = (SELECT system_id FROM _ai_provider_copy WHERE copy_id = ai_default_provider_id)
WHERE ai_default_provider_id IN (SELECT copy_id FROM _ai_provider_copy);
UPDATE ai_provider_doctype_map
SET provider_id = (SELECT system_id FROM _ai_provider_copy WHERE copy_id = provider_id)
WHERE provider_id IN (SELECT copy_id FROM _ai_provider_copy);
-- A group that already has the system AI keeps the copy's member as is (shown as unavailable).
UPDATE ai_group_members
SET provider_id = (SELECT system_id FROM _ai_provider_copy WHERE copy_id = provider_id)
WHERE provider_id IN (SELECT copy_id FROM _ai_provider_copy)
  AND NOT EXISTS (SELECT 1 FROM ai_group_members o
                  WHERE o.ai_group_id = ai_group_members.ai_group_id
                    AND o.provider_id = (SELECT system_id FROM _ai_provider_copy WHERE copy_id = ai_group_members.provider_id));
UPDATE ai_group_runtime_bindings
SET provider_id = (SELECT system_id FROM _ai_provider_copy WHERE copy_id = provider_id)
WHERE provider_id IN (SELECT copy_id FROM _ai_provider_copy);
UPDATE ai_groups
SET work_executor_provider_id = (SELECT system_id FROM _ai_provider_copy WHERE copy_id = work_executor_provider_id)
WHERE work_executor_provider_id IN (SELECT copy_id FROM _ai_provider_copy);
UPDATE ai_groups
SET reviewer_provider_id = (SELECT system_id FROM _ai_provider_copy WHERE copy_id = reviewer_provider_id)
WHERE reviewer_provider_id IN (SELECT copy_id FROM _ai_provider_copy);
UPDATE ai_groups
SET reworker_provider_id = (SELECT system_id FROM _ai_provider_copy WHERE copy_id = reworker_provider_id)
WHERE reworker_provider_id IN (SELECT copy_id FROM _ai_provider_copy);
UPDATE ai_evaluations
SET target_provider_id = (SELECT system_id FROM _ai_provider_copy WHERE copy_id = target_provider_id)
WHERE target_provider_id IN (SELECT copy_id FROM _ai_provider_copy)
  AND judge_provider_id <> (SELECT system_id FROM _ai_provider_copy WHERE copy_id = target_provider_id);
UPDATE ai_evaluations
SET judge_provider_id = (SELECT system_id FROM _ai_provider_copy WHERE copy_id = judge_provider_id)
WHERE judge_provider_id IN (SELECT copy_id FROM _ai_provider_copy)
  AND target_provider_id <> (SELECT system_id FROM _ai_provider_copy WHERE copy_id = judge_provider_id);
-- The left-behind copy: off, and renamed so it never collides in the system name index below.
UPDATE ai_providers SET enabled = 0, name = name || ' · ' || project_id
WHERE provider_id IN (SELECT copy_id FROM _ai_provider_copy);


DROP INDEX IF EXISTS ux_ai_providers_global_name;
DROP INDEX IF EXISTS ux_ai_providers_project_name;

UPDATE ai_providers
SET name = name || ' · ' || project_id
WHERE project_id IS NOT NULL
  AND provider_id NOT IN (SELECT copy_id FROM _ai_provider_copy)
  AND lower(name) IN (SELECT lower(name) FROM ai_providers
                      WHERE provider_id NOT IN (SELECT copy_id FROM _ai_provider_copy)
                      GROUP BY lower(name) HAVING COUNT(*) > 1);

-- Promoted rows follow the existing system AI in the system list (the next save renumbers).
UPDATE ai_providers SET project_access = 'listed', sort_order = sort_order + 1000, project_id = NULL
WHERE project_id IS NOT NULL AND provider_id NOT IN (SELECT copy_id FROM _ai_provider_copy);

CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_providers_system_name
    ON ai_providers(name) WHERE owner_scope = 'system';
CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_providers_user_name
    ON ai_providers(owner_user_id, name) WHERE owner_scope = 'user';
CREATE INDEX IF NOT EXISTS idx_ai_providers_owner ON ai_providers(owner_scope, owner_user_id);

COMMIT;
