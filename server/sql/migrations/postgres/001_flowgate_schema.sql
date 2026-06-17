-- ═══════════════════════════════════════════════════
-- 1. Independent tables
-- ═══════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS users (
    user_id     TEXT PRIMARY KEY,
    username    TEXT NOT NULL UNIQUE,
    email       TEXT NOT NULL UNIQUE,
    password    TEXT NOT NULL,
    totp_secret TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
    is_admin    INTEGER NOT NULL DEFAULT 0,
    first_login_required INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username ON users(username);
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email    ON users(email);
CREATE INDEX        IF NOT EXISTS idx_users_active  ON users(is_active);
CREATE TABLE IF NOT EXISTS roles (
    role_id     TEXT PRIMARY KEY,
    role_name   TEXT NOT NULL UNIQUE,
    description TEXT,
    is_system   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_roles_name ON roles(role_name);
CREATE TABLE IF NOT EXISTS permissions (
    permission_id   TEXT PRIMARY KEY,
    permission_name TEXT NOT NULL UNIQUE,
    description     TEXT,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    project_id   TEXT PRIMARY KEY,
    project_name TEXT NOT NULL,
    description  TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_active ON projects(is_active);
CREATE TABLE IF NOT EXISTS system_settings (
    setting_key   TEXT PRIMARY KEY,
    setting_value TEXT,
    value_type    TEXT NOT NULL DEFAULT 'string'
                      CHECK (value_type IN ('string', 'integer', 'boolean', 'json')),
    description   TEXT,
    updated_at    TEXT NOT NULL,
    updated_by    TEXT REFERENCES users(user_id)
);
CREATE TABLE IF NOT EXISTS document_types (
    id           SERIAL PRIMARY KEY,
    project_id   TEXT REFERENCES projects(project_id) ON DELETE CASCADE,
    type_code    TEXT NOT NULL,
    type_name    TEXT NOT NULL,
    series       TEXT NOT NULL
                     CHECK (series IN ('requirements', 'instruction', 'design', 'work', 'action')),
    is_system    INTEGER NOT NULL DEFAULT 0,
    is_active    INTEGER NOT NULL DEFAULT 1,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_doc_types_project  ON document_types(project_id);
CREATE INDEX IF NOT EXISTS idx_doc_types_series   ON document_types(series);
CREATE INDEX IF NOT EXISTS idx_doc_types_active   ON document_types(is_active);
CREATE UNIQUE INDEX IF NOT EXISTS ux_doc_types_global
    ON document_types(series, type_code)
    WHERE project_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_doc_types_project
    ON document_types(project_id, series, type_code)
    WHERE project_id IS NOT NULL;
-- ═══════════════════════════════════════════════════
-- 2. Simple FK tables
-- ═══════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id       TEXT NOT NULL REFERENCES roles(role_id) ON DELETE CASCADE,
    permission_id TEXT NOT NULL REFERENCES permissions(permission_id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);
CREATE INDEX IF NOT EXISTS idx_role_permissions_permission ON role_permissions(permission_id);
CREATE TABLE IF NOT EXISTS project_settings (
    project_id          TEXT PRIMARY KEY REFERENCES projects(project_id) ON DELETE CASCADE,
    group_structure     INTEGER NOT NULL DEFAULT 2
                            CHECK (group_structure IN (1, 2, 3, 4)),
    digits_group        INTEGER NOT NULL DEFAULT 4,
    digits_sub_group    INTEGER NOT NULL DEFAULT 3,
    digits_type         INTEGER NOT NULL DEFAULT 4,
    storage_root_override TEXT,
    updated_at          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS groups (
    group_id    TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(project_id),
    module      TEXT NOT NULL DEFAULT '__ALL__',
    parent_id   TEXT REFERENCES groups(group_id),
    title       TEXT NOT NULL,
    priority    TEXT,
    status      TEXT NOT NULL DEFAULT 'OPEN'
                    CHECK (status IN ('OPEN', 'CLOSED', 'CANCELLED')),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    closed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_groups_project_module ON groups(project_id, module);
CREATE INDEX IF NOT EXISTS idx_groups_parent         ON groups(parent_id);
CREATE INDEX IF NOT EXISTS idx_groups_status         ON groups(status);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    jti         TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    issued_at   TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    revoked_at  TEXT,
    replaced_by TEXT REFERENCES refresh_tokens(jti)
);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user    ON refresh_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires ON refresh_tokens(expires_at);
CREATE TABLE IF NOT EXISTS token_blacklist (
    jti         TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    revoked_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_token_blacklist_user    ON token_blacklist(user_id);
CREATE INDEX IF NOT EXISTS idx_token_blacklist_expires ON token_blacklist(expires_at);
CREATE TABLE IF NOT EXISTS id_counter (
    project_id    TEXT NOT NULL,
    module        TEXT NOT NULL DEFAULT '__ALL__',
    group_seq     TEXT NOT NULL DEFAULT '',
    sub_group_seq TEXT NOT NULL DEFAULT '',
    series        TEXT NOT NULL DEFAULT ''
                      CHECK (series IN ('', 'requirements', 'instruction', 'design', 'work', 'action')),
    type_code     TEXT NOT NULL DEFAULT '',
    last_seq      INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (project_id, module, group_seq, sub_group_seq, series, type_code)
);
-- ═══════════════════════════════════════════════════
-- 3. Two-stage FK tables
-- ═══════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS sub_groups (
    sub_group_id TEXT PRIMARY KEY,
    group_id     TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'OPEN'
                     CHECK (status IN ('OPEN', 'CLOSED')),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sub_groups_group ON sub_groups(group_id);
CREATE TABLE IF NOT EXISTS user_project_roles (
    user_id    TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    role_id    TEXT NOT NULL REFERENCES roles(role_id) ON DELETE CASCADE,
    granted_at TEXT NOT NULL,
    granted_by TEXT REFERENCES users(user_id),
    PRIMARY KEY (user_id, project_id)
);
CREATE INDEX IF NOT EXISTS idx_upr_role              ON user_project_roles(role_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_upr_user_project ON user_project_roles(user_id, project_id);
CREATE TABLE IF NOT EXISTS document_type_templates (
    id              SERIAL PRIMARY KEY,
    project_id      TEXT REFERENCES projects(project_id) ON DELETE CASCADE,
    type_code       TEXT NOT NULL,
    template_path   TEXT NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1,
    uploaded_by     TEXT REFERENCES users(user_id),
    uploaded_at     TEXT NOT NULL,
    UNIQUE (project_id, type_code)
);
CREATE INDEX IF NOT EXISTS idx_dtt_project_type ON document_type_templates(project_id, type_code);
-- ═══════════════════════════════════════════════════
-- 4. Documents/events (final)
-- ═══════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS documents (
    id           SERIAL PRIMARY KEY,
    doc_id       TEXT    NOT NULL UNIQUE,
    project_id   TEXT    NOT NULL REFERENCES projects(project_id),
    module       TEXT    NOT NULL DEFAULT '__ALL__',
    group_id     TEXT    REFERENCES groups(group_id),
    sub_group_id TEXT    REFERENCES sub_groups(sub_group_id),
    type_code    TEXT    NOT NULL,
    seq          INTEGER NOT NULL,
    title        TEXT    NOT NULL,
    file_path    TEXT,
    status       TEXT    NOT NULL DEFAULT 'draft'
                     CHECK (status IN (
                         'draft','open','in_review','approved','rejected',
                         'cancelled','closed','archived','answered'
                     )),
    owner_id     TEXT    REFERENCES users(user_id),
    priority     TEXT,
    due_date     TEXT,
    direction    TEXT,
    review_required INTEGER NOT NULL DEFAULT 0,
    tv_type      TEXT,
    pass_criteria TEXT   DEFAULT 'all',
    worker_tier  TEXT,
    target_id    TEXT,
    triggered_by TEXT,
    superseded_by TEXT,
    previous_tv  TEXT,
    previous_t   TEXT,
    previous_ds  TEXT,
    created_at   TEXT    NOT NULL,
    meta         TEXT,
    updated_at   TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_doc_id               ON documents(doc_id);
CREATE INDEX        IF NOT EXISTS idx_documents_prj_mod_grp_status  ON documents(project_id, module, group_id, status);
CREATE INDEX        IF NOT EXISTS idx_documents_prj_type_status     ON documents(project_id, type_code, status);
CREATE INDEX        IF NOT EXISTS idx_documents_owner               ON documents(owner_id);
CREATE INDEX        IF NOT EXISTS idx_documents_sub_group           ON documents(sub_group_id);
CREATE INDEX        IF NOT EXISTS idx_documents_target              ON documents(target_id, type_code);
CREATE INDEX        IF NOT EXISTS idx_documents_updated             ON documents(updated_at DESC);
CREATE TABLE IF NOT EXISTS events (
    event_id         SERIAL PRIMARY KEY,
    doc_id           TEXT    NOT NULL REFERENCES documents(doc_id),
    event_type       TEXT    NOT NULL,
    actor_user_id    TEXT    REFERENCES users(user_id),
    memo_file        TEXT,
    file_hash        TEXT,
    reason           TEXT,
    related_doc_id   TEXT    REFERENCES documents(doc_id),
    related_target_id TEXT,
    note             TEXT,
    created_at       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_doc_created ON events(doc_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_actor       ON events(actor_user_id);
CREATE INDEX IF NOT EXISTS idx_events_type        ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_created     ON events(created_at);
CREATE TABLE IF NOT EXISTS tv_scenarios (
    id              SERIAL PRIMARY KEY,
    tv_doc_id       TEXT    NOT NULL REFERENCES documents(doc_id),
    scenario_idx    INTEGER NOT NULL,
    source          TEXT    NOT NULL DEFAULT 'worker',
    title           TEXT    NOT NULL,
    result          TEXT,
    note            TEXT,
    disabled        INTEGER NOT NULL DEFAULT 0,
    disabled_reason TEXT,
    meta            TEXT,
    updated_at      TEXT    NOT NULL,
    UNIQUE (tv_doc_id, scenario_idx)
);
CREATE INDEX IF NOT EXISTS idx_tv_scenarios_tv_doc ON tv_scenarios(tv_doc_id);
CREATE TABLE IF NOT EXISTS workflow_events (
    id              SERIAL PRIMARY KEY,
    event_type      TEXT    NOT NULL,
    project_id      TEXT    NOT NULL REFERENCES projects(project_id),
    group_id        TEXT    REFERENCES groups(group_id),
    document_id     INTEGER REFERENCES documents(id),
    actor_user_id   TEXT    NOT NULL REFERENCES users(user_id),
    from_state      TEXT,
    to_state        TEXT,
    metadata        TEXT,
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_workflow_events_project_created  ON workflow_events(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_events_group_created    ON workflow_events(group_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_events_document_created ON workflow_events(document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_events_type_created     ON workflow_events(event_type, created_at DESC);
CREATE TABLE IF NOT EXISTS numbering_jobs (
    id              SERIAL PRIMARY KEY,
    project_id      TEXT    NOT NULL REFERENCES projects(project_id),
    requested_by    TEXT    NOT NULL REFERENCES users(user_id),
    target          TEXT    NOT NULL,
    from_width      INTEGER NOT NULL,
    to_width        INTEGER NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    affected_count  INTEGER,
    error_message   TEXT,
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at      TEXT,
    finished_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_numbering_jobs_project_status ON numbering_jobs(project_id, status);
-- ═══════════════════════════════════════════════════
-- VIEW definitions
-- ═══════════════════════════════════════════════════

CREATE OR REPLACE VIEW v_tv_progress AS
SELECT
    d.doc_id,
    d.project_id,
    d.module,
    d.group_id,
    d.title,
    d.status,
    d.tv_type,
    d.pass_criteria,
    d.worker_tier,
    d.meta::jsonb ->> 'tv_clear_scope'     AS tv_clear_scope,
    d.meta::jsonb ->> 'tv_progress'        AS tv_progress_note,
    d.meta::jsonb ->> 'tv_baseline_doc_id' AS tv_baseline_doc_id,
    d.meta::jsonb ->> 'tv_re_run_reason'   AS tv_re_run_reason,
    COUNT(ts.id)                                  AS scenario_total,
    SUM(CASE WHEN ts.disabled = 0 THEN 1 ELSE 0 END)
                                                  AS scenario_active,
    SUM(CASE WHEN ts.result IS NOT NULL AND ts.disabled = 0 THEN 1 ELSE 0 END)
                                                  AS scenario_done,
    SUM(CASE WHEN ts.result = 'pass' AND ts.disabled = 0 THEN 1 ELSE 0 END)
                                                  AS scenario_pass,
    SUM(CASE WHEN ts.result = 'fail' AND ts.disabled = 0 THEN 1 ELSE 0 END)
                                                  AS scenario_fail,
    d.owner_id,
    d.created_at,
    d.updated_at
FROM documents d
LEFT JOIN tv_scenarios ts ON ts.tv_doc_id = d.doc_id
WHERE d.tv_type IS NOT NULL
GROUP BY d.id;
CREATE OR REPLACE VIEW v_tv_open AS
SELECT *
FROM v_tv_progress
WHERE status NOT IN ('approved', 'closed', 'archived');
-- ═══════════════════════════════════════════════════
-- Seed data
-- ═══════════════════════════════════════════════════

-- Four roles (system-reserved)
DO $fg_or_ignore$
BEGIN
INSERT INTO roles (role_id, role_name, description, is_system, created_at, updated_at) VALUES
    ('role_admin',    '관리자', '시스템 전체 관리',   1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('role_manager',  '매니저', '프로젝트 관리',       1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('role_worker',   '작업자', '일반 작업',           1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('role_viewer',   '뷰어',   '읽기 전용',           1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
-- 27 permissions
DO $fg_or_ignore$
BEGIN
INSERT INTO permissions (permission_id, permission_name, created_at) VALUES
    ('perm_project_create',         '프로젝트 생성',         CURRENT_TIMESTAMP),
    ('perm_project_read',           '프로젝트 조회',         CURRENT_TIMESTAMP),
    ('perm_project_update',         '프로젝트 수정',         CURRENT_TIMESTAMP),
    ('perm_project_delete',         '프로젝트 삭제',         CURRENT_TIMESTAMP),
    ('perm_user_create',            '사용자 생성',           CURRENT_TIMESTAMP),
    ('perm_user_read',              '사용자 조회',           CURRENT_TIMESTAMP),
    ('perm_user_update',            '사용자 수정',           CURRENT_TIMESTAMP),
    ('perm_user_delete',            '사용자 삭제',           CURRENT_TIMESTAMP),
    ('perm_role_assign',            '역할 부여',             CURRENT_TIMESTAMP),
    ('perm_role_revoke',            '역할 회수',             CURRENT_TIMESTAMP),
    ('perm_document_create',        '문서 생성',             CURRENT_TIMESTAMP),
    ('perm_document_read',          '문서 조회',             CURRENT_TIMESTAMP),
    ('perm_document_update',        '문서 수정',             CURRENT_TIMESTAMP),
    ('perm_document_delete',        '문서 삭제',             CURRENT_TIMESTAMP),
    ('perm_document_approve',       '문서 승인',             CURRENT_TIMESTAMP),
    ('perm_document_reject',        '문서 반려',             CURRENT_TIMESTAMP),
    ('perm_group_create',           '그룹 생성',             CURRENT_TIMESTAMP),
    ('perm_group_read',             '그룹 조회',             CURRENT_TIMESTAMP),
    ('perm_group_update',           '그룹 수정',             CURRENT_TIMESTAMP),
    ('perm_group_close',            '그룹 종료',             CURRENT_TIMESTAMP),
    ('perm_settings_read',          '설정 조회',             CURRENT_TIMESTAMP),
    ('perm_settings_update',        '설정 수정',             CURRENT_TIMESTAMP),
    ('perm_tv_run',                 'TV/TVR 실행',           CURRENT_TIMESTAMP),
    ('perm_tv_approve',             'TV/TVR 승인',           CURRENT_TIMESTAMP),
    ('perm_numbering_manage',       '채번 관리',             CURRENT_TIMESTAMP),
    ('perm_document_type_manage',   '문서 타입 관리',         CURRENT_TIMESTAMP),
    ('perm_storage_manage',         '스토리지 관리',         CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
-- role_permissions (admin=all, manager=project-management scope, worker=basic work, viewer=read)
DO $fg_or_ignore$
BEGIN
INSERT INTO role_permissions (role_id, permission_id)
SELECT 'role_admin', permission_id FROM permissions ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO role_permissions (role_id, permission_id) VALUES
    ('role_manager', 'perm_project_read'),
    ('role_manager', 'perm_user_read'),
    ('role_manager', 'perm_role_assign'),
    ('role_manager', 'perm_role_revoke'),
    ('role_manager', 'perm_document_create'),
    ('role_manager', 'perm_document_read'),
    ('role_manager', 'perm_document_update'),
    ('role_manager', 'perm_document_delete'),
    ('role_manager', 'perm_document_approve'),
    ('role_manager', 'perm_document_reject'),
    ('role_manager', 'perm_group_create'),
    ('role_manager', 'perm_group_read'),
    ('role_manager', 'perm_group_update'),
    ('role_manager', 'perm_group_close'),
    ('role_manager', 'perm_settings_read'),
    ('role_manager', 'perm_tv_run'),
    ('role_manager', 'perm_tv_approve'),
    ('role_manager', 'perm_numbering_manage'),
    ('role_manager', 'perm_document_type_manage') ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO role_permissions (role_id, permission_id) VALUES
    ('role_worker', 'perm_project_read'),
    ('role_worker', 'perm_document_create'),
    ('role_worker', 'perm_document_read'),
    ('role_worker', 'perm_document_update'),
    ('role_worker', 'perm_group_read'),
    ('role_worker', 'perm_settings_read'),
    ('role_worker', 'perm_tv_run') ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO role_permissions (role_id, permission_id) VALUES
    ('role_viewer', 'perm_project_read'),
    ('role_viewer', 'perm_document_read'),
    ('role_viewer', 'perm_group_read'),
    ('role_viewer', 'perm_settings_read') ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
-- 21 document types (global defaults, project_id=NULL, is_system=1)
DO $fg_or_ignore$
BEGIN
INSERT INTO document_types (project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) VALUES
    (NULL, 'R',  '요건정의',             'requirements', 1, 1, 10,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'M',  '메모',                 'requirements', 1, 1, 20,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'Q',  '질의',                 'requirements', 1, 1, 30,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'A',  '응답',                 'requirements', 1, 1, 40,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'L',  '로그',                 'requirements', 1, 1, 50,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'B',  '버그',                 'requirements', 1, 1, 60,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'DS', '설계지시',             'instruction',  1, 1, 10,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'N',  '조사지시',             'instruction',  1, 1, 20,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'T',  '작업지시',             'instruction',  1, 1, 30,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'TS', '테스트시나리오지시',   'instruction',  1, 1, 40,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'D',  '기본설계',             'design',       1, 1, 10,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'P',  '프로토콜',             'design',       1, 1, 20,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'L',  '로직',                 'design',       1, 1, 30,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'DB', '데이터베이스',         'design',       1, 1, 40,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'NR', '조사레포트',           'work',         1, 1, 10,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'TR', '작업레포트',           'work',         1, 1, 20,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'TSR','테스트레포트',         'work',         1, 1, 30,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'V',  '리뷰의뢰',             'work',         1, 1, 40,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'C',  '커밋',                 'work',         1, 1, 50,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'AC', '승인',                 'action',       1, 1, 10,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    (NULL, 'RJ', '반려',                 'action',       1, 1, 20,  CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
-- Default system_settings values
DO $fg_or_ignore$
BEGIN
INSERT INTO system_settings (setting_key, setting_value, value_type, description, updated_at) VALUES
    ('storage_root',              '',    'string',  '스토리지 기본 루트 경로',          CURRENT_TIMESTAMP),
    ('log_retention_days',        '90',  'integer', '로그 보존 기간(일)',               CURRENT_TIMESTAMP),
    ('log_level',                 'INFO','string',  '로그 출력 레벨',                   CURRENT_TIMESTAMP),
    ('jwt_expiry_minutes',        '30',  'integer', 'JWT 액세스 토큰 만료(분)',         CURRENT_TIMESTAMP),
    ('refresh_token_expiry_days', '7',   'integer', '리프레시 토큰 만료(일)',           CURRENT_TIMESTAMP),
    ('support_email',             '',    'string',  '지원 문의 이메일 주소',            CURRENT_TIMESTAMP),
    ('jwt_expire_min',            '30',  'integer', 'JWT 액세스 토큰 만료(분) 별칭',   CURRENT_TIMESTAMP),
    ('refresh_expire_days',       '14',  'integer', '리프레시 토큰 만료(일) 별칭',     CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;