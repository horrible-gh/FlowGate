"""flowgate.v02.0009 T#2: env-vars/commands mutation permission regression tests.

Before this fix, every route in env_vars_commands.py (list/get, create/update/delete,
resolve, and execute) depended only on `_require_settings_read`
(project.settings.read or is_admin). Because Environment Variables and Commands have
no project_id — the whole page lives under Settings > System, not a project — any
user who could merely view the page could also create/update/delete variables and
commands, or run an arbitrary shell command through `execute_command`
(subprocess.run(..., shell=True)).

The fix adds `_require_settings_edit` (system.settings.manage, matching the same
System edit/manage policy already used by ai_settings.py / system.py /
project_settings.py's global-mode routes) and applies it to every mutating route,
while list/get/resolve keep the original read-only dependency.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))


@pytest.fixture(scope="module")
def test_db_path(migrated_sqlite_db):
    """Shared migration-replay factory (flowgate.default.0394 T0010 / NR0003 §13-6)."""
    return migrated_sqlite_db(
        "test_env_vars_commands_permission_0009.db",
        seed_sql="""
        INSERT OR IGNORE INTO roles(role_id,role_name,is_system,created_at,updated_at)
            VALUES('role_editor_0009','Editor',1,datetime('now'),datetime('now')),
                  ('role_reader_0009','Reader',1,datetime('now'),datetime('now'));
        INSERT OR IGNORE INTO permissions(permission_id,permission_name,created_at)
            VALUES
                ('system.settings.manage','System settings management',datetime('now')),
                ('project.settings.read','Project settings read',datetime('now'));
        INSERT OR IGNORE INTO role_permissions(role_id,permission_id)
            VALUES
                ('role_editor_0009','system.settings.manage'),
                ('role_reader_0009','project.settings.read');
        INSERT OR IGNORE INTO projects(project_id,project_name,is_active,created_at,updated_at)
            VALUES('__SYSTEM__','[System]',1,datetime('now'),datetime('now'));
        INSERT OR IGNORE INTO users(user_id,username,email,password,is_active,is_admin,first_login_required,created_at,updated_at)
            VALUES
                ('usr_admin_0009','admin0009','admin0009@test.com','hashed_pw',1,1,0,datetime('now'),datetime('now')),
                ('usr_editor_0009','editor0009','editor0009@test.com','hashed_pw',1,0,0,datetime('now'),datetime('now')),
                ('usr_reader_0009','reader0009','reader0009@test.com','hashed_pw',1,0,0,datetime('now'),datetime('now')),
                ('usr_none_0009','none0009','none0009@test.com','hashed_pw',1,0,0,datetime('now'),datetime('now'));
        INSERT OR IGNORE INTO user_project_roles(user_id,project_id,role_id,granted_at)
            VALUES
                ('usr_editor_0009','__SYSTEM__','role_editor_0009',datetime('now')),
                ('usr_reader_0009','__SYSTEM__','role_reader_0009',datetime('now'));
        """,
    )


@pytest.fixture(autouse=True)
def mock_db(test_db_path):
    """Point every module's bound `get_store` at a real sqlite file (same technique
    as test_settings_api.py's mock_db), plus the two modules this router actually
    reads/writes through: db.env_vars and db.commands."""
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

    store = TestStore(test_db_path)
    import importlib

    import modules.flow_gate.db.connection as _conn
    _real_get_store = _conn.get_store
    _modules = [
        importlib.import_module(_name)
        for _name in (
            "modules.flow_gate.db.connection",
            "modules.flow_gate.db.env_vars",
            "modules.flow_gate.db.commands",
            "modules.flow_gate.rbac.decorators",
            "modules.flow_gate.rbac.permission_service",
        )
    ]
    for _m in _modules:
        _m.get_store = lambda store=store: store

    from modules.flow_gate.rbac import permission_service as _perm_svc
    _perm_svc.invalidate_all()
    try:
        yield store
    finally:
        _perm_svc.invalidate_all()
        for _m in _modules:
            _m.get_store = _real_get_store
        store._conn.close()


def _make_client(user):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.settings.routers.env_vars_commands import router
    from modules.flow_gate.auth.middleware import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


ADMIN = {"user_id": "usr_admin_0009", "is_admin": 1}
EDITOR = {"user_id": "usr_editor_0009", "is_admin": 0}
READER = {"user_id": "usr_reader_0009", "is_admin": 0}
NONE_USER = {"user_id": "usr_none_0009", "is_admin": 0}


class TestEnvVarPermissions:
    def test_reader_can_list(self, mock_db):
        client = _make_client(READER)
        resp = client.get("/api/v1/env-vars")
        assert resp.status_code == 200
        assert resp.json() == {"env_vars": []}

    def test_no_permission_user_cannot_list(self, mock_db):
        client = _make_client(NONE_USER)
        resp = client.get("/api/v1/env-vars")
        assert resp.status_code == 403

    def test_reader_cannot_create(self, mock_db):
        client = _make_client(READER)
        resp = client.post("/api/v1/env-vars", json={"kind": "user", "name": "FOO", "value": "1"})
        assert resp.status_code == 403

    def test_editor_can_create_user_kind(self, mock_db):
        client = _make_client(EDITOR)
        resp = client.post("/api/v1/env-vars", json={"kind": "user", "name": "FOO", "value": "1"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "FOO"
        assert body["kind"] == "user"
        assert body["value"] == "1"

    def test_editor_cannot_create_system_kind(self, mock_db):
        """T#2 §2: system-kind mutation still needs is_admin, on top of the new edit gate."""
        client = _make_client(EDITOR)
        resp = client.post("/api/v1/env-vars", json={"kind": "system", "name": "BAR", "value": "1"})
        assert resp.status_code == 403
        assert "admins" in resp.json()["detail"].lower()

    def test_admin_can_create_system_kind(self, mock_db):
        client = _make_client(ADMIN)
        resp = client.post("/api/v1/env-vars", json={"kind": "system", "name": "BAR", "value": "1"})
        assert resp.status_code == 201
        assert resp.json()["kind"] == "system"

    def test_reader_cannot_update(self, mock_db):
        editor_client = _make_client(EDITOR)
        created = editor_client.post(
            "/api/v1/env-vars", json={"kind": "user", "name": "UPDATABLE", "value": "orig"}
        ).json()

        reader_client = _make_client(READER)
        resp = reader_client.put(f"/api/v1/env-vars/{created['var_id']}", json={"value": "hacked"})
        assert resp.status_code == 403

        # unchanged despite the rejected attempt
        row = reader_client.get("/api/v1/env-vars").json()["env_vars"]
        assert next(r for r in row if r["var_id"] == created["var_id"])["value"] == "orig"

    def test_editor_can_update(self, mock_db):
        editor_client = _make_client(EDITOR)
        created = editor_client.post(
            "/api/v1/env-vars", json={"kind": "user", "name": "UPDATABLE2", "value": "orig"}
        ).json()

        resp = editor_client.put(f"/api/v1/env-vars/{created['var_id']}", json={"value": "changed"})
        assert resp.status_code == 200
        assert resp.json()["value"] == "changed"

    def test_reader_cannot_delete(self, mock_db):
        editor_client = _make_client(EDITOR)
        created = editor_client.post(
            "/api/v1/env-vars", json={"kind": "user", "name": "DELETABLE", "value": "x"}
        ).json()

        reader_client = _make_client(READER)
        resp = reader_client.delete(f"/api/v1/env-vars/{created['var_id']}")
        assert resp.status_code == 403

        rows = reader_client.get("/api/v1/env-vars").json()["env_vars"]
        assert any(r["var_id"] == created["var_id"] for r in rows)

    def test_editor_can_delete(self, mock_db):
        editor_client = _make_client(EDITOR)
        created = editor_client.post(
            "/api/v1/env-vars", json={"kind": "user", "name": "DELETABLE2", "value": "x"}
        ).json()

        resp = editor_client.delete(f"/api/v1/env-vars/{created['var_id']}")
        assert resp.status_code == 200
        assert resp.json() == {"detail": "deleted"}

    def test_create_duplicate_name_still_returns_409_for_editor(self, mock_db):
        """Business-error semantics (duplicate name) must not change with the permission fix."""
        client = _make_client(EDITOR)
        client.post("/api/v1/env-vars", json={"kind": "user", "name": "DUPTEST", "value": "1"})
        resp = client.post("/api/v1/env-vars", json={"kind": "user", "name": "DUPTEST", "value": "2"})
        assert resp.status_code == 409

    def test_update_missing_var_still_returns_404_for_editor(self, mock_db):
        client = _make_client(EDITOR)
        resp = client.put("/api/v1/env-vars/does-not-exist", json={"value": "x"})
        assert resp.status_code == 404

    def test_admin_can_update_system_kind(self, mock_db):
        client = _make_client(ADMIN)
        created = client.post(
            "/api/v1/env-vars", json={"kind": "system", "name": "SYS_UPDATABLE", "value": "orig"}
        ).json()

        resp = client.put(f"/api/v1/env-vars/{created['var_id']}", json={"value": "changed"})
        assert resp.status_code == 200
        assert resp.json()["value"] == "changed"

    def test_admin_can_delete_system_kind(self, mock_db):
        client = _make_client(ADMIN)
        created = client.post(
            "/api/v1/env-vars", json={"kind": "system", "name": "SYS_DELETABLE", "value": "x"}
        ).json()

        resp = client.delete(f"/api/v1/env-vars/{created['var_id']}")
        assert resp.status_code == 200
        assert resp.json() == {"detail": "deleted"}


class TestCommandPermissions:
    def test_reader_can_list_and_resolve(self, mock_db):
        editor_client = _make_client(EDITOR)
        created = editor_client.post(
            "/api/v1/commands", json={"kind": "user", "name": "greet", "template": "echo hi"}
        ).json()

        reader_client = _make_client(READER)
        list_resp = reader_client.get("/api/v1/commands")
        assert list_resp.status_code == 200

        resolve_resp = reader_client.post(f"/api/v1/commands/{created['command_id']}/resolve")
        assert resolve_resp.status_code == 200
        assert resolve_resp.json()["resolved"] == "echo hi"

    def test_reader_cannot_create(self, mock_db):
        client = _make_client(READER)
        resp = client.post("/api/v1/commands", json={"kind": "user", "name": "blocked", "template": "echo no"})
        assert resp.status_code == 403

    def test_editor_can_create_update_delete(self, mock_db):
        client = _make_client(EDITOR)
        created = client.post(
            "/api/v1/commands", json={"kind": "user", "name": "cmdcrud", "template": "echo one"}
        )
        assert created.status_code == 201
        command_id = created.json()["command_id"]

        updated = client.put(f"/api/v1/commands/{command_id}", json={"template": "echo two"})
        assert updated.status_code == 200
        assert updated.json()["template"] == "echo two"

        deleted = client.delete(f"/api/v1/commands/{command_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"detail": "deleted"}

    def test_editor_cannot_create_system_kind(self, mock_db):
        client = _make_client(EDITOR)
        resp = client.post("/api/v1/commands", json={"kind": "system", "name": "syscmd", "template": "echo sys"})
        assert resp.status_code == 403

    def test_reader_cannot_update(self, mock_db):
        editor_client = _make_client(EDITOR)
        created = editor_client.post(
            "/api/v1/commands", json={"kind": "user", "name": "cmd_updatable", "template": "echo orig"}
        ).json()

        reader_client = _make_client(READER)
        resp = reader_client.put(f"/api/v1/commands/{created['command_id']}", json={"template": "echo hacked"})
        assert resp.status_code == 403

        # unchanged despite the rejected attempt
        rows = reader_client.get("/api/v1/commands").json()["commands"]
        assert next(r for r in rows if r["command_id"] == created["command_id"])["template"] == "echo orig"

    def test_reader_cannot_delete(self, mock_db):
        editor_client = _make_client(EDITOR)
        created = editor_client.post(
            "/api/v1/commands", json={"kind": "user", "name": "cmd_deletable", "template": "echo x"}
        ).json()

        reader_client = _make_client(READER)
        resp = reader_client.delete(f"/api/v1/commands/{created['command_id']}")
        assert resp.status_code == 403

        rows = reader_client.get("/api/v1/commands").json()["commands"]
        assert any(r["command_id"] == created["command_id"] for r in rows)

    def test_admin_can_create_update_delete_system_kind(self, mock_db):
        """T#2 §4 rework: prove admin succeeds across the full CRUD surface for a
        kind='system' command, not just a single create — mirrors the env-var
        coverage in test_admin_can_update_system_kind/test_admin_can_delete_system_kind."""
        client = _make_client(ADMIN)
        created = client.post(
            "/api/v1/commands", json={"kind": "system", "name": "syscmd_admin", "template": "echo sys1"}
        )
        assert created.status_code == 201
        assert created.json()["kind"] == "system"
        command_id = created.json()["command_id"]

        updated = client.put(f"/api/v1/commands/{command_id}", json={"template": "echo sys2"})
        assert updated.status_code == 200
        assert updated.json()["template"] == "echo sys2"

        deleted = client.delete(f"/api/v1/commands/{command_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"detail": "deleted"}

    def test_reader_cannot_execute(self, mock_db):
        """The core regression this fix closes: running a saved command is a mutation
        (subprocess.run with shell=True), not a read — it must not succeed on read
        permission alone."""
        editor_client = _make_client(EDITOR)
        created = editor_client.post(
            "/api/v1/commands", json={"kind": "user", "name": "exec_guard", "template": "echo hi"}
        ).json()

        reader_client = _make_client(READER)
        resp = reader_client.post(f"/api/v1/commands/{created['command_id']}/execute")
        assert resp.status_code == 403

    def test_no_permission_user_cannot_execute(self, mock_db):
        editor_client = _make_client(EDITOR)
        created = editor_client.post(
            "/api/v1/commands", json={"kind": "user", "name": "exec_guard2", "template": "echo hi"}
        ).json()

        none_client = _make_client(NONE_USER)
        resp = none_client.post(f"/api/v1/commands/{created['command_id']}/execute")
        assert resp.status_code == 403

    def test_editor_can_execute(self, mock_db):
        client = _make_client(EDITOR)
        created = client.post(
            "/api/v1/commands", json={"kind": "user", "name": "exec_ok", "template": "echo hi"}
        ).json()

        resp = client.post(f"/api/v1/commands/{created['command_id']}/execute")
        assert resp.status_code == 200
        body = resp.json()
        assert body["return_code"] == 0
        assert "hi" in body["stdout"].lower()
        # request/response contract unchanged: same keys as before the permission fix
        assert set(body.keys()) == {
            "command_id", "resolved", "stdout", "stderr", "return_code", "executed_at",
        }

    def test_admin_can_execute(self, mock_db):
        client = _make_client(EDITOR)
        created = client.post(
            "/api/v1/commands", json={"kind": "user", "name": "exec_admin", "template": "echo hi"}
        ).json()

        admin_client = _make_client(ADMIN)
        resp = admin_client.post(f"/api/v1/commands/{created['command_id']}/execute")
        assert resp.status_code == 200

    def test_admin_can_execute_system_kind(self, mock_db):
        """The user-kind case above doesn't prove admin can run a kind='system'
        command — the is_admin gate is re-checked per route, so cover it directly."""
        client = _make_client(ADMIN)
        created = client.post(
            "/api/v1/commands", json={"kind": "system", "name": "syscmd_exec", "template": "echo hi"}
        ).json()

        resp = client.post(f"/api/v1/commands/{created['command_id']}/execute")
        assert resp.status_code == 200
        assert "hi" in resp.json()["stdout"].lower()

    def test_execute_missing_command_still_returns_404_for_editor(self, mock_db):
        client = _make_client(EDITOR)
        resp = client.post("/api/v1/commands/does-not-exist/execute")
        assert resp.status_code == 404

    def test_create_duplicate_name_still_returns_409_for_editor(self, mock_db):
        client = _make_client(EDITOR)
        client.post("/api/v1/commands", json={"kind": "user", "name": "DUPCMD", "template": "echo a"})
        resp = client.post("/api/v1/commands", json={"kind": "user", "name": "DUPCMD", "template": "echo b"})
        assert resp.status_code == 409
