"""T058: pytest tests for the settings API.

Using TESTING=1 mode with an in-memory SQLite DB.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))


@pytest.fixture(scope="module")
def test_db_path(migrated_sqlite_db):
    """flowgate.default.0394 T0010 (NR0003 §13-6 / §7.2): shared conftest.py factory —
    applies every migration so the schema always matches current product (e.g.
    project_settings' `branch` column), the same guarantee the old per-file loop over
    every migration gave, without a second copy of the loop to keep in sync."""
    return migrated_sqlite_db(
        "test_settings.db",
        seed_sql="""
        INSERT OR IGNORE INTO roles(role_id,role_name,is_system,created_at,updated_at)
            VALUES('role_admin','Administrator',1,datetime('now'),datetime('now')),
                  ('role_manager','Manager',1,datetime('now'),datetime('now'));
        INSERT OR IGNORE INTO permissions(permission_id,permission_name,created_at)
            VALUES
                ('system.settings.manage','System settings management',datetime('now')),
                ('system.user.read','User read',datetime('now')),
                ('system.user.create','User create',datetime('now')),
                ('system.user.update','User update',datetime('now')),
                ('system.user.delete','User delete',datetime('now')),
                ('system.user.assign_role','Assign role',datetime('now')),
                ('project.settings.read','Project settings read',datetime('now')),
                ('project.settings.edit','Project settings edit',datetime('now')),
                ('project.document_type.create','Document type create',datetime('now')),
                ('project.document_type.update','Document type update',datetime('now')),
                ('project.document_type.delete','Document type delete',datetime('now'));
        INSERT OR IGNORE INTO role_permissions(role_id,permission_id)
            VALUES
                ('role_admin','system.settings.manage'),
                ('role_admin','system.user.read'),
                ('role_admin','system.user.create'),
                ('role_admin','system.user.update'),
                ('role_admin','system.user.delete'),
                ('role_admin','system.user.assign_role'),
                ('role_admin','project.settings.read'),
                ('role_admin','project.settings.edit'),
                ('role_admin','project.document_type.create'),
                ('role_admin','project.document_type.update'),
                ('role_admin','project.document_type.delete'),
                ('role_manager','project.settings.read'),
                ('role_manager','project.settings.edit'),
                ('role_manager','project.document_type.create'),
                ('role_manager','project.document_type.update'),
                ('role_manager','project.document_type.delete');
        INSERT OR IGNORE INTO projects(project_id,project_name,is_active,created_at,updated_at)
            VALUES('__SYSTEM__','[System]',1,datetime('now'),datetime('now')),
                  ('proj_001','TestProject',1,datetime('now'),datetime('now'));
        INSERT OR IGNORE INTO users(user_id,username,email,password,is_active,is_admin,first_login_required,created_at,updated_at)
            VALUES('usr_admin','admin','admin@test.com','hashed_pw',1,1,0,datetime('now'),datetime('now')),
                  ('usr_worker','worker','worker@test.com','hashed_pw',1,0,0,datetime('now'),datetime('now')),
                  ('usr_manager','manager','manager@test.com','hashed_pw',1,0,0,datetime('now'),datetime('now'));
        INSERT OR IGNORE INTO user_project_roles(user_id,project_id,role_id,granted_at)
            VALUES('usr_admin','__SYSTEM__','role_admin',datetime('now')),
                  ('usr_manager','proj_001','role_manager',datetime('now'));
        INSERT OR IGNORE INTO system_settings(setting_key,setting_value,value_type,updated_at)
            VALUES('storage_root','/data/flowgate','string',datetime('now'));
        """,
    )


@pytest.fixture(autouse=True)
def mock_db(test_db_path):
    from modules.flow_gate.db.connection import FlowGateStore, now_iso  # noqa: F401

    class TestStore:
        def __init__(self, db_path):
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")

        def _execute(self, sql, params=None):
            self._conn.execute(sql, params or [])
            self._conn.commit()

        def _fetch_one(self, sql, params=None):
            cur = self._conn.execute(sql, params or [])
            row = cur.fetchone()
            return dict(row) if row else None

        def _fetch_all(self, sql, params=None):
            cur = self._conn.execute(sql, params or [])
            return [dict(r) for r in cur.fetchall()]

        def table_exists(self, table_name):
            row = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                [table_name],
            ).fetchone()
            return row is not None

    store = TestStore(test_db_path)
    # Each db/rbac module does `from .connection import get_store`, so the name must be
    # overridden in every importing namespace. The previous nested `with patch(...)`
    # form did NOT restore reliably here (the shared underlying function object made
    # mock's per-target restore leak the patched stub into later test modules, e.g.
    # test_inbox). Capture the canonical original once and restore every binding to it
    # explicitly so this fixture is leak-free regardless of test ordering.
    import importlib

    import modules.flow_gate.db.connection as _conn
    _real_get_store = _conn.get_store
    _modules = [
        importlib.import_module(_name)
        for _name in (
            "modules.flow_gate.db.connection",
            "modules.flow_gate.db.system_settings",
            "modules.flow_gate.db.users",
            "modules.flow_gate.db.projects",
            "modules.flow_gate.db.templates",
            "modules.flow_gate.db.numbering_jobs",
            "modules.flow_gate.db.totp_backup_codes",
            "modules.flow_gate.rbac.decorators",
            # rbac.decorators._has_permission delegates to permission_service
            # (0276 T0009), so its bound get_store needs the same treatment.
            "modules.flow_gate.rbac.permission_service",
            # Settings services do `from ..db.connection import get_store` at import
            # time, so their bound `get_store` is whatever the name pointed at when the
            # module was first imported. In a full-suite run these modules are imported
            # early (before this fixture patches), binding the real get_store -> global
            # STORE (which has _db=None under TESTING=1). Patch them here too so the test
            # store is used regardless of import order.
            "modules.flow_gate.settings.user_admin_service",
            "modules.flow_gate.settings.project_settings_service",
        )
    ]
    for _m in _modules:
        _m.get_store = lambda store=store: store
    # permission_service memoises resolved permission sets in a module-level dict.
    # Clear it around each test so entries resolved against a previous test's
    # store cannot leak into this one (0276 T0009).
    from modules.flow_gate.rbac import permission_service as _perm_svc
    _perm_svc.invalidate_all()
    try:
        yield store
    finally:
        _perm_svc.invalidate_all()
        for _m in _modules:
            _m.get_store = _real_get_store


class TestSystemSettingsService:
    def test_get_all_returns_list(self):
        from modules.flow_gate.settings.system_settings_service import get_all

        result = get_all()
        assert isinstance(result, list)

    def test_set_values_allowlist_ok(self):
        from modules.flow_gate.settings.system_settings_service import get_one, set_values

        set_values({"log_level": "DEBUG"}, updated_by="usr_admin")
        row = get_one("log_level")
        assert row is not None
        assert row["setting_value"] == "DEBUG"

    def test_set_values_allowlist_rejected(self):
        from modules.flow_gate.settings.system_settings_service import set_values

        with pytest.raises(ValueError, match="Disallowed setting key"):
            set_values({"unknown_key": "bad"})

    def test_set_values_multiple(self):
        from modules.flow_gate.settings.system_settings_service import get_one, set_values

        set_values({"log_retention_days": "30", "log_level": "INFO"})
        assert get_one("log_retention_days")["setting_value"] == "30"
        assert get_one("log_level")["setting_value"] == "INFO"

    def test_storage_root_empty_uses_default(self, monkeypatch):
        """빈 storage_root 는 기본 경로로 대체된다.

        0394 T0004: 이 케이스는 FLOWGATE_STORAGE_DIR 이 **없어야** 성립한다
        (`get_storage_root` 의 1순위가 그 변수다). 그런데 스스로 지운 적이 없고, 앞서
        실행된 다른 모듈이 정리하면서 그 변수를 지워 준 덕에 통과하고 있었다 — 그
        누출을 막자 이 케이스가 빨간불이 됐다. NR0003 §5.1 이 말한 "옆 테스트 덕에
        우연히 통과" 의 실물이라, 전제를 자기 손으로 만들도록 고친다. monkeypatch 는
        테스트가 끝나면 원래 값을 되돌려 준다.
        """
        from modules.flow_gate.settings.system_settings_service import get_one, set_values

        monkeypatch.delenv("FLOWGATE_STORAGE_DIR", raising=False)

        try:
            set_values({"storage_root": ""})
            row = get_one("storage_root")
            assert row is not None
            assert row["setting_value"] == str(Path.cwd() / "storage")
        finally:
            set_values({"storage_root": "/data/flowgate"})


class TestUserAdminService:
    def test_list_users_admin(self):
        from modules.flow_gate.settings.user_admin_service import list_users_for_admin

        result = list_users_for_admin()
        assert result["total"] >= 1
        assert all("password" not in u for u in result["items"])

    def test_list_users_manager_scope(self):
        from modules.flow_gate.settings.user_admin_service import list_users_for_manager

        result = list_users_for_manager("usr_manager")
        assert isinstance(result["items"], list)

    def test_get_user_found(self):
        from modules.flow_gate.settings.user_admin_service import get_user

        user = get_user("usr_admin")
        assert user is not None
        assert "password" not in user

    def test_get_user_not_found(self):
        from modules.flow_gate.settings.user_admin_service import get_user

        assert get_user("nonexistent") is None

    def test_create_user(self):
        from modules.flow_gate.settings.user_admin_service import create_user, get_user

        user = create_user({"username": "newuser", "email": "new@test.com", "password": "Pass1234!"})
        assert user["username"] == "newuser"
        assert "password" not in user
        found = get_user(user["user_id"])
        assert found is not None

    def test_update_user(self):
        from modules.flow_gate.settings.user_admin_service import update_user

        user = update_user("usr_worker", {"is_active": 0})
        assert user is not None
        assert user["is_active"] == 0
        update_user("usr_worker", {"is_active": 1})

    def test_deactivate_user(self):
        from modules.flow_gate.settings.user_admin_service import deactivate_user, update_user

        user = deactivate_user("usr_worker")
        assert user["is_active"] == 0
        update_user("usr_worker", {"is_active": 1})

    def test_assign_and_revoke_role(self):
        from modules.flow_gate.settings.user_admin_service import assign_project_role, get_user_project_roles, revoke_project_role

        assign_project_role("usr_worker", "proj_001", "role_manager")
        roles = get_user_project_roles("usr_worker")
        assert any(r["project_id"] == "proj_001" for r in roles)
        revoke_project_role("usr_worker", "proj_001")
        roles_after = get_user_project_roles("usr_worker")
        assert not any(r["project_id"] == "proj_001" for r in roles_after)


class TestProjectSettingsService:
    def test_list_document_types(self):
        from modules.flow_gate.settings.project_settings_service import list_document_types

        result = list_document_types("proj_001")
        assert isinstance(result, list)

    def test_list_document_types_includes_active_template(self, mock_db):
        from modules.flow_gate.settings.project_settings_service import list_document_types

        mock_db._execute(
            "INSERT OR REPLACE INTO document_type_templates"
            " (project_id, type_code, template_path, is_active, uploaded_by, uploaded_at)"
            " VALUES (?, ?, ?, 1, ?, datetime('now'))",
            ["proj_001", "R", "C:/templates/proj_001_R_template.md", "usr_admin"],
        )

        result = list_document_types("proj_001")
        r_type = next(row for row in result if row["type_code"] == "R")
        assert r_type["template_path"] == "C:/templates/proj_001_R_template.md"

    def test_create_and_delete_document_type(self):
        from modules.flow_gate.settings.project_settings_service import create_document_type, delete_document_type, list_document_types

        row = create_document_type(
            "proj_001",
            {"type_code": "TST", "type_name": "Test Type", "series": "work"},
        )
        assert row["type_code"] == "TST"
        result = list_document_types("proj_001")
        assert any(r["type_code"] == "TST" for r in result)
        assert delete_document_type("proj_001", row["id"]) is True

    def test_delete_system_type_raises(self, mock_db):
        from modules.flow_gate.settings.project_settings_service import delete_document_type
        from modules.flow_gate.db.connection import now_iso

        now = now_iso()
        mock_db._execute(
            "INSERT INTO document_types(project_id,type_code,series,is_system,is_active,sort_order,created_at,updated_at)"
            " VALUES(?,?,?,1,1,0,?,?)",
            ["proj_001", "SYS", "work", now, now],
        )
        row = mock_db._fetch_one(
            "SELECT * FROM document_types WHERE type_code='SYS' AND project_id='proj_001'"
        )
        with pytest.raises(ValueError, match="System-reserved document types cannot be deleted"):
            delete_document_type("proj_001", row["id"])
        mock_db._execute("DELETE FROM document_types WHERE type_code='SYS' AND project_id='proj_001'")

    def test_update_project_settings(self):
        from modules.flow_gate.settings.project_settings_service import get_project_settings, update_project_settings

        row = update_project_settings("proj_001", {"digits_group": 5})
        assert row["digits_group"] == 5
        assert get_project_settings("proj_001")["digits_group"] == 5

    def test_numbering_impact(self):
        from modules.flow_gate.settings.project_settings_service import get_numbering_impact

        impact = get_numbering_impact("proj_001")
        assert "documents" in impact
        assert "groups" in impact

    def test_enqueue_numbering_migrate(self, mock_db):
        from modules.flow_gate.settings.project_settings_service import enqueue_numbering_migrate, get_numbering_job

        mock_db._execute("DELETE FROM numbering_jobs WHERE project_id='proj_001'")
        job = enqueue_numbering_migrate("proj_001", "group", 4, 5, "usr_admin")
        assert job["status"] == "queued"
        found = get_numbering_job(job["id"])
        assert found is not None
        with pytest.raises(ValueError):
            enqueue_numbering_migrate("proj_001", "group", 4, 5, "usr_admin")
        mock_db._execute("DELETE FROM numbering_jobs WHERE project_id='proj_001'")


class TestProjectCreate:
    """T257: project_name UNIQUE constraint ? block duplicate creates with 422."""

    def _make_client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from modules.flow_gate.settings.routers.project_settings import router
        from modules.flow_gate.auth.middleware import get_current_user

        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_current_user] = lambda: {"user_id": "usr_admin", "is_admin": 1}
        return TestClient(app)

    def test_create_project_first_succeeds(self, mock_db):
        client = self._make_client()
        resp = client.post("/api/v1/projects", json={"project_name": "UniqueProject257"})
        assert resp.status_code == 201
        assert resp.json()["project_name"] == "UniqueProject257"
        mock_db._execute("DELETE FROM projects WHERE project_name = 'UniqueProject257'")

    def test_create_project_duplicate_name_returns_422(self, mock_db):
        client = self._make_client()
        resp1 = client.post("/api/v1/projects", json={"project_name": "DuplicateProject257"})
        assert resp1.status_code == 201
        resp2 = client.post("/api/v1/projects", json={"project_name": "DuplicateProject257"})
        assert resp2.status_code == 422
        assert "DuplicateProject257" in resp2.json()["detail"]
        mock_db._execute("DELETE FROM projects WHERE project_name = 'DuplicateProject257'")

    def test_create_project_slug_from_name(self, mock_db):
        client = self._make_client()
        resp = client.post("/api/v1/projects", json={"project_name": "FlowGate MVP"})
        assert resp.status_code == 201
        assert resp.json()["project_id"] == "flowgate-mvp"
        mock_db._execute("DELETE FROM projects WHERE project_id = 'flowgate-mvp'")

    def test_create_project_korean_name_allowed(self, mock_db):
        client = self._make_client()
        resp = client.post("/api/v1/projects", json={"project_name": "Test"})
        assert resp.status_code == 201
        # Slugification lowercases the name (cf. test_create_project_slug_from_name).
        assert resp.json()["project_id"] == "test"
        mock_db._execute("DELETE FROM projects WHERE project_id = 'test'")

    def test_create_project_dangerous_chars_rejected(self):
        client = self._make_client()
        resp = client.post("/api/v1/projects", json={"project_name": "bad/name"})
        assert resp.status_code == 422

    def test_create_project_empty_after_slug(self):
        client = self._make_client()
        resp = client.post("/api/v1/projects", json={"project_name": "???"})
        assert resp.status_code == 422


class TestRBACDecorators:
    def test_is_admin_passes_all(self):
        from modules.flow_gate.rbac.decorators import _has_permission

        admin = {"user_id": "usr_admin", "is_admin": 1}
        assert _has_permission(admin, "system.settings.manage", None) is True
        assert _has_permission(admin, "any.permission", "any_project") is True

    def test_role_admin_has_system_permission(self):
        from modules.flow_gate.rbac.decorators import _has_permission

        user = {"user_id": "usr_admin", "is_admin": 0}
        assert _has_permission(user, "system.settings.manage", None) is True

    def test_no_role_denied(self):
        from modules.flow_gate.rbac.decorators import _has_permission

        user = {"user_id": "usr_worker", "is_admin": 0}
        assert _has_permission(user, "system.settings.manage", None) is False

    def test_manager_has_project_permission(self):
        from modules.flow_gate.rbac.decorators import _has_permission

        user = {"user_id": "usr_manager", "is_admin": 0}
        assert _has_permission(user, "project.settings.read", "proj_001") is True

    def test_manager_denied_system_permission(self):
        from modules.flow_gate.rbac.decorators import _has_permission

        user = {"user_id": "usr_manager", "is_admin": 0}
        assert _has_permission(user, "system.settings.manage", None) is False


class TestProjectArchiveRestore:
    def _make_client(self, user):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from modules.flow_gate.settings.routers.project_settings import router
        from modules.flow_gate.auth.middleware import get_current_user

        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    def test_project_list_defaults_to_active_and_management_can_request_all(self, mock_db):
        client = self._make_client({"user_id": "usr_admin", "is_admin": 1})
        mock_db._execute(
            "INSERT OR REPLACE INTO projects(project_id,project_name,is_active,created_at,updated_at) "
            "VALUES('proj_archived','Archived',0,datetime('now'),datetime('now'))"
        )
        try:
            active_resp = client.get("/api/v1/projects")
            all_resp = client.get("/api/v1/projects?status=all")

            assert all_resp.status_code == 200
            assert active_resp.status_code == 200
            all_ids = {p["project_id"] for p in all_resp.json()["projects"]}
            active_ids = {p["project_id"] for p in active_resp.json()["projects"]}
            assert "proj_archived" in all_ids
            assert "proj_archived" not in active_ids
        finally:
            mock_db._execute("DELETE FROM projects WHERE project_id='proj_archived'")

    def test_archive_restore_are_persistent_and_idempotent(self, mock_db):
        client = self._make_client({"user_id": "usr_admin", "is_admin": 1})

        archived = client.post("/api/v1/projects/proj_001/archive")
        archived_again = client.post("/api/v1/projects/proj_001/archive")
        row = mock_db._fetch_one("SELECT is_active FROM projects WHERE project_id='proj_001'")
        restored = client.post("/api/v1/projects/proj_001/restore")
        row_after_restore = mock_db._fetch_one("SELECT is_active FROM projects WHERE project_id='proj_001'")

        assert archived.status_code == 200
        assert archived.json()["is_active"] == 0
        assert archived_again.status_code == 200
        assert archived_again.json()["is_active"] == 0
        assert row["is_active"] == 0
        assert restored.status_code == 200
        assert restored.json()["is_active"] == 1
        assert row_after_restore["is_active"] == 1

    def test_patch_status_rejects_missing_project(self):
        client = self._make_client({"user_id": "usr_admin", "is_admin": 1})

        resp = client.patch("/api/v1/projects/missing/status", json={"is_active": 0})

        assert resp.status_code == 404
    def test_patch_status_rejects_invalid_boolean(self):
        client = self._make_client({"user_id": "usr_admin", "is_admin": 1})

        resp = client.patch("/api/v1/projects/proj_001/status", json={"is_active": 2})

        assert resp.status_code == 422

    def test_project_status_requires_project_edit_permission(self):
        client = self._make_client({"user_id": "usr_worker", "is_admin": 0})

        resp = client.post("/api/v1/projects/proj_001/archive")

        assert resp.status_code == 403
