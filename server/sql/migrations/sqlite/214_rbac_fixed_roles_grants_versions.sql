-- 214_rbac_fixed_roles_grants_versions.sql
-- flowgate.v02test.0090 card 3 (T0007, design 0004-D §2.3·2.4·3.3·7): one permission ID notation,
-- four fixed project roles, teams, permission versions and the one-time grant migration record.
--
--   permissions                 every ID the code checks, scope.target.action (project.* / workspace.* /
--                               system.*). Old perm_* rows stay for the migration's before-table and are
--                               never read by a judgment.
--   roles / role_permissions    role_admin / role_developer / role_reviewer / role_viewer and their
--                               project.* IDs. role_manager / role_worker remain only as aliases
--                               (read as role_admin / role_developer).
--   workspace_role_permissions  workspace member role (owner/admin/member) -> workspace.* IDs.
--                               workspace.project.create is further limited by the workspace policy.
--   teams / team_members / team_project_roles
--                               people grouped inside one workspace, and a team may hold one project role
--                               per project of that workspace.
--   users.authz_version         per-person permission version, raised in the same transaction as any
--                               change that alters what the person may do.
--   rbac_state.global_version   raised when a role -> permission mapping changes (all caches drop).
--   rbac_migrations             the one-time grant migration (rbac/migration_0090.py): status, digests of
--                               the before/after tables, the categorised difference rows, the error.
--                               A fresh install (no project grant, no exception, no non-admin workspace
--                               member) is recorded as completed here, because there is nothing to move.
--   user_project_roles_legacy_0090
--                               the user_project_roles rows as they were before the migration (kept as
--                               the ground for undoing it, never read by a judgment).
--   permissions_legacy_0090 / role_permissions_legacy_0090
--                               the permission registry and the role -> permission rows as they were
--                               before this file, copied first. The migration's table ② (old data, old
--                               rules) reads these, so a dotted ID an install had already registered and
--                               granted is told apart from one that was never registered.

-- ── Registry and role mapping before this file (design 7.3, table ②) ─────────
CREATE TABLE IF NOT EXISTS permissions_legacy_0090 (
    permission_id TEXT NOT NULL PRIMARY KEY
);

INSERT OR IGNORE INTO permissions_legacy_0090 (permission_id)
SELECT permission_id FROM permissions;

CREATE TABLE IF NOT EXISTS role_permissions_legacy_0090 (
    role_id TEXT NOT NULL,
    permission_id TEXT NOT NULL,
    PRIMARY KEY (role_id, permission_id)
);

INSERT OR IGNORE INTO role_permissions_legacy_0090 (role_id, permission_id)
SELECT role_id, permission_id FROM role_permissions;

-- ── Permission IDs (design 2.3) ───────────────────────────────────────────────
INSERT OR IGNORE INTO permissions (permission_id, permission_name, description, created_at)
VALUES
    ('project.view', 'project.view', '프로젝트·모듈·플로우 트리 보기', datetime('now')),
    ('project.document.read', 'project.document.read', '문서·대화·첨부 읽기', datetime('now')),
    ('project.document.create', 'project.document.create', '새 문서·대화 만들기', datetime('now')),
    ('project.document.update', 'project.document.update', '문서 고치기·대화 쓰기', datetime('now')),
    ('project.document.delete', 'project.document.delete', '남의 문서·초안 아닌 문서 지우기', datetime('now')),
    ('project.document.approve', 'project.document.approve', '승인·최종 승인·되돌리기', datetime('now')),
    ('project.document.reject', 'project.document.reject', '반려', datetime('now')),
    ('project.group.read', 'project.group.read', '플로우·타임라인 보기', datetime('now')),
    ('project.group.manage', 'project.group.manage', '플로우 만들기·고치기·보관·상태 전이', datetime('now')),
    ('project.run', 'project.run', 'AI 실행·Self-check·테스트 실행', datetime('now')),
    ('project.git.read', 'project.git.read', '브랜치·변경·diff 보기', datetime('now')),
    ('project.git.write', 'project.git.write', '작업 브랜치 커밋·작업 기준 바꾸기', datetime('now')),
    ('project.git.merge', 'project.git.merge', '기준 브랜치로 합치기·충돌 정리', datetime('now')),
    ('project.storage.write', 'project.storage.write', '소스 폴더·파일 만들기', datetime('now')),
    ('project.settings.read', 'project.settings.read', '프로젝트 설정 보기', datetime('now')),
    ('project.settings.edit', 'project.settings.edit', '프로젝트 설정 고치기·보관/복원', datetime('now')),
    ('project.document_type.manage', 'project.document_type.manage', '문서 종류 추가·수정·삭제', datetime('now')),
    ('project.numbering.manage', 'project.numbering.manage', '번호 체계 이관·검증', datetime('now')),
    ('project.member.read', 'project.member.read', '프로젝트 멤버 목록 보기', datetime('now')),
    ('project.member.manage', 'project.member.manage', '사람·팀에게 프로젝트 역할 주기·빼기', datetime('now')),
    ('workspace.view', 'workspace.view', '워크스페이스와 그 멤버·프로젝트 목록 보기', datetime('now')),
    ('workspace.member.manage', 'workspace.member.manage', '워크스페이스 멤버·기본 접근값·예외 관리', datetime('now')),
    ('workspace.team.manage', 'workspace.team.manage', '팀 만들기·지우기, 팀 멤버 넣기·빼기', datetime('now')),
    ('workspace.admin', 'workspace.admin', '워크스페이스 이름·끄기·켜기·지우기·AI 정책', datetime('now')),
    ('workspace.project.create', 'workspace.project.create', '워크스페이스에 프로젝트 만들기(정책 따름)', datetime('now')),
    ('system.settings.read', 'system.settings.read', '시스템 설정 보기', datetime('now')),
    ('system.settings.manage', 'system.settings.manage', '시스템 설정 고치기', datetime('now')),
    ('system.user.read', 'system.user.read', '사용자 보기', datetime('now')),
    ('system.user.create', 'system.user.create', '사용자 만들기', datetime('now')),
    ('system.user.update', 'system.user.update', '사용자 고치기', datetime('now')),
    ('system.user.disable', 'system.user.disable', '사용자 끄기·켜기', datetime('now')),
    ('system.user.assign_admin', 'system.user.assign_admin', '시스템 관리자 지정·해제', datetime('now')),
    ('system.workspace.create', 'system.workspace.create', '워크스페이스 만들기', datetime('now'));

-- ── Fixed project roles (role_admin / role_viewer already exist) ─────────────
INSERT OR IGNORE INTO roles (role_id, role_name, description, is_system, created_at, updated_at)
VALUES
    ('role_developer', '개발자', NULL, 1, datetime('now'), datetime('now')),
    ('role_reviewer', '리뷰어', NULL, 1, datetime('now'), datetime('now'));

-- ── Project role -> permission mapping (design 2.4, first table) ─────────────
INSERT OR IGNORE INTO role_permissions (role_id, permission_id)
VALUES
    ('role_admin', 'project.view'),
    ('role_admin', 'project.document.read'),
    ('role_admin', 'project.group.read'),
    ('role_admin', 'project.git.read'),
    ('role_admin', 'project.settings.read'),
    ('role_admin', 'project.member.read'),
    ('role_admin', 'project.document.create'),
    ('role_admin', 'project.document.update'),
    ('role_admin', 'project.run'),
    ('role_admin', 'project.git.write'),
    ('role_admin', 'project.storage.write'),
    ('role_admin', 'project.document.approve'),
    ('role_admin', 'project.document.reject'),
    ('role_admin', 'project.git.merge'),
    ('role_admin', 'project.document.delete'),
    ('role_admin', 'project.group.manage'),
    ('role_admin', 'project.settings.edit'),
    ('role_admin', 'project.document_type.manage'),
    ('role_admin', 'project.numbering.manage'),
    ('role_admin', 'project.member.manage');

INSERT OR IGNORE INTO role_permissions (role_id, permission_id)
VALUES
    ('role_developer', 'project.view'),
    ('role_developer', 'project.document.read'),
    ('role_developer', 'project.group.read'),
    ('role_developer', 'project.git.read'),
    ('role_developer', 'project.settings.read'),
    ('role_developer', 'project.member.read'),
    ('role_developer', 'project.document.create'),
    ('role_developer', 'project.document.update'),
    ('role_developer', 'project.run'),
    ('role_developer', 'project.git.write'),
    ('role_developer', 'project.storage.write');

INSERT OR IGNORE INTO role_permissions (role_id, permission_id)
VALUES
    ('role_reviewer', 'project.view'),
    ('role_reviewer', 'project.document.read'),
    ('role_reviewer', 'project.group.read'),
    ('role_reviewer', 'project.git.read'),
    ('role_reviewer', 'project.settings.read'),
    ('role_reviewer', 'project.member.read'),
    ('role_reviewer', 'project.document.approve'),
    ('role_reviewer', 'project.document.reject'),
    ('role_reviewer', 'project.git.merge');

INSERT OR IGNORE INTO role_permissions (role_id, permission_id)
VALUES
    ('role_viewer', 'project.view'),
    ('role_viewer', 'project.document.read'),
    ('role_viewer', 'project.group.read'),
    ('role_viewer', 'project.git.read'),
    ('role_viewer', 'project.settings.read'),
    ('role_viewer', 'project.member.read');

-- ── Workspace member role -> permission mapping (design 2.4, second table) ───
CREATE TABLE IF NOT EXISTS workspace_role_permissions (
    workspace_role TEXT NOT NULL CHECK (workspace_role IN ('owner', 'admin', 'member')),
    permission_id TEXT NOT NULL REFERENCES permissions(permission_id) ON DELETE CASCADE,
    PRIMARY KEY (workspace_role, permission_id)
);

INSERT OR IGNORE INTO workspace_role_permissions (workspace_role, permission_id)
VALUES
    ('owner', 'workspace.view'),
    ('owner', 'workspace.member.manage'),
    ('owner', 'workspace.team.manage'),
    ('owner', 'workspace.admin'),
    ('owner', 'workspace.project.create');

INSERT OR IGNORE INTO workspace_role_permissions (workspace_role, permission_id)
VALUES
    ('admin', 'workspace.view'),
    ('admin', 'workspace.member.manage'),
    ('admin', 'workspace.team.manage'),
    ('admin', 'workspace.project.create');

INSERT OR IGNORE INTO workspace_role_permissions (workspace_role, permission_id)
VALUES
    ('member', 'workspace.view'),
    ('member', 'workspace.project.create');

-- ── Teams (query 643: teams exist) ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS teams (
    team_id TEXT NOT NULL PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_by TEXT NULL REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, name)
);

CREATE TABLE IF NOT EXISTS team_members (
    team_id TEXT NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    added_by TEXT NULL REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (team_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_team_members_user ON team_members(user_id);

CREATE TABLE IF NOT EXISTS team_project_roles (
    team_id TEXT NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    role_id TEXT NOT NULL REFERENCES roles(role_id) ON DELETE CASCADE,
    granted_by TEXT NULL REFERENCES users(user_id) ON DELETE SET NULL,
    granted_at TEXT NOT NULL,
    PRIMARY KEY (team_id, project_id)
);

CREATE INDEX IF NOT EXISTS idx_team_project_roles_project ON team_project_roles(project_id);

-- ── Permission versions (design 3.3) ─────────────────────────────────────────
ALTER TABLE users ADD COLUMN authz_version INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS rbac_state (
    state_key TEXT NOT NULL PRIMARY KEY,
    global_version INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO rbac_state (state_key, global_version, updated_at)
VALUES ('global', 0, datetime('now'));

-- ── One-time grant migration record and the kept copy of the old rows (design 7) ──
CREATE TABLE IF NOT EXISTS rbac_migrations (
    migration_id TEXT NOT NULL PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    attempt INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NULL,
    finished_at TEXT NULL,
    before_digest TEXT NULL,
    after_digest TEXT NULL,
    summary TEXT NULL,
    diff TEXT NULL,
    error TEXT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_project_roles_legacy_0090 (
    user_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    role_id TEXT NOT NULL,
    granted_at TEXT NULL,
    granted_by TEXT NULL,
    copied_at TEXT NOT NULL,
    PRIMARY KEY (user_id, project_id)
);

-- A fresh install has nothing to move: record the migration as completed right away. An existing
-- install keeps no row here, and the server refuses non-administrator requests until the startup
-- migration has recorded 'completed' (design 7.1).
INSERT OR IGNORE INTO rbac_migrations (migration_id, status, attempt, started_at, finished_at, summary, updated_at)
SELECT 'rbac_0090', 'completed', 0, datetime('now'), datetime('now'), '{"fresh_install": true}', datetime('now')
WHERE NOT EXISTS (SELECT 1 FROM user_project_roles WHERE project_id <> '__SYSTEM__')
  AND NOT EXISTS (SELECT 1 FROM workspace_project_access)
  AND NOT EXISTS (SELECT 1 FROM workspace_members m JOIN users u ON u.user_id = m.user_id
                  WHERE u.is_admin = 0);
