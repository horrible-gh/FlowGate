"""flowgate.default.0623 T0004: server contracts for the v0.2 general settings screens.

1. GET /rbac/system/my-permissions — current user's system-scope permissions.
   Admins hold everything; non-admins get only roles granted on the system scope,
   never project roles, and the internal __SYSTEM__ sentinel is not in the body.
2. Env Variables / Commands — read (list, resolve) keeps project.settings.read;
   create/update/delete and execute need project.settings.edit; kind=system stays
   admin-only.
3. GET /system/storage-root and /projects/{id}/storage-root — stored vs effective
   storage root, resolved by storage.paths (the same SSOT as get_storage_root()).

Runs against the real migrated sqlite schema and the real permission SQL.
db.connection.STORE is swapped (not get_store) so modules imported mid-test keep
the real function.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))


_SEED_SQL = """
INSERT OR IGNORE INTO projects(project_id,project_name,is_active,created_at,updated_at)
    VALUES('__SYSTEM__','[System]',1,datetime('now'),datetime('now')),
          ('proj_a','ProjectA',1,datetime('now'),datetime('now')),
          ('proj_b','ProjectB',1,datetime('now'),datetime('now'));
INSERT OR IGNORE INTO users(user_id,username,email,password,is_active,is_admin,first_login_required,created_at,updated_at)
    VALUES('usr_admin','admin','admin@test.com','x',1,1,0,datetime('now'),datetime('now')),
          ('usr_sys_editor','sys_editor','se@test.com','x',1,0,0,datetime('now'),datetime('now')),
          ('usr_sys_reader','sys_reader','sr@test.com','x',1,0,0,datetime('now'),datetime('now')),
          ('usr_proj_only','proj_only','po@test.com','x',1,0,0,datetime('now'),datetime('now')),
          ('usr_none','none','no@test.com','x',1,0,0,datetime('now'),datetime('now'));
INSERT OR IGNORE INTO permissions(permission_id,permission_name,created_at)
    VALUES('system.settings.manage','t0623 manage system settings',datetime('now')),
          ('project.settings.read','t0623 read settings',datetime('now')),
          ('project.settings.edit','t0623 edit settings',datetime('now'));
INSERT OR IGNORE INTO roles(role_id,role_name,is_system,created_at,updated_at)
    VALUES('t0623_sys_editor','t0623 sys editor',0,datetime('now'),datetime('now')),
          ('t0623_sys_reader','t0623 sys reader',0,datetime('now'),datetime('now')),
          ('t0623_proj_all','t0623 project all',0,datetime('now'),datetime('now'));
INSERT OR IGNORE INTO role_permissions(role_id,permission_id)
    VALUES('t0623_sys_editor','project.settings.read'),
          ('t0623_sys_editor','project.settings.edit'),
          ('t0623_sys_reader','project.settings.read'),
          ('t0623_proj_all','project.settings.read'),
          ('t0623_proj_all','project.settings.edit'),
          ('t0623_proj_all','system.settings.manage');
INSERT OR IGNORE INTO user_project_roles(user_id,project_id,role_id,granted_at)
    VALUES('usr_sys_editor','__SYSTEM__','t0623_sys_editor',datetime('now')),
          ('usr_sys_reader','__SYSTEM__','t0623_sys_reader',datetime('now')),
          ('usr_proj_only','proj_a','t0623_proj_all',datetime('now'));
"""

ADMIN = {"user_id": "usr_admin", "is_admin": 1}
SYS_EDITOR = {"user_id": "usr_sys_editor", "is_admin": 0}
SYS_READER = {"user_id": "usr_sys_reader", "is_admin": 0}
PROJ_ONLY = {"user_id": "usr_proj_only", "is_admin": 0}
NOBODY = {"user_id": "usr_none", "is_admin": 0}


@pytest.fixture(scope="module")
def test_db_path(migrated_sqlite_db):
    return migrated_sqlite_db("test_general_settings_server_contract_0623.db", seed_sql=_SEED_SQL)


class _Store:
    def __init__(self, db_path):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def _execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def _fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def transaction(self):
        yield self


@pytest.fixture(autouse=True)
def store(test_db_path, monkeypatch):
    import modules.flow_gate.db.connection as _conn
    from modules.flow_gate.rbac import permission_service

    s = _Store(test_db_path)
    monkeypatch.setattr(_conn, "STORE", s)
    assert _conn.get_store() is s
    permission_service.clear_all_cache()
    s._execute("DELETE FROM env_variables")
    s._execute("DELETE FROM commands")
    s._execute("DELETE FROM system_settings WHERE setting_key = 'storage_root'")
    s._execute("DELETE FROM project_settings WHERE project_id IN ('proj_a', 'proj_b')")
    monkeypatch.delenv("FLOWGATE_STORAGE_DIR", raising=False)
    yield s
    permission_service.clear_all_cache()
    # A rejected INSERT (the 409 path) leaves an implicit transaction open; closing
    # drops it so the next test's connection is not locked out.
    s._conn.close()


def _client(user):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.rbac.routers import router as rbac_router
    from modules.flow_gate.settings.routers.env_vars_commands import router as ev_router
    from modules.flow_gate.settings.routers.project_settings import router as project_router
    from modules.flow_gate.settings.routers.system import router as system_router

    app = FastAPI()
    app.include_router(rbac_router, prefix="/rbac")
    app.include_router(ev_router, prefix="/api/v1")
    app.include_router(system_router, prefix="/api/v1")
    app.include_router(project_router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


# ── 1. System permission contract ─────────────────────────────────────────────

class TestSystemMyPermissions:
    def test_admin_holds_every_permission(self, store):
        body = _client(ADMIN).get("/rbac/system/my-permissions").json()
        every = sorted(r["permission_id"] for r in store._fetch_all("SELECT permission_id FROM permissions"))
        assert body == {"scope": "system", "permissions": every, "is_admin": True}
        assert "system.settings.manage" in body["permissions"]

    def test_non_admin_with_system_role_gets_only_that_role(self):
        body = _client(SYS_EDITOR).get("/rbac/system/my-permissions").json()
        assert body == {
            "scope": "system",
            "permissions": ["project.settings.edit", "project.settings.read"],
            "is_admin": False,
        }

    def test_non_admin_without_system_role_gets_nothing(self):
        body = _client(NOBODY).get("/rbac/system/my-permissions").json()
        assert body == {"scope": "system", "permissions": [], "is_admin": False}

    def test_project_role_does_not_leak_into_system_scope(self):
        client = _client(PROJ_ONLY)
        system = client.get("/rbac/system/my-permissions").json()
        assert system["permissions"] == []
        assert "system.settings.manage" not in system["permissions"]
        # ...while the project endpoint still reports that project's role unchanged.
        project = client.get("/rbac/projects/proj_a/my-permissions").json()
        assert project == {
            "project_id": "proj_a",
            "permissions": ["project.settings.edit", "project.settings.read", "system.settings.manage"],
            "is_admin": False,
        }

    def test_system_role_still_counts_in_project_endpoint(self):
        # The existing project contract sums project + system roles; unchanged.
        body = _client(SYS_READER).get("/rbac/projects/proj_b/my-permissions").json()
        assert body["permissions"] == ["project.settings.read"]

    def test_sentinel_not_exposed(self):
        for user in (ADMIN, SYS_EDITOR, NOBODY):
            resp = _client(user).get("/rbac/system/my-permissions")
            assert resp.status_code == 200
            assert "__SYSTEM__" not in resp.text

    def test_matches_permission_service_ssot(self):
        from modules.flow_gate.rbac import permission_service

        body = _client(SYS_READER).get("/rbac/system/my-permissions").json()
        assert body["permissions"] == sorted(
            permission_service.get_user_permissions("usr_sys_reader", "__SYSTEM__")
        )


# ── 2. Env Variables / Commands permissions ──────────────────────────────────

def _seed_env(store, kind="user", name="FOO", value="bar"):
    from modules.flow_gate.db import env_vars

    return env_vars.create({"kind": kind, "name": name, "value": value})


def _seed_cmd(store, kind="user", name="hello", template="echo hello"):
    from modules.flow_gate.db import commands

    return commands.create({"kind": kind, "name": name, "template": template})


class TestEnvVarsPermissions:
    def test_reader_can_list(self, store):
        _seed_env(store)
        resp = _client(SYS_READER).get("/api/v1/env-vars")
        assert resp.status_code == 200
        assert [r["name"] for r in resp.json()["env_vars"]] == ["FOO"]

    def test_reader_cannot_mutate(self, store):
        row = _seed_env(store)
        client = _client(SYS_READER)
        assert client.post("/api/v1/env-vars", json={"name": "NEW", "value": "1"}).status_code == 403
        assert client.put(f"/api/v1/env-vars/{row['var_id']}", json={"value": "x"}).status_code == 403
        assert client.delete(f"/api/v1/env-vars/{row['var_id']}").status_code == 403
        assert store._fetch_one("SELECT value FROM env_variables WHERE var_id = ?", [row["var_id"]])["value"] == "bar"
        assert store._fetch_one("SELECT 1 AS x FROM env_variables WHERE name = 'NEW'") is None

    def test_no_permission_cannot_read(self):
        assert _client(NOBODY).get("/api/v1/env-vars").status_code == 403

    def test_project_role_does_not_grant_env_mutation(self):
        resp = _client(PROJ_ONLY).post("/api/v1/env-vars", json={"name": "NEW", "value": "1"})
        assert resp.status_code == 403

    def test_editor_can_mutate(self, store):
        client = _client(SYS_EDITOR)
        created = client.post("/api/v1/env-vars", json={"name": "NEW", "value": "1"})
        assert created.status_code == 201
        var_id = created.json()["var_id"]
        updated = client.put(f"/api/v1/env-vars/{var_id}", json={"value": "2"})
        assert updated.status_code == 200 and updated.json()["value"] == "2"
        assert client.delete(f"/api/v1/env-vars/{var_id}").json() == {"detail": "deleted"}
        assert store._fetch_one("SELECT 1 AS x FROM env_variables WHERE var_id = ?", [var_id]) is None

    def test_editor_still_blocked_on_system_kind(self, store):
        row = _seed_env(store, kind="system", name="SYS")
        client = _client(SYS_EDITOR)
        assert client.post("/api/v1/env-vars", json={"kind": "system", "name": "S2"}).status_code == 403
        assert client.put(f"/api/v1/env-vars/{row['var_id']}", json={"value": "x"}).status_code == 403
        assert client.delete(f"/api/v1/env-vars/{row['var_id']}").status_code == 403
        assert client.get("/api/v1/env-vars?include_system=true").status_code == 403

    def test_admin_can_mutate_system_kind(self, store):
        client = _client(ADMIN)
        created = client.post("/api/v1/env-vars", json={"kind": "system", "name": "S2", "value": "v"})
        assert created.status_code == 201
        assert client.delete(f"/api/v1/env-vars/{created.json()['var_id']}").status_code == 200

    def test_existing_404_and_409_unchanged_for_editor(self, store):
        _seed_env(store)
        client = _client(SYS_EDITOR)
        assert client.put("/api/v1/env-vars/ev-missing", json={"value": "x"}).status_code == 404
        assert client.delete("/api/v1/env-vars/ev-missing").status_code == 404
        assert client.post("/api/v1/env-vars", json={"name": "FOO"}).status_code == 409


class TestCommandsPermissions:
    def test_reader_can_list_and_resolve(self, store):
        row = _seed_cmd(store)
        client = _client(SYS_READER)
        listed = client.get("/api/v1/commands")
        assert listed.status_code == 200
        assert [r["name"] for r in listed.json()["commands"]] == ["hello"]
        resolved = client.post(f"/api/v1/commands/{row['command_id']}/resolve")
        assert resolved.status_code == 200
        assert resolved.json()["resolved"] == "echo hello"

    def test_reader_cannot_create_update_delete(self, store):
        row = _seed_cmd(store)
        client = _client(SYS_READER)
        assert client.post("/api/v1/commands", json={"name": "c2", "template": "echo 2"}).status_code == 403
        assert client.put(f"/api/v1/commands/{row['command_id']}", json={"template": "echo x"}).status_code == 403
        assert client.delete(f"/api/v1/commands/{row['command_id']}").status_code == 403
        assert store._fetch_one(
            "SELECT template FROM commands WHERE command_id = ?", [row["command_id"]]
        )["template"] == "echo hello"

    def test_reader_cannot_execute(self, store, monkeypatch):
        row = _seed_cmd(store)
        ran = []
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
        assert _client(SYS_READER).post(f"/api/v1/commands/{row['command_id']}/execute").status_code == 403
        assert ran == []

    def test_no_permission_cannot_resolve(self, store):
        row = _seed_cmd(store)
        assert _client(NOBODY).post(f"/api/v1/commands/{row['command_id']}/resolve").status_code == 403

    def test_editor_can_create_update_delete(self, store):
        client = _client(SYS_EDITOR)
        created = client.post("/api/v1/commands", json={"name": "c2", "template": "echo 2"})
        assert created.status_code == 201
        cid = created.json()["command_id"]
        assert client.put(f"/api/v1/commands/{cid}", json={"template": "echo 3"}).json()["template"] == "echo 3"
        assert client.delete(f"/api/v1/commands/{cid}").json() == {"detail": "deleted"}

    def test_editor_execute_response_unchanged(self, store):
        row = _seed_cmd(store, template="echo t0623")
        resp = _client(SYS_EDITOR).post(f"/api/v1/commands/{row['command_id']}/execute", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"command_id", "resolved", "stdout", "stderr", "return_code", "executed_at"}
        assert body["command_id"] == row["command_id"]
        assert body["resolved"] == "echo t0623"
        assert body["return_code"] == 0
        assert "t0623" in body["stdout"]

    def test_editor_execute_timeout_still_504(self, store, monkeypatch):
        row = _seed_cmd(store)

        def _timeout(*_a, **_k):
            raise subprocess.TimeoutExpired(cmd="echo hello", timeout=60)

        monkeypatch.setattr(subprocess, "run", _timeout)
        resp = _client(SYS_EDITOR).post(f"/api/v1/commands/{row['command_id']}/execute")
        assert resp.status_code == 504
        assert resp.json()["detail"] == {"detail": "command timed out after 60s", "resolved": "echo hello"}

    def test_editor_still_blocked_on_system_kind(self, store):
        row = _seed_cmd(store, kind="system", name="sys_cmd")
        client = _client(SYS_EDITOR)
        assert client.post("/api/v1/commands", json={"kind": "system", "name": "s2", "template": "x"}).status_code == 403
        assert client.put(f"/api/v1/commands/{row['command_id']}", json={"template": "x"}).status_code == 403
        assert client.delete(f"/api/v1/commands/{row['command_id']}").status_code == 403

    def test_project_role_does_not_grant_execute(self, store):
        row = _seed_cmd(store)
        assert _client(PROJ_ONLY).post(f"/api/v1/commands/{row['command_id']}/execute").status_code == 403

    def test_admin_bypass_kept(self, store):
        row = _seed_cmd(store)
        client = _client(ADMIN)
        assert client.put(f"/api/v1/commands/{row['command_id']}", json={"template": "echo a"}).status_code == 200
        assert client.post(f"/api/v1/commands/{row['command_id']}/execute").status_code == 200


# ── 3. Storage root stored vs effective ──────────────────────────────────────

def _set_system_root(store, value):
    store._execute(
        "INSERT INTO system_settings(setting_key,setting_value,value_type,updated_at) "
        "VALUES('storage_root', ?, 'string', datetime('now'))",
        [value],
    )


def _set_project_override(store, project_id, value):
    store._execute(
        "INSERT INTO project_settings(project_id, storage_root_override, updated_at) "
        "VALUES(?, ?, datetime('now'))",
        [project_id, value],
    )


class TestStorageRootContract:
    def test_stored_value_is_effective(self, store, tmp_path):
        from modules.flow_gate.storage.paths import get_storage_root

        root = str(tmp_path / "sysroot")
        _set_system_root(store, root)
        body = _client(ADMIN).get("/api/v1/system/storage-root").json()
        assert body == {
            "project_id": None,
            "configured_value": root,
            "project_override": None,
            "effective_value": str(get_storage_root()),
            "effective_source": "system_setting",
            "env_override": False,
        }
        assert body["effective_value"] == str(Path(root))

    def test_missing_row_reports_null_and_default(self):
        from modules.flow_gate.storage.paths import default_storage_root

        body = _client(ADMIN).get("/api/v1/system/storage-root").json()
        assert body["configured_value"] is None
        assert body["effective_source"] == "default"
        assert body["effective_value"] == str(default_storage_root())

    def test_blank_value_reports_null_not_empty_string(self, store):
        from modules.flow_gate.storage.paths import default_storage_root

        _set_system_root(store, "   ")
        body = _client(ADMIN).get("/api/v1/system/storage-root").json()
        assert body["configured_value"] is None
        assert body["effective_value"] == str(default_storage_root())
        assert body["effective_source"] == "default"

    def test_env_overrides_stored_value(self, store, tmp_path, monkeypatch):
        _set_system_root(store, str(tmp_path / "sysroot"))
        monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path / "envroot"))
        body = _client(ADMIN).get("/api/v1/system/storage-root").json()
        assert body["configured_value"] == str(tmp_path / "sysroot")
        assert body["effective_value"] == str(tmp_path / "envroot")
        assert body["effective_source"] == "env"
        assert body["env_override"] is True

    def test_system_endpoint_needs_system_settings_manage(self):
        assert _client(SYS_EDITOR).get("/api/v1/system/storage-root").status_code == 403
        assert _client(PROJ_ONLY).get("/api/v1/system/storage-root").status_code == 403

    def test_project_override_beats_system_value(self, store, tmp_path):
        from modules.flow_gate.storage.paths import get_storage_root

        _set_system_root(store, str(tmp_path / "sysroot"))
        _set_project_override(store, "proj_a", str(tmp_path / "projroot"))
        body = _client(PROJ_ONLY).get("/api/v1/projects/proj_a/storage-root").json()
        assert body == {
            "project_id": "proj_a",
            "configured_value": str(tmp_path / "sysroot"),
            "project_override": str(tmp_path / "projroot"),
            "effective_value": str(get_storage_root("proj_a")),
            "effective_source": "project_override",
            "env_override": False,
        }
        assert body["effective_value"] == str(tmp_path / "projroot")

    def test_project_without_override_falls_back_to_system(self, store, tmp_path):
        _set_system_root(store, str(tmp_path / "sysroot"))
        _set_project_override(store, "proj_b", "")
        body = _client(ADMIN).get("/api/v1/projects/proj_b/storage-root").json()
        assert body["project_override"] is None
        assert body["effective_source"] == "system_setting"
        assert body["effective_value"] == str(tmp_path / "sysroot")

    def test_env_shadows_project_override_but_override_is_reported(self, store, tmp_path, monkeypatch):
        _set_project_override(store, "proj_a", str(tmp_path / "projroot"))
        monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path / "envroot"))
        body = _client(ADMIN).get("/api/v1/projects/proj_a/storage-root").json()
        assert body["project_override"] == str(tmp_path / "projroot")
        assert body["effective_source"] == "env"
        assert body["effective_value"] == str(tmp_path / "envroot")
        assert body["env_override"] is True

    def test_project_endpoint_permission_and_404(self):
        assert _client(NOBODY).get("/api/v1/projects/proj_a/storage-root").status_code == 403
        assert _client(ADMIN).get("/api/v1/projects/proj_missing/storage-root").status_code == 404

    def test_existing_system_settings_response_unchanged(self, store):
        # Legacy consumers still get the effective path in setting_value for a blank row.
        from modules.flow_gate.storage.paths import default_storage_root

        _set_system_root(store, "")
        body = _client(ADMIN).get("/api/v1/system/settings").json()
        row = next(r for r in body["settings"] if r["setting_key"] == "storage_root")
        assert row["setting_value"] == str(default_storage_root())
        assert set(body) == {"settings"}
