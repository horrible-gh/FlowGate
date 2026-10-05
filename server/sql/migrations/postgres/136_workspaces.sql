-- 136_workspaces.sql  (postgres)
-- flowgate.v02.0020 TR (시안 11~14). Same meaning as sqlite/136: workspaces, workspace_members,
-- workspace_project_access, workspace_ai_allowlist, projects.workspace_id, users.last_login_at and
-- the invitation columns. Existing projects move into the default workspace 'FlowGate Workspace';
-- existing project roles are kept as per-person exceptions where they differ from the role
-- default, so no project access changes here.

CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    ai_policy_mode TEXT NOT NULL DEFAULT 'off' CHECK (ai_policy_mode IN ('off', 'allowlist')),
    project_create_policy TEXT NOT NULL DEFAULT 'owner_admin'
        CHECK (project_create_policy IN ('owner', 'owner_admin', 'all')),
    default_access_owner TEXT NOT NULL DEFAULT 'admin'
        CHECK (default_access_owner IN ('admin', 'dev', 'review', 'none')),
    default_access_admin TEXT NOT NULL DEFAULT 'dev'
        CHECK (default_access_admin IN ('admin', 'dev', 'review', 'none')),
    default_access_member TEXT NOT NULL DEFAULT 'none'
        CHECK (default_access_member IN ('admin', 'dev', 'review', 'none')),
    created_by TEXT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member')),
    added_by TEXT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_workspace_members_user ON workspace_members(user_id);

CREATE TABLE IF NOT EXISTS workspace_project_access (
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    access TEXT NOT NULL CHECK (access IN ('admin', 'dev', 'review', 'none')),
    updated_by TEXT REFERENCES users(user_id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_workspace_project_access_ws ON workspace_project_access(workspace_id, user_id);

CREATE TABLE IF NOT EXISTS workspace_ai_allowlist (
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    resource_type TEXT NOT NULL CHECK (resource_type IN ('provider', 'agent')),
    resource_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'approved' CHECK (status IN ('pending', 'approved', 'rejected', 'revoked')),
    source TEXT NOT NULL DEFAULT 'admin' CHECK (source IN ('admin', 'request')),
    requested_by TEXT REFERENCES users(user_id) ON DELETE SET NULL,
    decided_by TEXT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, resource_type, resource_id)
);

ALTER TABLE projects ADD COLUMN IF NOT EXISTS workspace_id TEXT REFERENCES workspaces(workspace_id);
CREATE INDEX IF NOT EXISTS idx_projects_workspace ON projects(workspace_id);

ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS invited_at TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS invite_token_hash TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS invite_expires_at TEXT;

INSERT INTO workspaces (workspace_id, name, name_key, status, is_default, ai_policy_mode,
                        project_create_policy, default_access_owner, default_access_admin,
                        default_access_member, created_by, created_at, updated_at)
VALUES ('ws_default', 'FlowGate Workspace', 'flowgate workspace', 'active', 1, 'off',
        'all', 'admin', 'dev', 'none', NULL,
        to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'),
        to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
ON CONFLICT DO NOTHING;

UPDATE projects SET workspace_id = 'ws_default' WHERE project_id <> '__SYSTEM__';

INSERT INTO workspace_members (workspace_id, user_id, role, added_by, created_at, updated_at)
SELECT 'ws_default', u.user_id, 'owner', NULL,
       to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'),
       to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
FROM users u
WHERE u.is_admin = 1 AND u.is_active = 1
ORDER BY u.created_at, u.user_id
LIMIT 1
ON CONFLICT DO NOTHING;

INSERT INTO workspace_members (workspace_id, user_id, role, added_by, created_at, updated_at)
SELECT 'ws_default', u.user_id, 'admin', NULL,
       to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'),
       to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
FROM users u
WHERE u.is_admin = 1
ON CONFLICT DO NOTHING;

INSERT INTO workspace_members (workspace_id, user_id, role, added_by, created_at, updated_at)
SELECT DISTINCT 'ws_default', upr.user_id, 'member', NULL,
       to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'),
       to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
FROM user_project_roles upr
WHERE upr.project_id <> '__SYSTEM__'
ON CONFLICT DO NOTHING;

INSERT INTO workspace_project_access (project_id, user_id, workspace_id, access, updated_by, updated_at)
SELECT upr.project_id, upr.user_id, 'ws_default',
       CASE upr.role_id WHEN 'role_admin' THEN 'admin' WHEN 'role_manager' THEN 'admin'
                        WHEN 'role_worker' THEN 'dev' ELSE 'review' END,
       NULL, to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
FROM user_project_roles upr
JOIN workspace_members m ON m.workspace_id = 'ws_default' AND m.user_id = upr.user_id
WHERE upr.project_id <> '__SYSTEM__'
  AND (CASE upr.role_id WHEN 'role_admin' THEN 'admin' WHEN 'role_manager' THEN 'admin'
                        WHEN 'role_worker' THEN 'dev' ELSE 'review' END)
      <> (CASE m.role WHEN 'owner' THEN 'admin' WHEN 'admin' THEN 'dev' ELSE 'none' END)
ON CONFLICT DO NOTHING;
