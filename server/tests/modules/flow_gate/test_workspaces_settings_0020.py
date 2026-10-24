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

    def test_removing_a_member_leaves_every_project_and_owner_is_locked(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        ws.remove_member(ADMIN, "ws_default", "u_dev")
        assert _role(store, "u_dev", "alpha") is None
        assert store._fetch_all("SELECT * FROM workspace_project_access WHERE user_id='u_dev'") == []
        with pytest.raises(ws.WorkspaceError) as locked:
            ws.remove_member(ADMIN, "ws_default", "u_admin")
        assert locked.value.code == "owner_locked"

    def test_member_management_needs_owner_or_admin(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        with pytest.raises(ws.WorkspaceError):
            ws.add_member(_user("u_dev"), "ws_default", "u_free", "member")
        ws.add_member(_user("u_admin2"), "ws_default", "u_free", "member")  # workspace admin
        with pytest.raises(ws.WorkspaceError) as denied:
            ws.update_workspace(_user("u_admin2"), "ws_default", name="Renamed")
        assert denied.value.code == "forbidden"  # rename is the owner's

    def test_disable_revokes_and_enable_restores_access(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        lab = ws.create_workspace(ADMIN, "Archive Team", "u_admin")
        store._execute("UPDATE projects SET workspace_id=? WHERE project_id='beta'", [lab["workspace_id"]])
        ws.add_member(ADMIN, lab["workspace_id"], "u_view", "admin")
        assert _role(store, "u_view", "beta") == "role_worker"
        ws.update_workspace(ADMIN, lab["workspace_id"], status="disabled")
        assert _role(store, "u_view", "beta") is None
        ws.update_workspace(ADMIN, lab["workspace_id"], status="active")
        assert _role(store, "u_view", "beta") == "role_worker"
        with pytest.raises(ws.WorkspaceError) as locked:
            ws.update_workspace(ADMIN, "ws_default", status="disabled")
        assert locked.value.code == "default_workspace_locked"

    def test_delete_needs_an_empty_non_default_workspace(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        lab = ws.create_workspace(ADMIN, "Data Platform", "u_admin")
        ws.add_member(ADMIN, lab["workspace_id"], "u_dev", "member")
        with pytest.raises(ws.WorkspaceError) as busy:
            ws.delete_workspace(ADMIN, lab["workspace_id"])
        assert busy.value.code == "workspace_not_empty"
        ws.remove_member(ADMIN, lab["workspace_id"], "u_dev")
        ws.delete_workspace(ADMIN, lab["workspace_id"])
        assert store._fetch_one("SELECT 1 FROM workspaces WHERE workspace_id=?", [lab["workspace_id"]]) is None
        with pytest.raises(ws.WorkspaceError):
            ws.delete_workspace(ADMIN, "ws_default")

    def test_project_create_policy(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        assert ws.resolve_create_workspace(_user("u_dev"), None) == "ws_default"  # migrated: all
        ws.set_policy(ADMIN, "ws_default", project_create="owner_admin")
        with pytest.raises(ws.WorkspaceError) as denied:
            ws.resolve_create_workspace(_user("u_dev"), None)
        assert denied.value.code == "project_create_forbidden"
        assert ws.resolve_create_workspace(_user("u_admin2"), "ws_default") == "ws_default"
        ws.set_policy(ADMIN, "ws_default", project_create="owner")
        assert not ws.can_create_project(_user("u_admin2"), "ws_default")
        with pytest.raises(ws.WorkspaceError):
            ws.resolve_create_workspace(_user("u_free"), None)  # not a member

    def test_new_project_goes_to_its_workspace_and_creator_can_open_it(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        store._execute("INSERT INTO projects(project_id,project_name,is_active,created_at,updated_at) "
                       "VALUES ('gamma','Gamma',1,datetime('now'),datetime('now'))")
        ws.on_project_created(_user("u_dev"), "gamma", "ws_default")
        assert store._fetch_one("SELECT workspace_id FROM projects WHERE project_id='gamma'")["workspace_id"] == "ws_default"
        assert _role(store, "u_dev", "gamma") == "role_manager"
        assert _role(store, "u_admin2", "gamma") == "role_worker"  # admin default: dev

    def test_legacy_role_api_keeps_the_workspace_in_step(self, store):
        from modules.flow_gate.settings import user_admin_service
        from modules.flow_gate.settings import workspace_service as ws

        user_admin_service.assign_project_role("u_free", "beta", "role_worker", granted_by="u_admin")
        assert ws.member_role("ws_default", "u_free") == "member"
        ws.sync("ws_default")
        assert _role(store, "u_free", "beta") == "role_worker"
        user_admin_service.revoke_project_role("u_free", "beta")
        ws.sync("ws_default")
        assert _role(store, "u_free", "beta") is None


def _rbac_client(user):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.rbac.routers.rbac_admin import router as rbac_router

    app = FastAPI()
    app.include_router(rbac_router, prefix="/rbac")
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


class TestLegacyRbacRoleRoutes:
    """POST /rbac/users/{id}/roles and DELETE /rbac/users/{id}/roles/{project_id} go through the
    workspace: no project access without workspace membership (R0001 §3.1)."""

    def _workspace(self, store, default_member="none"):
        from modules.flow_gate.settings import workspace_service as ws

        wid = ws.create_workspace(ADMIN, "Data Platform", "u_admin")["workspace_id"]
        for pid in ("gamma", "delta"):
            store._execute("INSERT INTO projects(project_id,project_name,is_active,created_at,updated_at) "
                           "VALUES (?,?,1,datetime('now'),datetime('now'))", [pid, pid.title()])
            ws.on_project_created(ADMIN, pid, wid)
        ws.set_default_access(ADMIN, wid, {"member": default_member})
        return wid

    def test_grant_to_an_outsider_makes_a_member_with_access_to_that_project_only(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        wid = self._workspace(store, default_member="dev")
        assert ws.member_role(wid, "u_free") is None
        resp = _rbac_client(ADMIN).post("/rbac/users/u_free/roles",
                                        json={"project_id": "gamma", "role_id": "role_viewer"})
        assert resp.status_code == 201
        assert resp.json()["role_id"] == "role_viewer"
        assert ws.member_role(wid, "u_free") == "member"
        assert _role(store, "u_free", "gamma") == "role_viewer"
        assert _role(store, "u_free", "delta") is None  # the member default does not leak
        ws.sync(wid)
        assert (_role(store, "u_free", "gamma"), _role(store, "u_free", "delta")) == ("role_viewer", None)

    def test_revoke_route_records_the_exception_so_a_sync_keeps_it_revoked(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        wid = self._workspace(store, default_member="dev")
        ws.add_member(ADMIN, wid, "u_free", "member")
        assert _role(store, "u_free", "gamma") == "role_worker"
        resp = _rbac_client(ADMIN).delete("/rbac/users/u_free/roles/gamma")
        assert resp.status_code == 204
        assert _role(store, "u_free", "gamma") is None
        ws.sync(wid)
        assert _role(store, "u_free", "gamma") is None
        assert _role(store, "u_free", "delta") == "role_worker"

    def test_a_project_manager_cannot_pull_an_outsider_into_the_workspace(self, store, monkeypatch):
        from modules.flow_gate.rbac.routers import rbac_admin
        from modules.flow_gate.settings import workspace_service as ws

        wid = self._workspace(store)
        monkeypatch.setattr(rbac_admin, "get_user_permissions", lambda uid, pid: {"perm_role_assign"})
        client = _rbac_client(_user("u_dev"))
        resp = client.post("/rbac/users/u_free/roles", json={"project_id": "gamma", "role_id": "role_worker"})
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "not_workspace_member"
        assert _role(store, "u_free", "gamma") is None and ws.member_role(wid, "u_free") is None
        ws.add_member(ADMIN, wid, "u_free", "member")
        resp = client.post("/rbac/users/u_free/roles", json={"project_id": "gamma", "role_id": "role_worker"})
        assert resp.status_code == 201  # an existing member: the project-level grant stands
        assert _role(store, "u_free", "gamma") == "role_worker"

    def test_disabled_workspace_and_custom_roles_take_no_grant(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        wid = self._workspace(store)
        store._execute("INSERT INTO roles(role_id,role_name,is_system,created_at,updated_at) "
                       "VALUES ('role_custom','Custom',0,datetime('now'),datetime('now'))")
        client = _rbac_client(ADMIN)
        resp = client.post("/rbac/users/u_free/roles", json={"project_id": "gamma", "role_id": "role_custom"})
        assert (resp.status_code, resp.json()["detail"]["code"]) == (422, "role_invalid")
        ws.update_workspace(ADMIN, wid, status="disabled")
        resp = client.post("/rbac/users/u_free/roles", json={"project_id": "gamma", "role_id": "role_worker"})
        assert (resp.status_code, resp.json()["detail"]["code"]) == (409, "workspace_disabled")
        assert _role(store, "u_free", "gamma") is None and ws.member_role(wid, "u_free") is None

    def test_system_roles_are_not_workspace_bound(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        resp = _rbac_client(ADMIN).post("/rbac/users/u_free/roles",
                                        json={"project_id": "__SYSTEM__", "role_id": "role_manager"})
        assert resp.status_code == 201
        assert _role(store, "u_free", "__SYSTEM__") == "role_manager"
        assert ws.member_role("ws_default", "u_free") is None

    def test_settings_user_create_with_project_roles_goes_through_the_workspace(self, store):
        from modules.flow_gate.settings import user_admin_service
        from modules.flow_gate.settings import workspace_service as ws

        wid = self._workspace(store, default_member="review")
        created = user_admin_service.create_user({
            "username": "newbie", "email": "newbie@x.io", "password": "Pass1234!Pass",
            "project_roles": [{"project_id": "gamma", "role_id": "role_worker"}],
        }, created_by="u_admin")
        uid = created["user_id"]
        assert ws.member_role(wid, uid) == "member"
        assert (_role(store, uid, "gamma"), _role(store, uid, "delta")) == ("role_worker", None)


class TestAiPolicy:
    def _seed_ai(self, store):
        store._execute(
            "INSERT INTO ai_providers (provider_id, project_id, name, exec_type, kind, enabled, sort_order, "
            "cli_command, owner_scope, created_at, updated_at) "
            "VALUES ('aip_a', NULL, 'OpenAI', 'cli', 'codex', 1, 0, 'codex', 'system', datetime('now'), datetime('now')),"
            "       ('aip_b', NULL, 'Claude', 'cli', 'claude', 1, 1, 'claude', 'system', datetime('now'), datetime('now'))"
        )

    def test_allowlist_limits_the_run_chain_and_revoke_blocks_new_runs(self, store, monkeypatch):
        from modules.flow_gate.settings import ai_settings_service
        from modules.flow_gate.settings import workspace_service as ws

        self._seed_ai(store)
        chain = {"ok": True, "source": "system", "default_provider_id": "aip_a",
                 "providers": [{"id": "aip_a", "name": "OpenAI"}, {"id": "aip_b", "name": "Claude"}]}
        monkeypatch.setattr(ai_settings_service, "_require_project", lambda pid: None)
        monkeypatch.setattr(ai_settings_service, "resolve_global", lambda user_id=None: dict(chain))

        assert ws.allowed_resource_ids("alpha") is None  # policy off
        assert [p["id"] for p in ai_settings_service.resolve_effective("alpha")["providers"]] == ["aip_a", "aip_b"]

        ws.set_policy(ADMIN, "ws_default", ai_mode="allow")
        assert ai_settings_service.resolve_effective("alpha")["providers"] == []
        detail = ws.allow_resource(ADMIN, "ws_default", "provider", "aip_b")
        assert detail["policy"]["allowlist"][0]["name"] == "Claude"
        effective = ai_settings_service.resolve_effective("alpha")
        assert [p["id"] for p in effective["providers"]] == ["aip_b"]
        assert effective["default_provider_id"] == "aip_b"

        ws.revoke_resource(ADMIN, "ws_default", "provider", "aip_b")
        assert ai_settings_service.resolve_effective("alpha")["providers"] == []
        row = store._fetch_one("SELECT status FROM workspace_ai_allowlist WHERE resource_id='aip_b'")
        assert row["status"] == "revoked"  # kept as history
        with pytest.raises(ws.WorkspaceError):
            ws.allow_resource(ADMIN, "ws_default", "provider", "aip_missing")

    def test_resources_list_system_ai_and_agents(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        self._seed_ai(store)
        store._execute("INSERT INTO agents(agent_id,name,enabled,location,created_at,updated_at) "
                       "VALUES ('ag_1','Local Codex Worker',1,'local',datetime('now'),datetime('now'))")
        result = ws.list_policy_resources(ADMIN, "ws_default")
        assert [p["name"] for p in result["providers"]] == ["OpenAI", "Claude"]
        assert result["agents"][0]["name"] == "Local Codex Worker"
        ws.set_policy(ADMIN, "ws_default", ai_mode="allow")
        ws.allow_resource(ADMIN, "ws_default", "agent", "ag_1")
        assert ws.is_agent_allowed("alpha", "ag_1") and not ws.is_agent_allowed("alpha", "ag_2")


# ── system users ─────────────────────────────────────────────────────────────

class TestSystemUsers:
    def test_list_summary(self, store):
        from modules.flow_gate.settings import system_user_service as su

        result = su.list_users()
        assert result["summary"]["total"] == 5 and result["summary"]["admins"] == 2
        assert result["summary"]["no_workspace"] == 1  # u_free
        assert result["summary"]["tfa_missing"] == 5
        free = next(u for u in result["users"] if u["user_id"] == "u_free")
        assert free["workspaces"] == [] and free["state"] == "active"

    def test_invite_create_accept_and_login_state(self, store):
        from modules.flow_gate.settings import system_user_service as su

        created = su.create_user(ADMIN, "Han Seojun", "han@x.io", "invite", False, "ws_default")
        assert created["user"]["state"] == "invited" and created["invite_path"].startswith("/login?invite=")
        assert created["user"]["workspaces"][0]["workspace_id"] == "ws_default"
        token = created["invite_path"].split("=", 1)[1]
        assert su.find_invited(token)["username"] == "Han Seojun"
        with pytest.raises(su.SystemUserError) as weak:
            su.accept_invite(token, "short")
        assert weak.value.code == "invalid_password_policy"
        assert su.accept_invite(token, "Str0ng-Passw0rd!")["email"] == "han@x.io"
        assert su.find_invited(token) is None
        with pytest.raises(su.SystemUserError) as dup:
            su.create_user(ADMIN, "Other", "han@x.io", "password", False)
        assert dup.value.code == "email_already_exists"

    def test_temporary_password_user_is_admin_when_asked(self, store):
        from modules.flow_gate.settings import system_user_service as su

        created = su.create_user(ADMIN, "Choi Yuna", "yuna@x.io", "password", True)
        assert created["temp_password"] and created["user"]["is_admin"] is True
        row = store._fetch_one("SELECT first_login_required FROM users WHERE username='Choi Yuna'")
        assert row["first_login_required"] == 1

    def test_last_admin_guard_and_disable_ends_sessions(self, store):
        from modules.flow_gate.auth.session_store import create_session, get_session
        from modules.flow_gate.settings import system_user_service as su

        su.set_admin(ADMIN, "u_admin2", False)
        with pytest.raises(su.SystemUserError) as last:
            su.set_admin(ADMIN, "u_admin", False)
        assert last.value.code == "last_admin"
        with pytest.raises(su.SystemUserError):
            su.disable_user({"user_id": "u_admin2", "is_admin": 1}, "u_admin")
        sid = create_session("u_dev")
        assert su.disable_user(ADMIN, "u_dev")["state"] == "disabled"
        assert get_session(sid)["revoked_at"] is not None
        assert su.enable_user(ADMIN, "u_dev")["state"] == "active"

    def test_resend_invite_and_resets(self, store):
        from modules.flow_gate.settings import system_user_service as su

        created = su.create_user(ADMIN, "Park Jiwon", "jiwon@x.io", "invite", False)
        uid = created["user"]["user_id"]
        again = su.resend_invite(ADMIN, uid)
        assert again["invite_path"] != created["invite_path"]
        assert su.find_invited(created["invite_path"].split("=", 1)[1]) is None
        with pytest.raises(su.SystemUserError):
            su.resend_invite(ADMIN, "u_dev")
        store._execute("UPDATE users SET totp_secret='enc' WHERE user_id='u_dev'")
        assert su.reset_tfa(ADMIN, "u_dev")["tfa_enabled"] is False
        assert su.reset_password(ADMIN, "u_dev")["temp_password"]


# ── security policy ──────────────────────────────────────────────────────────

class TestSecurityPolicy:
    def test_defaults_keep_the_previous_behaviour(self, store):
        from modules.flow_gate.auth.password import validate_password
        from modules.flow_gate.settings import security_policy_service as sec

        policy = sec.get_policy(use_cache=False)
        assert policy["tfa_scope"] == "off" and policy["password_min_length"] == 12
        assert sec.lock_rule() == (5, 15)
        assert any("12 characters" in e for e in validate_password("Abcdef1!x"))

    def test_update_validates_and_applies(self, store):
        from modules.flow_gate.auth.password import validate_password
        from modules.flow_gate.settings import security_policy_service as sec

        with pytest.raises(sec.SecurityPolicyError):
            sec.update_policy({"lock_attempts": 4})
        with pytest.raises(sec.SecurityPolicyError):
            sec.update_policy({"nope": 1})
        sec.update_policy({"password_min_length": 8, "lock_attempts": 3, "lock_minutes": 60,
                           "idle_timeout": "30m", "recovery_codes": False})
        assert validate_password("Abcdef1!x") == []
        assert sec.lock_rule() == (3, 60)
        assert sec.idle_seconds() == 1800 and sec.recovery_codes_enabled() is False

    def test_tfa_enrolment_waits_for_the_grace_period(self, store):
        from modules.flow_gate.settings import security_policy_service as sec

        sec.update_policy({"tfa_scope": "admin", "tfa_grace_days": 7})
        admin = {"is_admin": 1, "created_at": "2026-01-01T00:00:00+00:00"}
        member = {"is_admin": 0, "created_at": "2026-01-01T00:00:00+00:00"}
        assert sec.tfa_required(admin) and not sec.tfa_required(member)
        assert sec.tfa_enrollment_due(admin) is False  # policy changed just now: 7 days left
        sec.update_policy({"tfa_grace_days": 0})
        assert sec.tfa_enrollment_due(admin) is True
        assert sec.tfa_enrollment_due({**admin, "totp_secret": "x"}) is False
        assert sec.tfa_enrollment_due(member) is False

    def test_widening_scope_signs_out_newly_covered_users(self, store):
        from modules.flow_gate.auth.session_store import create_session, get_session
        from modules.flow_gate.settings import security_policy_service as sec

        sid_dev, sid_admin = create_session("u_dev"), create_session("u_admin")
        result = sec.update_policy({"tfa_scope": "admin"})
        assert result["signed_out"] == 0  # logout_on_tighten is off by default
        result = sec.update_policy({"logout_on_tighten": True, "tfa_scope": "all"})
        assert result["signed_out"] == 3  # u_dev, u_view, u_free — admins were already covered
        assert get_session(sid_dev)["revoked_at"] is not None
        assert get_session(sid_admin)["revoked_at"] is None

    def test_coverage_lists_active_people(self, store):
        from modules.flow_gate.settings import security_policy_service as sec

        store._execute("UPDATE users SET is_active=0 WHERE user_id='u_free'")
        people = sec.coverage()
        assert {p["user_id"] for p in people} == {"u_admin", "u_admin2", "u_dev", "u_view"}
        assert people[0]["is_admin"] is True


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


class TestRoutes:
    def test_workspace_routes(self, store):
        client = _client({**ADMIN, "username": "admin"})
        assert client.get("/api/v1/workspaces").json()["workspaces"][0]["workspace_id"] == "ws_default"
        created = client.post("/api/v1/workspaces", json={"name": "Data Platform"})
        assert created.status_code == 201
        wid = created.json()["workspace_id"]
        assert client.post("/api/v1/workspaces", json={"name": "data platform"}).json()["detail"]["code"] == "duplicate_name"
        cands = client.get(f"/api/v1/workspaces/{wid}/member-candidates", params={"q": "dev"}).json()["users"]
        assert [c["user_id"] for c in cands] == ["u_dev"]
        assert client.post(f"/api/v1/workspaces/{wid}/members", json={"user_id": "u_dev", "role": "admin"}).status_code == 201
        assert client.patch(f"/api/v1/workspaces/{wid}/members/u_dev", json={"role": "member"}).status_code == 200
        assert client.put(f"/api/v1/workspaces/{wid}/access/defaults", json={"member": "review"}).json()["access"]["defaults"]["member"] == "review"
        assert client.put(f"/api/v1/workspaces/{wid}/policy", json={"ai_mode": "allow", "project_create": "all"}).json()["policy"] == {
            "ai_mode": "allow", "allowlist": [], "project_create": "all"}
        assert client.get(f"/api/v1/workspaces/{wid}/policy/resources").status_code == 200
        assert client.patch(f"/api/v1/workspaces/{wid}", json={"name": "Data"}).json()["name"] == "Data"
        assert client.delete(f"/api/v1/workspaces/{wid}").status_code == 409
        assert client.delete(f"/api/v1/workspaces/{wid}/members/u_dev").status_code == 200
        assert client.delete(f"/api/v1/workspaces/{wid}").status_code == 200
        assert client.get("/api/v1/workspaces/ws_nope").status_code == 404

    def test_member_cannot_reach_system_routes(self, store):
        client = _client({"user_id": "u_dev", "is_admin": 0, "is_active": 1})
        assert client.get("/api/v1/system/users").status_code == 403
        assert client.get("/api/v1/system/security").status_code == 403
        assert client.post("/api/v1/workspaces", json={"name": "X"}).status_code == 403

    def test_system_user_and_security_routes(self, store):
        client = _client({**ADMIN, "username": "admin"})
        listed = client.get("/api/v1/system/users").json()
        assert listed["summary"]["total"] == 5 and listed["workspaces"][0]["workspace_id"] == "ws_default"
        created = client.post("/api/v1/system/users", json={"name": "Lee Hana", "email": "hana@x.io", "start": "invite"})
        assert created.status_code == 201
        uid = created.json()["user"]["user_id"]
        token = created.json()["invite_path"].split("=", 1)[1]
        assert client.get("/auth/invite", params={"token": token}).json()["username"] == "Lee Hana"
        assert client.post("/auth/invite/accept", json={"token": token, "password": "x"}).status_code == 400
        assert client.post("/auth/invite/accept", json={"token": token, "password": "Str0ng-Passw0rd!"}).status_code == 200
        assert client.get("/auth/invite", params={"token": token}).status_code == 404
        assert client.put(f"/api/v1/system/users/{uid}/admin", json={"is_admin": True}).json()["is_admin"] is True
        assert client.post(f"/api/v1/system/users/{uid}/disable").json()["state"] == "disabled"
        assert client.post(f"/api/v1/system/users/{uid}/enable").json()["state"] != "disabled"
        assert client.put("/api/v1/system/users/u_admin/admin", json={"is_admin": False}).status_code == 200  # u_admin2 + new admin remain
        sec = client.get("/api/v1/system/security").json()
        assert sec["policy"]["tfa_scope"] == "off" and len(sec["people"]) == 6
        bad = client.put("/api/v1/system/security", json={"lock_attempts": 4})
        assert bad.status_code == 422
        saved = client.put("/api/v1/system/security", json={"tfa_scope": "all", "tfa_grace_days": 0}).json()
        assert saved["policy"]["tfa_scope"] == "all" and saved["signed_out"] == 0


class TestLoginEnrolment:
    def test_due_user_must_enrol_then_verifies_with_the_enrolment_token(self, store, monkeypatch):
        import base64
        import pyotp
        from modules.flow_gate.auth.password import hash_password
        from modules.flow_gate.settings import security_policy_service as sec

        monkeypatch.setenv("FLOWGATE_TOTP_ENCRYPT_KEY", base64.b64encode(b"k" * 32).decode())
        store._execute("UPDATE users SET password=? WHERE user_id='u_dev'", [hash_password("TestP@ssw0rd123")])
        client = _client({"user_id": "u_dev", "is_admin": 0, "is_active": 1})

        ok = client.post("/auth/login", json={"username": "dev", "password": "TestP@ssw0rd123"}).json()
        assert "access_token" in ok  # policy off: no enrolment
        last = store._fetch_one("SELECT last_login_at FROM users WHERE user_id='u_dev'")["last_login_at"]
        assert last

        sec.update_policy({"tfa_scope": "all", "tfa_grace_days": 0, "recovery_codes": False})
        step = client.post("/auth/login", json={"username": "dev", "password": "TestP@ssw0rd123"}).json()
        assert step == {"totp_setup_required": True, "temp_token": step["temp_token"]}
        enrol = client.post("/auth/totp/enroll", json={"temp_token": step["temp_token"]}).json()
        assert enrol["backup_codes"] == [] and enrol["qr_uri"].startswith("otpauth://")
        code = pyotp.TOTP(enrol["secret"]).now()
        tokens = client.post("/auth/totp", json={"temp_token": enrol["temp_token"], "code": code}).json()
        assert "access_token" in tokens
        assert client.post("/auth/totp/backup", json={"temp_token": step["temp_token"], "backup_code": "X"}).status_code == 403

    def test_interrupted_enrolment_saves_nothing_and_restarts_on_next_sign_in(self, store, monkeypatch):
        import base64
        import pyotp
        from modules.flow_gate.auth.backup_codes import count_unused
        from modules.flow_gate.auth.password import hash_password
        from modules.flow_gate.settings import security_policy_service as sec
        from modules.flow_gate.settings.system_user_service import get_user_view

        monkeypatch.setenv("FLOWGATE_TOTP_ENCRYPT_KEY", base64.b64encode(b"k" * 32).decode())
        store._execute("UPDATE users SET password=? WHERE user_id='u_dev'", [hash_password("TestP@ssw0rd123")])
        client = _client({"user_id": "u_dev", "is_admin": 0, "is_active": 1})
        sec.update_policy({"tfa_scope": "all", "tfa_grace_days": 0})
        login = {"username": "dev", "password": "TestP@ssw0rd123"}

        def saved_secret():
            return store._fetch_one("SELECT totp_secret FROM users WHERE user_id='u_dev'")["totp_secret"]

        def clear_failures():
            store._execute("UPDATE users SET totp_failed_count=0 WHERE user_id='u_dev'")

        # First attempt: the key and backup codes are shown, then the user leaves (closes the page,
        # presses Back or never receives the response) without confirming a code.
        step = client.post("/auth/login", json=login).json()
        first = client.post("/auth/totp/enroll", json={"temp_token": step["temp_token"]}).json()
        assert len(first["backup_codes"]) == 10 and first["temp_token"] != step["temp_token"]
        assert saved_secret() is None and count_unused("u_dev") == 0
        assert get_user_view("u_dev")["tfa_enabled"] is False
        assert next(p for p in sec.coverage() if p["user_id"] == "u_dev")["tfa_enabled"] is False
        # A backup code cannot stand in for the unconfirmed enrolment, and a wrong code saves nothing.
        backup = client.post("/auth/totp/backup", json={"temp_token": first["temp_token"], "backup_code": first["backup_codes"][0]})
        assert backup.status_code == 409 and backup.json()["detail"] == "totp_not_enrolled"
        assert client.post("/auth/totp", json={"temp_token": first["temp_token"], "code": "000000"}).status_code == 401
        assert saved_secret() is None
        clear_failures()

        # Next sign-in asks for enrolment again (not for a code) and issues a new key.
        again = client.post("/auth/login", json=login).json()
        assert again == {"totp_setup_required": True, "temp_token": again["temp_token"]}
        second = client.post("/auth/totp/enroll", json={"temp_token": again["temp_token"]}).json()
        assert second["secret"] != first["secret"]
        # The sign-in token carries no enrolment, and the abandoned key does not confirm the new one.
        assert client.post("/auth/totp", json={"temp_token": again["temp_token"], "code": pyotp.TOTP(second["secret"]).now()}).status_code == 401
        assert client.post("/auth/totp", json={"temp_token": second["temp_token"], "code": pyotp.TOTP(first["secret"]).now()}).status_code == 401
        assert saved_secret() is None
        clear_failures()

        tokens = client.post("/auth/totp", json={"temp_token": second["temp_token"], "code": pyotp.TOTP(second["secret"]).now()}).json()
        assert "access_token" in tokens
        assert saved_secret() and count_unused("u_dev") == 10
        assert get_user_view("u_dev")["tfa_enabled"] is True

        # Enrolled now: sign-in asks for the code, a new enrolment is refused, and only the backup
        # codes of the confirmed enrolment work.
        step = client.post("/auth/login", json=login).json()
        assert step == {"totp_required": True, "temp_token": step["temp_token"]}
        assert client.post("/auth/totp/enroll", json={"temp_token": step["temp_token"]}).status_code == 409
        assert client.post("/auth/totp/backup", json={"temp_token": step["temp_token"], "backup_code": first["backup_codes"][0]}).status_code == 401
        assert "access_token" in client.post("/auth/totp/backup", json={"temp_token": step["temp_token"], "backup_code": second["backup_codes"][0]}).json()

    def test_lockout_follows_the_policy(self, store):
        from modules.flow_gate.auth.password import hash_password
        from modules.flow_gate.settings import security_policy_service as sec

        store._execute("UPDATE users SET password=? WHERE user_id='u_view'", [hash_password("TestP@ssw0rd123")])
        sec.update_policy({"lock_attempts": 3, "lock_minutes": 5})
        client = _client({"user_id": "u_view", "is_admin": 0, "is_active": 1})
        for _ in range(3):
            assert client.post("/auth/login", json={"username": "viewer", "password": "bad"}).status_code == 400
        assert client.post("/auth/login", json={"username": "viewer", "password": "TestP@ssw0rd123"}).status_code == 423
        until = store._fetch_one("SELECT login_locked_until FROM users WHERE user_id='u_view'")["login_locked_until"]
        delta = datetime.fromisoformat(until) - datetime.now(timezone.utc)
        assert timedelta(minutes=3) < delta <= timedelta(minutes=5)

    def test_idle_session_is_signed_out_on_refresh(self, store):
        from modules.flow_gate.auth.auth_api import _session_idle
        from modules.flow_gate.settings import security_policy_service as sec

        sec.update_policy({"idle_timeout": "30m"})
        old = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat(timespec="seconds")
        fresh = datetime.now(timezone.utc).isoformat(timespec="seconds")
        assert _session_idle({"last_used_at": old}) is True
        assert _session_idle({"last_used_at": fresh}) is False
