"""flowgate.v02.0020 — 환경 설정 시안 11~14: workspaces, system users, system security.

Covers the migration 136 data carry-over, the workspace service (membership, project access
materialised into user_project_roles, policy, AI allowlist enforcement), the system user service
(invitations, last-admin guard) and the security policy (2FA enrolment, lockout, password length,
idle sign-out, sign-out when the 2FA scope widens), plus the HTTP routes.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))
_MIGRATIONS = _SERVER_DIR / "sql" / "migrations" / "sqlite"

_SEED = """
INSERT OR IGNORE INTO roles(role_id,role_name,is_system,created_at,updated_at) VALUES
 ('role_admin','Administrator',1,datetime('now'),datetime('now')),
 ('role_manager','Manager',1,datetime('now'),datetime('now')),
 ('role_worker','Worker',1,datetime('now'),datetime('now')),
 ('role_viewer','Viewer',1,datetime('now'),datetime('now'));
INSERT OR IGNORE INTO permissions(permission_id,permission_name,created_at) VALUES
 ('system.settings.manage','Manage',datetime('now')),
 ('system.user.read','Read',datetime('now')),
 ('system.user.create','Create',datetime('now')),
 ('system.user.update','Update',datetime('now'));
INSERT OR IGNORE INTO role_permissions(role_id,permission_id) VALUES
 ('role_admin','system.settings.manage'),('role_admin','system.user.read'),
 ('role_admin','system.user.create'),('role_admin','system.user.update');
INSERT OR IGNORE INTO projects(project_id,project_name,is_active,created_at,updated_at) VALUES
 ('__SYSTEM__','[System]',1,datetime('now'),datetime('now')),
 ('alpha','Alpha',1,datetime('now'),datetime('now')),
 ('beta','Beta',1,datetime('now'),datetime('now'));
INSERT OR IGNORE INTO users(user_id,username,email,password,is_active,is_admin,first_login_required,created_at,updated_at) VALUES
 ('u_admin','admin','admin@x.io','x',1,1,0,'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00'),
 ('u_admin2','admin2','admin2@x.io','x',1,1,0,'2026-01-02T00:00:00+00:00','2026-01-02T00:00:00+00:00'),
 ('u_dev','dev','dev@x.io','x',1,0,0,'2026-01-03T00:00:00+00:00','2026-01-03T00:00:00+00:00'),
 ('u_view','viewer','view@x.io','x',1,0,0,'2026-01-04T00:00:00+00:00','2026-01-04T00:00:00+00:00'),
 ('u_free','free','free@x.io','x',1,0,0,'2026-01-05T00:00:00+00:00','2026-01-05T00:00:00+00:00');
INSERT OR IGNORE INTO user_project_roles(user_id,project_id,role_id,granted_at) VALUES
 ('u_admin','__SYSTEM__','role_admin',datetime('now')),
 ('u_admin2','__SYSTEM__','role_admin',datetime('now')),
 ('u_dev','__SYSTEM__','role_viewer',datetime('now')),
 ('u_view','__SYSTEM__','role_viewer',datetime('now')),
 ('u_free','__SYSTEM__','role_viewer',datetime('now')),
 ('u_dev','alpha','role_worker',datetime('now')),
 ('u_view','alpha','role_viewer',datetime('now')),
 ('u_admin2','beta','role_manager',datetime('now'));
"""


def _build_db(path: Path) -> str:
    """Every migration before 136, the seed above, then 136 — so 136's carry-over of existing
    data is exercised exactly as on an upgraded install."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    files = sorted(_MIGRATIONS.glob("*.sql"))
    later = [f for f in files if f.name >= "136"]
    for migration in files:
        if migration.name < "136":
            conn.executescript(migration.read_text(encoding="utf-8"))
    conn.executescript(_SEED)
    for migration in later:
        conn.executescript(migration.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    return str(path)


class _SqliteStore:
    """FlowGateStore double over one sqlite file (same shape as test_auth.py's)."""

    def __init__(self, db_path):
        self._db_path = db_path
        self._txn = None

    def _conn(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def transaction(self):
        if self._txn is not None:
            yield self
            return
        conn = self._conn()
        self._txn = conn
        try:
            yield self
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._txn = None
            conn.close()

    def _execute(self, sql, params=None):
        if self._txn is not None:
            self._txn.execute(sql, params or [])
            return
        with self._conn() as conn:
            conn.execute(sql, params or [])
            conn.commit()

    def _fetch_one(self, sql, params=None):
        rows = self._fetch_all(sql, params)
        return rows[0] if rows else None

    def _fetch_all(self, sql, params=None):
        if self._txn is not None:
            return [dict(r) for r in self._txn.execute(sql, params or []).fetchall()]
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params or []).fetchall()]

    def table_exists(self, table_name):
        return self._fetch_one("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", [table_name]) is not None


@pytest.fixture
def store(tmp_path):
    import modules.flow_gate.db.connection as conn_mod
    from modules.flow_gate.db import meta_cache
    from modules.flow_gate.auth import auth_cache
    from modules.flow_gate.rbac import permission_service
    from modules.flow_gate.settings import security_policy_service

    db = _SqliteStore(_build_db(tmp_path / "ws.db"))
    previous = conn_mod.STORE
    conn_mod.STORE = db  # every module's get_store() returns the module-global STORE
    permission_service.invalidate_all()
    security_policy_service.invalidate_cache()
    for name in ("invalidate_all", "clear"):
        fn = getattr(meta_cache, name, None)
        if callable(fn):
            fn()
    try:
        yield db
    finally:
        conn_mod.STORE = previous
        permission_service.invalidate_all()
        security_policy_service.invalidate_cache()
        auth_cache.invalidate_all_sessions()


ADMIN = {"user_id": "u_admin", "is_admin": 1, "is_active": 1}


def _user(uid):
    return {"user_id": uid, "is_admin": 0, "is_active": 1}


def _role(store, uid, pid):
    row = store._fetch_one("SELECT role_id FROM user_project_roles WHERE user_id=? AND project_id=?", [uid, pid])
    return row["role_id"] if row else None


# ── migration 136 ────────────────────────────────────────────────────────────

class TestMigration:
    def test_default_workspace_takes_every_project_and_member(self, store):
        ws = store._fetch_one("SELECT * FROM workspaces WHERE workspace_id='ws_default'")
        assert ws["is_default"] == 1 and ws["status"] == "active" and ws["project_create_policy"] == "all"
        projects = store._fetch_all("SELECT project_id, workspace_id FROM projects ORDER BY project_id")
        assert {p["project_id"]: p["workspace_id"] for p in projects} == {
            "__SYSTEM__": None, "alpha": "ws_default", "beta": "ws_default"}
        members = {m["user_id"]: m["role"] for m in store._fetch_all("SELECT * FROM workspace_members")}
        # Oldest active admin owns it, the other admin administers it, project members are members,
        # and a user with only a system role is in no workspace.
        assert members == {"u_admin": "owner", "u_admin2": "admin", "u_dev": "member", "u_view": "member"}

    def test_existing_project_roles_survive_as_exceptions(self, store):
        exc = {(e["project_id"], e["user_id"]): e["access"]
               for e in store._fetch_all("SELECT * FROM workspace_project_access")}
        # member default 'none' → both member roles kept; admin default 'dev' → manager kept as admin.
        assert exc == {("alpha", "u_dev"): "dev", ("alpha", "u_view"): "review", ("beta", "u_admin2"): "admin"}

    def test_a_sync_after_migration_changes_no_existing_role(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        ws.sync("ws_default")
        assert _role(store, "u_dev", "alpha") == "role_worker"
        assert _role(store, "u_view", "alpha") == "role_viewer"
        assert _role(store, "u_admin2", "beta") == "role_manager"
        assert _role(store, "u_view", "beta") is None


# ── workspace service ────────────────────────────────────────────────────────

class TestWorkspaces:
    def test_list_shows_counts_owner_and_visibility(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        result = ws.list_workspaces(ADMIN)
        assert result["can_create"] is True
        [default] = result["workspaces"]
        assert default["member_count"] == 4 and default["project_count"] == 2
        assert default["owner"]["user_id"] == "u_admin" and default["ai_policy_mode"] == "off"
        assert ws.list_workspaces(_user("u_free"))["workspaces"] == []
        assert [w["workspace_id"] for w in ws.list_workspaces(_user("u_dev"))["workspaces"]] == ["ws_default"]

    def test_only_a_system_admin_creates_and_names_are_unique(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        with pytest.raises(ws.WorkspaceError) as denied:
            ws.create_workspace(_user("u_dev"), "Lab", None)
        assert denied.value.code == "forbidden"
        created = ws.create_workspace(ADMIN, "  Personal   Lab ", "u_dev")
        assert created["name"] == "Personal Lab" and created["owner"]["user_id"] == "u_dev"
        assert created["policy"]["project_create"] == "owner_admin"
        with pytest.raises(ws.WorkspaceError) as dup:
            ws.create_workspace(ADMIN, "personal lab", None)
        assert dup.value.code == "duplicate_name"

    def test_member_role_default_access_and_exception_materialise_roles(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        ws.add_member(ADMIN, "ws_default", "u_free", "member")
        assert _role(store, "u_free", "alpha") is None  # member default: none
        ws.set_default_access(ADMIN, "ws_default", {"member": "review"})
        assert _role(store, "u_free", "alpha") == "role_viewer"
        assert _role(store, "u_free", "beta") == "role_viewer"
        detail = ws.set_project_access(ADMIN, "ws_default", "beta", "u_free", "none")
        assert _role(store, "u_free", "beta") is None
        assert {"project_id": "beta", "user_id": "u_free", "access": "none"} in detail["access"]["exceptions"]
        assert next(m for m in detail["members"] if m["user_id"] == "u_free")["project_count"] == 1
        ws.change_member_role(ADMIN, "ws_default", "u_free", "admin")
        assert _role(store, "u_free", "alpha") == "role_worker"  # admin default: dev
        ws.set_project_access(ADMIN, "ws_default", "beta", "u_free", "default")
        assert _role(store, "u_free", "beta") == "role_worker"









def _rbac_client(user):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.rbac.routers.rbac_admin import router as rbac_router

    app = FastAPI()
    app.include_router(rbac_router, prefix="/rbac")
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)






# ── system users ─────────────────────────────────────────────────────────────



# ── security policy ──────────────────────────────────────────────────────────



# ── HTTP routes ──────────────────────────────────────────────────────────────

def _client(user):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.auth.auth_api import router as auth_router
    from modules.flow_gate.settings.routers.system_users import router as system_router
    from modules.flow_gate.settings.routers.workspaces import router as ws_router

    app = FastAPI()
    app.include_router(ws_router, prefix="/api/v1")
    app.include_router(system_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/auth")
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)
