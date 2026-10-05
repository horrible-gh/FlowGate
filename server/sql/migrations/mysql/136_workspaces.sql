-- 136_workspaces.sql  (mysql)
-- flowgate.v02.0020 TR (시안 11~14). Same meaning as sqlite/136: workspaces, workspace_members,
-- workspace_project_access, workspace_ai_allowlist, projects.workspace_id, users.last_login_at and
-- the invitation columns. Existing projects move into the default workspace 'FlowGate Workspace';
-- existing project roles are kept as per-person exceptions where they differ from the role
-- default, so no project access changes here.

CREATE TABLE workspaces (
    workspace_id VARCHAR(191) NOT NULL PRIMARY KEY,
    name VARCHAR(191) NOT NULL,
    name_key VARCHAR(191) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    is_default TINYINT NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    ai_policy_mode VARCHAR(16) NOT NULL DEFAULT 'off' CHECK (ai_policy_mode IN ('off', 'allowlist')),
    project_create_policy VARCHAR(16) NOT NULL DEFAULT 'owner_admin'
        CHECK (project_create_policy IN ('owner', 'owner_admin', 'all')),
    default_access_owner VARCHAR(16) NOT NULL DEFAULT 'admin'
        CHECK (default_access_owner IN ('admin', 'dev', 'review', 'none')),
    default_access_admin VARCHAR(16) NOT NULL DEFAULT 'dev'
        CHECK (default_access_admin IN ('admin', 'dev', 'review', 'none')),
    default_access_member VARCHAR(16) NOT NULL DEFAULT 'none'
        CHECK (default_access_member IN ('admin', 'dev', 'review', 'none')),
    created_by VARCHAR(191) NULL,
    created_at VARCHAR(64) NOT NULL,
    updated_at VARCHAR(64) NOT NULL,
    UNIQUE KEY ux_workspaces_name_key (name_key),
    CONSTRAINT fk_ws_created_by FOREIGN KEY (created_by) REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE TABLE workspace_members (
    workspace_id VARCHAR(191) NOT NULL,
    user_id VARCHAR(191) NOT NULL,
    role VARCHAR(16) NOT NULL CHECK (role IN ('owner', 'admin', 'member')),
    added_by VARCHAR(191) NULL,
    created_at VARCHAR(64) NOT NULL,
    updated_at VARCHAR(64) NOT NULL,
    PRIMARY KEY (workspace_id, user_id),
    KEY idx_workspace_members_user (user_id),
    CONSTRAINT fk_wsm_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    CONSTRAINT fk_wsm_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    CONSTRAINT fk_wsm_added_by FOREIGN KEY (added_by) REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE TABLE workspace_project_access (
    project_id VARCHAR(191) NOT NULL,
    user_id VARCHAR(191) NOT NULL,
    workspace_id VARCHAR(191) NOT NULL,
    access VARCHAR(16) NOT NULL CHECK (access IN ('admin', 'dev', 'review', 'none')),
    updated_by VARCHAR(191) NULL,
    updated_at VARCHAR(64) NOT NULL,
    PRIMARY KEY (project_id, user_id),
    KEY idx_workspace_project_access_ws (workspace_id, user_id),
    CONSTRAINT fk_wspa_project FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    CONSTRAINT fk_wspa_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    CONSTRAINT fk_wspa_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    CONSTRAINT fk_wspa_updated_by FOREIGN KEY (updated_by) REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE TABLE workspace_ai_allowlist (
    workspace_id VARCHAR(191) NOT NULL,
    resource_type VARCHAR(16) NOT NULL CHECK (resource_type IN ('provider', 'agent')),
    resource_id VARCHAR(191) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'approved' CHECK (status IN ('pending', 'approved', 'rejected', 'revoked')),
    source VARCHAR(16) NOT NULL DEFAULT 'admin' CHECK (source IN ('admin', 'request')),
    requested_by VARCHAR(191) NULL,
    decided_by VARCHAR(191) NULL,
    created_at VARCHAR(64) NOT NULL,
    updated_at VARCHAR(64) NOT NULL,
    PRIMARY KEY (workspace_id, resource_type, resource_id),
    CONSTRAINT fk_wsai_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    CONSTRAINT fk_wsai_requested_by FOREIGN KEY (requested_by) REFERENCES users(user_id) ON DELETE SET NULL,
    CONSTRAINT fk_wsai_decided_by FOREIGN KEY (decided_by) REFERENCES users(user_id) ON DELETE SET NULL
);

ALTER TABLE projects ADD COLUMN workspace_id VARCHAR(191) NULL;
ALTER TABLE projects ADD CONSTRAINT fk_projects_workspace
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id);
CREATE INDEX idx_projects_workspace ON projects(workspace_id);

ALTER TABLE users ADD COLUMN last_login_at VARCHAR(64) NULL;
ALTER TABLE users ADD COLUMN invited_at VARCHAR(64) NULL;
ALTER TABLE users ADD COLUMN invite_token_hash VARCHAR(128) NULL;
ALTER TABLE users ADD COLUMN invite_expires_at VARCHAR(64) NULL;

INSERT IGNORE INTO workspaces (workspace_id, name, name_key, status, is_default, ai_policy_mode,
                               project_create_policy, default_access_owner, default_access_admin,
                               default_access_member, created_by, created_at, updated_at)
VALUES ('ws_default', 'FlowGate Workspace', 'flowgate workspace', 'active', 1, 'off',
        'all', 'admin', 'dev', 'none', NULL,
        DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d %H:%i:%s'), DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d %H:%i:%s'));

UPDATE projects SET workspace_id = 'ws_default' WHERE project_id <> '__SYSTEM__';

INSERT IGNORE INTO workspace_members (workspace_id, user_id, role, added_by, created_at, updated_at)
SELECT 'ws_default', u.user_id, 'owner', NULL,
       DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d %H:%i:%s'), DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d %H:%i:%s')
FROM users u
WHERE u.is_admin = 1 AND u.is_active = 1
ORDER BY u.created_at, u.user_id
LIMIT 1;

INSERT IGNORE INTO workspace_members (workspace_id, user_id, role, added_by, created_at, updated_at)
SELECT 'ws_default', u.user_id, 'admin', NULL,
       DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d %H:%i:%s'), DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d %H:%i:%s')
FROM users u
WHERE u.is_admin = 1;

INSERT IGNORE INTO workspace_members (workspace_id, user_id, role, added_by, created_at, updated_at)
SELECT DISTINCT 'ws_default', upr.user_id, 'member', NULL,
       DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d %H:%i:%s'), DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d %H:%i:%s')
FROM user_project_roles upr
WHERE upr.project_id <> '__SYSTEM__';

INSERT IGNORE INTO workspace_project_access (project_id, user_id, workspace_id, access, updated_by, updated_at)
SELECT upr.project_id, upr.user_id, 'ws_default',
       CASE upr.role_id WHEN 'role_admin' THEN 'admin' WHEN 'role_manager' THEN 'admin'
                        WHEN 'role_worker' THEN 'dev' ELSE 'review' END,
       NULL, DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d %H:%i:%s')
FROM user_project_roles upr
JOIN workspace_members m ON m.workspace_id = 'ws_default' AND m.user_id = upr.user_id
WHERE upr.project_id <> '__SYSTEM__'
  AND (CASE upr.role_id WHEN 'role_admin' THEN 'admin' WHEN 'role_manager' THEN 'admin'
                        WHEN 'role_worker' THEN 'dev' ELSE 'review' END)
      <> (CASE m.role WHEN 'owner' THEN 'admin' WHEN 'admin' THEN 'dev' ELSE 'none' END);
