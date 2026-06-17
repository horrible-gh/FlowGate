-- 004_rbac.sql
-- RBAC seed data: __SYSTEM__ project, system roles, permissions, role-permission mappings
-- Idempotency guaranteed: uses INSERT OR IGNORE
-- Applies to: FlowGate Phase B (T066)

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- ── __SYSTEM__ project ───────────────────────────────────────────────────────
INSERT OR IGNORE INTO projects (project_id, project_name, is_active, created_at, updated_at)
VALUES ('__SYSTEM__', '[시스템]', 1, datetime('now'), datetime('now'));

-- ── System roles ─────────────────────────────────────────────────────────────
INSERT OR IGNORE INTO roles (role_id, role_name, is_system, created_at, updated_at)
VALUES
    ('role_admin',   '관리자', 1, datetime('now'), datetime('now')),
    ('role_manager', '매니저', 1, datetime('now'), datetime('now')),
    ('role_worker',  '작업자', 1, datetime('now'), datetime('now')),
    ('role_viewer',  '뷰어',   1, datetime('now'), datetime('now'));

-- ── Permission list ──────────────────────────────────────────────────────────
INSERT OR IGNORE INTO permissions (permission_id, permission_name, description, created_at)
VALUES
    ('perm_document_create',         'perm_document_create',        '문서 생성',             datetime('now')),
    ('perm_document_read',           'perm_document_read',          '문서 조회',             datetime('now')),
    ('perm_document_update',         'perm_document_update',        '문서 수정',             datetime('now')),
    ('perm_document_delete',         'perm_document_delete',        '문서 삭제',             datetime('now')),
    ('perm_role_assign',             'perm_role_assign',            '역할 부여',             datetime('now')),
    ('perm_role_revoke',             'perm_role_revoke',            '역할 회수',             datetime('now')),
    ('perm_user_read',               'perm_user_read',              '사용자 조회',           datetime('now')),
    ('perm_project_settings_read',   'perm_project_settings_read',  '프로젝트 설정 조회',   datetime('now')),
    ('perm_project_settings_write',  'perm_project_settings_write', '프로젝트 설정 변경',   datetime('now')),
    ('perm_system_settings_read',    'perm_system_settings_read',   '시스템 설정 조회',     datetime('now')),
    ('perm_system_settings_write',   'perm_system_settings_write',  '시스템 설정 변경',     datetime('now'));

-- ── Role-permission mappings ─────────────────────────────────────────────────
-- role_admin: all permissions
INSERT OR IGNORE INTO role_permissions (role_id, permission_id)
VALUES
    ('role_admin', 'perm_document_create'),
    ('role_admin', 'perm_document_read'),
    ('role_admin', 'perm_document_update'),
    ('role_admin', 'perm_document_delete'),
    ('role_admin', 'perm_role_assign'),
    ('role_admin', 'perm_role_revoke'),
    ('role_admin', 'perm_user_read'),
    ('role_admin', 'perm_project_settings_read'),
    ('role_admin', 'perm_project_settings_write'),
    ('role_admin', 'perm_system_settings_read'),
    ('role_admin', 'perm_system_settings_write');

-- role_manager: document CRUD + user lookup + project settings (excluding role management)
INSERT OR IGNORE INTO role_permissions (role_id, permission_id)
VALUES
    ('role_manager', 'perm_document_create'),
    ('role_manager', 'perm_document_read'),
    ('role_manager', 'perm_document_update'),
    ('role_manager', 'perm_document_delete'),
    ('role_manager', 'perm_user_read'),
    ('role_manager', 'perm_project_settings_read'),
    ('role_manager', 'perm_project_settings_write');

-- role_worker: document create/read/update (excluding delete)
INSERT OR IGNORE INTO role_permissions (role_id, permission_id)
VALUES
    ('role_worker', 'perm_document_create'),
    ('role_worker', 'perm_document_read'),
    ('role_worker', 'perm_document_update');

-- role_viewer: document lookup + project settings lookup
INSERT OR IGNORE INTO role_permissions (role_id, permission_id)
VALUES
    ('role_viewer', 'perm_document_read'),
    ('role_viewer', 'perm_project_settings_read');

COMMIT;
