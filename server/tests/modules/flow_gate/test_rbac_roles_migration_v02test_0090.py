"""Fixed roles, one permission-ID notation, permission versions and the one-time grant migration.

Checked on SQLite databases built from the deploy SQL:

* deploy SQL 214 registers every permission ID the server code uses and the fixed mapping;
* system admin / project admin / developer / reviewer / viewer / non-member / disabled account /
  ``__SYSTEM__``-only account x every ``project.*`` ID, through every judgment entry;
* a role change is seen by the next judgment, also when another connection commits it, and a
  rolled-back change leaves both the grant and the version untouched;
* the workspace value and team roles feed the same calculator (highest role wins);
* the Git routes check project.git.read / write / merge per role (real routes, stubbed Git);
* the grant migration: both pass conditions in project and workspace scope, the categories,
  membership for direct grant holders, run-once, refusal of non-administrators until it
  completes (project and workspace requests, real workspace routes), rollback and no automatic
  retry; a disabled workspace's owner/admin, project creation brought by a boundary membership,
  and dotted IDs an install had registered before deploy SQL 214.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-rbac-roles-0090")

_SERVER_DIR = Path(__file__).resolve().parents[3]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

NOW = "2026-11-26T00:00:00+09:00"

ALL4 = {"project.view", "project.document.read", "project.group.read", "project.git.read",
        "project.settings.read", "project.member.read"}
DEV = {"project.document.create", "project.document.update", "project.run", "project.git.write",
       "project.storage.write"}
REV = {"project.document.approve", "project.document.reject", "project.git.merge"}
ADM = {"project.document.delete", "project.group.manage", "project.settings.edit",
       "project.document_type.manage", "project.numbering.manage", "project.member.manage"}
EXPECTED = {
    "role_admin": ALL4 | DEV | REV | ADM,
    "role_developer": ALL4 | DEV,
    "role_reviewer": ALL4 | REV,
    "role_viewer": set(ALL4),
}
SYSTEM_IDS = ("system.settings.read", "system.settings.manage", "system.user.read", "system.user.create",
              "system.user.update", "system.user.disable", "system.user.assign_admin", "system.workspace.create")


# ── database plumbing ────────────────────────────────────────────────────────

class _Txn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._conn.execute(sql, params or [])
        return self._cur

    def fetchone(self):
        row = self._cur.fetchone() if self._cur else None
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()] if self._cur else []


class _DB:
    def __init__(self, path: str):
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        try:
            yield _Txn(self._conn)
        except BaseException:
            self._conn.rollback()
            raise
        self._conn.commit()

    def close(self):
        self._conn.close()


def _number(path: Path) -> int:
    match = re.match(r"(\d+)", path.name)
    return int(match.group(1)) if match else -1


def _apply(conn: sqlite3.Connection, files) -> dict:
    errors = {}
    for sql_file in files:
        try:
            conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 -- older files may not replay on a bare connection
            errors[sql_file.name] = str(exc)
    conn.commit()
    return errors


def _all_files():
    return sorted(_SCHEMA_DIR.glob("*.sql"))


@contextmanager
def _using(db: _DB):
    from modules.flow_gate.auth import auth_cache
    from modules.flow_gate.db import connection as conn_mod
    from modules.flow_gate.rbac import permission_service

    original = conn_mod.STORE

    class _Store(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = db
            self._sq = None

    conn_mod.STORE = _Store()
    permission_service.clear_all_cache()
    auth_cache.invalidate_user()
    try:
        yield conn_mod.STORE
    finally:
        permission_service.clear_all_cache()
        auth_cache.invalidate_user()
        conn_mod.STORE = original


def _user(run, user_id, is_admin=0, is_active=1):
    run("INSERT INTO users (user_id, username, email, password, is_admin, is_active, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [user_id, f"n_{user_id}", f"{user_id}@example.test", "x", is_admin, is_active, NOW, NOW])


def _project(run, project_id, workspace_id=None):
    run("INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) VALUES (?,?,1,?,?)",
        [project_id, project_id.upper(), NOW, NOW])
    run("UPDATE projects SET workspace_id = ? WHERE project_id = ?", [workspace_id, project_id])


def _grant(run, user_id, project_id, role_id):
    run("INSERT INTO user_project_roles (user_id, project_id, role_id, granted_at) VALUES (?,?,?,?)",
        [user_id, project_id, role_id, NOW])


def _workspace(run, workspace_id, status="active", owner="admin", admin="dev", member="none", policy="owner_admin"):
    run("INSERT INTO workspaces (workspace_id, name, name_key, status, is_default, ai_policy_mode,"
        " project_create_policy, default_access_owner, default_access_admin, default_access_member,"
        " created_by, created_at, updated_at) VALUES (?,?,?,?,0,'off',?,?,?,?,NULL,?,?)",
        [workspace_id, workspace_id, workspace_id, status, policy, owner, admin, member, NOW, NOW])


def _member(run, workspace_id, user_id, role):
    run("INSERT INTO workspace_members (workspace_id, user_id, role, added_by, created_at, updated_at)"
        " VALUES (?,?,?,NULL,?,?)", [workspace_id, user_id, role, NOW, NOW])


def _exception(run, workspace_id, project_id, user_id, access):
    run("INSERT INTO workspace_project_access (project_id, user_id, workspace_id, access, updated_by, updated_at)"
        " VALUES (?,?,?,?,NULL,?)", [project_id, user_id, workspace_id, access, NOW])


def _version(path: str, user_id: str) -> int:
    other = sqlite3.connect(path)
    try:
        return int(other.execute("SELECT authz_version FROM users WHERE user_id = ?", [user_id]).fetchone()[0])
    finally:
        other.close()


# ── fresh install from the deploy SQL ────────────────────────────────────────

SYSADMIN = {"user_id": "u_sys_admin", "is_admin": 1}
P_ADMIN = {"user_id": "u_p_admin", "is_admin": 0}
DEVELOPER = {"user_id": "u_dev", "is_admin": 0}
REVIEWER = {"user_id": "u_rev", "is_admin": 0}
VIEWER = {"user_id": "u_view", "is_admin": 0}
OUTSIDER = {"user_id": "u_out", "is_admin": 0}
SYSTEM_ONLY = {"user_id": "u_system_only", "is_admin": 0}
DISABLED = {"user_id": "u_disabled", "is_admin": 0}
ROLE_OF = {"u_p_admin": "role_admin", "u_dev": "role_developer", "u_rev": "role_reviewer", "u_view": "role_viewer"}
PEOPLE = (SYSADMIN, P_ADMIN, DEVELOPER, REVIEWER, VIEWER, OUTSIDER, SYSTEM_ONLY, DISABLED)


@pytest.fixture(scope="module")
def fresh():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        path = handle.name
    db = _DB(path)
    errors = _apply(db._conn, _all_files())
    assert not {name: err for name, err in errors.items() if _number(Path(name)) >= 214}, errors
    run = db._conn.execute
    for person in PEOPLE:
        _user(run, person["user_id"], is_admin=person["is_admin"],
              is_active=0 if person is DISABLED else 1)
    for pid in ("pr1", "pr2"):
        _project(run, pid)
    for uid, role in ROLE_OF.items():
        _grant(run, uid, "pr1", role)
    _grant(run, DISABLED["user_id"], "pr1", "role_admin")
    _grant(run, SYSTEM_ONLY["user_id"], "__SYSTEM__", "role_admin")
    db._conn.commit()
    with _using(db) as store:
        yield db, store
    db.close()
    os.unlink(path)


class TestDeploySql:
    def test_ids_roles_and_mappings(self, fresh):
        from modules.flow_gate.rbac import matrix

        _, store = fresh
        registered = {r["permission_id"] for r in store._fetch_all("SELECT permission_id FROM permissions")}
        assert matrix.NEW_PERMISSION_IDS <= registered
        assert len(matrix.NEW_PERMISSION_IDS) == 33
        for role, perms in EXPECTED.items():
            rows = store._fetch_all("SELECT permission_id FROM role_permissions WHERE role_id = ?", [role])
            assert {r["permission_id"] for r in rows if r["permission_id"].startswith("project.")} == perms, role
        ws = {}
        for row in store._fetch_all("SELECT workspace_role, permission_id FROM workspace_role_permissions"):
            ws.setdefault(row["workspace_role"], set()).add(row["permission_id"])
        assert ws == {
            "owner": {"workspace.view", "workspace.member.manage", "workspace.team.manage", "workspace.admin",
                      "workspace.project.create"},
            "admin": {"workspace.view", "workspace.member.manage", "workspace.team.manage", "workspace.project.create"},
            "member": {"workspace.view", "workspace.project.create"},
        }
        record = store._fetch_one("SELECT status, summary FROM rbac_migrations WHERE migration_id = 'rbac_0090'")
        assert record["status"] == "completed" and "fresh_install" in record["summary"]

    def test_every_permission_id_in_the_server_code_is_registered(self, fresh):
        _, store = fresh
        registered = {r["permission_id"] for r in store._fetch_all("SELECT permission_id FROM permissions")}
        literal = re.compile(r"""["']((?:project|workspace|system)\.[a-z_]+(?:\.[a-z_]+)?)["']""")
        legacy = re.compile(r"""["'](perm_[a-z_]+)["']""")
        used, old = {}, {}
        for base in ("modules", "flowgate", "routers"):
            for path in (_SERVER_DIR / base).rglob("*.py"):
                text = path.read_text(encoding="utf-8", errors="replace")
                rel = path.relative_to(_SERVER_DIR).as_posix()
                if rel == "modules/flow_gate/rbac/matrix.py":
                    continue  # the old -> new ID table of the migration
                for found in literal.findall(text):
                    used.setdefault(found, rel)
                for found in legacy.findall(text):
                    old.setdefault(found, rel)
        assert {pid: where for pid, where in used.items() if pid not in registered} == {}
        assert old == {}

    def test_test_only_permission_seeds_are_gone(self):
        tests = _SERVER_DIR / "tests"
        for rel in ("conftest.py", "modules/flow_gate/test_settings_api.py",
                    "modules/flow_gate/test_workspaces_settings_0020.py"):
            text = (tests / rel).read_text(encoding="utf-8")
            assert "INTO permissions" not in text and "INTO role_permissions" not in text, rel


class TestRoleTable:
    @pytest.mark.parametrize("person", PEOPLE, ids=[p["user_id"] for p in PEOPLE])
    def test_every_project_permission_through_every_entry(self, fresh, person):
        from modules.flow_gate.rbac import access, decorators, permission_service

        uid = person["user_id"]
        for pid in ("pr1", "pr2"):
            for perm in sorted(ALL4 | DEV | REV | ADM):
                if person is SYSADMIN:
                    expected = True
                elif pid == "pr1" and uid in ROLE_OF:
                    expected = perm in EXPECTED[ROLE_OF[uid]]
                else:
                    expected = False
                assert access.can(person, pid, perm) is expected, (uid, pid, perm)
                assert decorators._has_permission(person, perm, pid) is expected, (uid, pid, perm)
                assert permission_service.has_permission(uid, pid, perm) is expected, (uid, pid, perm)
        for perm in SYSTEM_IDS:
            assert access.can(person, None, perm) is (person is SYSADMIN), (uid, perm)
            assert access.can(person, "pr1", perm) is (person is SYSADMIN), (uid, perm)

    def test_system_role_alone_never_opens_a_project(self, fresh):
        from modules.flow_gate.rbac import access, permission_service

        assert not access.is_member(SYSTEM_ONLY["user_id"], "pr1")
        assert permission_service.get_user_permissions(SYSTEM_ONLY["user_id"], "pr1") == set()
        assert permission_service.get_user_permissions(SYSTEM_ONLY["user_id"], "__SYSTEM__") == set()
        assert access.visible_project_clause(SYSTEM_ONLY) == (" AND 1 = 0", [])
        with pytest.raises(HTTPException) as caught:
            access.require(SYSTEM_ONLY, "pr1", "project.document.read")
        assert caught.value.status_code == 403

    def test_lists_follow_the_same_judgment(self, fresh):
        from modules.flow_gate.rbac import access

        _, store = fresh
        for person in PEOPLE:
            for perm in ("project.document.read", "project.document.update", "project.document.approve",
                         "project.member.manage"):
                sql, params = access.member_permission_clause(person, perm, "p.project_id")
                rows = store._fetch_all(
                    "SELECT p.project_id FROM projects p WHERE p.project_id IN ('pr1', 'pr2')" + sql, params)
                expected = {pid for pid in ("pr1", "pr2") if access.can(person, pid, perm)}
                assert {r["project_id"] for r in rows} == expected, (person["user_id"], perm)
        with pytest.raises(ValueError):
            access.member_permission_clause(DEVELOPER, "project.document.read", "project_id")


class TestVersions:
    def test_role_change_is_seen_at_once(self, fresh):
        from modules.flow_gate.rbac import access, role_service

        db, _ = fresh
        user = {"user_id": "u_change", "is_admin": 0}
        _user(db._conn.execute, "u_change")
        db._conn.commit()
        assert not access.can(user, "pr2", "project.document.read")
        before = _version(db.path, "u_change")
        row = role_service.assign_role("u_change", "pr2", "role_worker")
        assert row["role_id"] == "role_developer"
        assert access.can(user, "pr2", "project.document.update")
        role_service.assign_role("u_change", "pr2", "role_viewer")
        assert access.can(user, "pr2", "project.document.read")
        assert not access.can(user, "pr2", "project.document.update")
        role_service.revoke_role("u_change", "pr2")
        assert not access.can(user, "pr2", "project.document.read")
        assert _version(db.path, "u_change") == before + 3
        with pytest.raises(ValueError):
            role_service.assign_role("u_change", "pr2", "role_custom_unknown")

    def test_change_committed_by_another_connection_is_seen(self, fresh):
        from modules.flow_gate.rbac import access

        db, _ = fresh
        user = {"user_id": "u_other_proc", "is_admin": 0}
        _user(db._conn.execute, "u_other_proc")
        db._conn.commit()
        assert not access.can(user, "pr2", "project.document.read")
        other = sqlite3.connect(db.path)
        try:
            other.execute("INSERT INTO user_project_roles (user_id, project_id, role_id, granted_at)"
                          " VALUES ('u_other_proc', 'pr2', 'role_viewer', ?)", [NOW])
            other.commit()
            # without a version change this process keeps its cached answer ...
            assert not access.can(user, "pr2", "project.document.read")
            other.execute("UPDATE users SET authz_version = authz_version + 1 WHERE user_id = 'u_other_proc'")
            other.commit()
            # ... and the version decides: the next judgment reads the new grant
            assert access.can(user, "pr2", "project.document.read")
            other.execute("DELETE FROM user_project_roles WHERE user_id = 'u_other_proc'")
            other.execute("UPDATE users SET authz_version = authz_version + 1 WHERE user_id = 'u_other_proc'")
            other.commit()
            assert not access.can(user, "pr2", "project.document.read")
        finally:
            other.close()

    def test_team_change_and_version_commit_together_or_not_at_all(self, fresh):
        from modules.flow_gate.rbac import access, permission_service, role_service

        db, store = fresh
        user = {"user_id": "u_team", "is_admin": 0}
        _user(db._conn.execute, "u_team")
        db._conn.commit()
        team = role_service.create_team("ws_default", f"team-{uuid.uuid4().hex[:6]}")
        role_service.add_team_member(team["team_id"], "u_team")
        start = _version(db.path, "u_team")
        assert not access.can(user, "pr2", "project.document.read")
        with pytest.raises(RuntimeError):
            with store.transaction():
                role_service.assign_team_role(team["team_id"], "pr2", "role_developer")
                raise RuntimeError("fail after the grant")
        assert role_service.get_team_role(team["team_id"], "pr2") is None
        assert _version(db.path, "u_team") == start
        assert not access.can(user, "pr2", "project.document.read")
        role_service.assign_team_role(team["team_id"], "pr2", "role_developer")
        assert _version(db.path, "u_team") == start + 1
        assert access.can(user, "pr2", "project.document.update")
        assert permission_service.role_detail("u_team", "pr2")["sources"] == ["team"]
        role_service.remove_team_member(team["team_id"], "u_team")
        assert _version(db.path, "u_team") == start + 2
        assert not access.can(user, "pr2", "project.document.read")
        role_service.add_team_member(team["team_id"], "u_team")
        role_service.delete_team(team["team_id"])
        assert not access.can(user, "pr2", "project.document.read")
        assert _version(db.path, "u_team") == start + 4

    def test_highest_role_wins_across_direct_and_team(self, fresh):
        from modules.flow_gate.rbac import access, permission_service, role_service

        db, _ = fresh
        user = {"user_id": "u_mix", "is_admin": 0}
        _user(db._conn.execute, "u_mix")
        db._conn.commit()
        role_service.assign_role("u_mix", "pr2", "role_reviewer")
        assert access.can(user, "pr2", "project.document.approve")
        team = role_service.create_team("ws_default", f"mix-{uuid.uuid4().hex[:6]}")
        role_service.add_team_member(team["team_id"], "u_mix")
        role_service.assign_team_role(team["team_id"], "pr2", "role_developer")
        detail = permission_service.role_detail("u_mix", "pr2")
        assert detail["role"] == "role_developer" and detail["sources"] == ["team"]
        assert detail["grants"] == {"direct": "role_reviewer", "team": "role_developer"}
        assert access.can(user, "pr2", "project.document.update")
        assert not access.can(user, "pr2", "project.document.approve")
        role_service.delete_team(team["team_id"])
        assert access.can(user, "pr2", "project.document.approve")

    def test_account_state_and_admin_flag_raise_the_version(self, fresh):
        from modules.flow_gate.db import users as db_users
        from modules.flow_gate.rbac import permission_service, role_service

        db, _ = fresh
        _user(db._conn.execute, "u_state")
        db._conn.commit()
        role_service.assign_role("u_state", "pr2", "role_viewer")
        assert permission_service.has_permission("u_state", "pr2", "project.document.read")
        before = _version(db.path, "u_state")
        db_users.update("u_state", {"is_active": 0})
        assert _version(db.path, "u_state") == before + 1
        assert permission_service.get_user_permissions("u_state", "pr2") == set()
        db_users.update("u_state", {"is_active": 1})
        assert permission_service.has_permission("u_state", "pr2", "project.document.read")
        db_users.update("u_state", {"is_admin": 1})
        assert permission_service.has_permission("u_state", "pr1", "project.settings.edit")
        assert _version(db.path, "u_state") == before + 3


class TestWorkspaceScope:
    def test_workspace_value_and_workspace_permissions(self, fresh):
        from modules.flow_gate.rbac import access, permission_service
        from modules.flow_gate.settings import workspace_service

        db, store = fresh
        run = db._conn.execute
        for uid in ("u_wo", "u_wa", "u_wm"):
            _user(run, uid)
        _workspace(run, "ws_t", owner="none", admin="dev", member="none", policy="owner_admin")
        _project(run, "pw1", "ws_t")
        _member(run, "ws_t", "u_wo", "owner")
        _member(run, "ws_t", "u_wa", "admin")
        _member(run, "ws_t", "u_wm", "member")
        db._conn.commit()
        owner, wadmin, member = ({"user_id": u, "is_admin": 0} for u in ("u_wo", "u_wa", "u_wm"))
        # workspace roles: mapping + policy
        assert access.can_workspace(owner, "ws_t", "workspace.admin")
        assert not access.can_workspace(wadmin, "ws_t", "workspace.admin")
        assert access.can_workspace(wadmin, "ws_t", "workspace.member.manage")
        assert access.can_workspace(wadmin, "ws_t", "workspace.project.create")
        assert not access.can_workspace(member, "ws_t", "workspace.project.create")
        assert access.can_workspace(member, "ws_t", "workspace.view")
        assert not access.can_workspace(OUTSIDER, "ws_t", "workspace.view")
        assert not access.can(owner, "pw1", "workspace.view")
        # a workspace role alone does not open a project; the workspace value does
        assert not access.can(owner, "pw1", "project.document.read")
        assert access.can(wadmin, "pw1", "project.document.update")
        assert not access.can(member, "pw1", "project.document.read")
        workspace_service.set_default_access(SYSADMIN, "ws_t", {"member": "review"})
        assert permission_service.effective_role("u_wm", "pw1") == "role_reviewer"
        workspace_service.set_project_access(SYSADMIN, "ws_t", "pw1", "u_wm", "none")
        assert permission_service.effective_role("u_wm", "pw1") is None
        workspace_service.set_project_access(SYSADMIN, "ws_t", "pw1", "u_wm", "default")
        assert permission_service.effective_role("u_wm", "pw1") == "role_reviewer"
        workspace_service.remove_member(SYSADMIN, "ws_t", "u_wm")
        assert permission_service.effective_role("u_wm", "pw1") is None
        assert store._fetch_all("SELECT * FROM user_project_roles WHERE project_id = 'pw1'") == []

    def test_direct_grant_becomes_the_persons_role(self, fresh):
        from modules.flow_gate.rbac import permission_service
        from modules.flow_gate.settings import workspace_service

        db, _ = fresh
        run = db._conn.execute
        _user(run, "u_direct")
        _workspace(run, "ws_d", member="dev")
        _project(run, "pd1", "ws_d")
        _project(run, "pd2", "ws_d")
        db._conn.commit()
        workspace_service.assign_direct_project_role("u_direct", "pd1", "role_viewer", actor=SYSADMIN)
        assert permission_service.effective_role("u_direct", "pd1") == "role_viewer"
        assert permission_service.effective_role("u_direct", "pd2") is None
        workspace_service.revoke_direct_project_role("u_direct", "pd1", actor=SYSADMIN)
        assert permission_service.effective_role("u_direct", "pd1") is None


# ── Git routes per role (real routes, Git stubbed) ───────────────────────────

def _git_client(user: dict, monkeypatch) -> TestClient:
    from modules.flow_gate.api.v1 import git_routes
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.services.git import worktree_manual

    monkeypatch.setattr(worktree_manual, "commit", lambda group_id, **kwargs: {"committed": group_id})
    monkeypatch.setattr(git_routes.git_service, "approve_merge_review", lambda *a, **k: {"approved": True})
    monkeypatch.setattr(git_routes.git_service, "list_branches", lambda project_id: {"branches": []})
    monkeypatch.setattr(git_routes.git_service, "delete_config", lambda project_id: {"deleted": True})
    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


class TestGitRoutesPerRole:
    @pytest.mark.parametrize("person,read,write,merge,settings", [
        (P_ADMIN, 200, 200, 200, 200),
        (DEVELOPER, 200, 200, 403, 403),
        (REVIEWER, 200, 403, 200, 403),
        (VIEWER, 200, 403, 403, 403),
        (OUTSIDER, 403, 403, 403, 403),
    ], ids=["admin", "developer", "reviewer", "viewer", "outsider"])
    def test_route_checks_follow_the_role(self, fresh, monkeypatch, person, read, write, merge, settings):
        client = _git_client(person, monkeypatch)
        assert client.get("/api/v1/projects/pr1/git/branches").status_code == read
        assert client.post("/api/v1/groups/pr1.m.0001/git/worktree/commit",
                           json={"message": "work"}).status_code == write
        assert client.post("/api/v1/projects/pr1/git/merge/1/approve",
                           json={"attempt_id": str(uuid.uuid4()), "review_fingerprint": "f"}).status_code == merge
        assert client.delete("/api/v1/projects/pr1/git/config").status_code == settings


# ── the grant migration ──────────────────────────────────────────────────────

OLD_USERS = {
    "m_admin": 1, "m_a": 0, "m_b": 0, "m_c": 0, "m_d": 0, "m_e": 0, "m_g": 0, "m_h": 0,
    "m_null": 0, "m_sys": 0, "m_off": 0, "m_wo": 0, "m_wa": 0, "m_pa": 0,
}


def _seed_old_install(conn: sqlite3.Connection) -> None:
    """An old install whose every difference falls in a category design 7.3 lists."""
    run = conn.execute
    for uid, admin in OLD_USERS.items():
        _user(run, uid, is_admin=admin, is_active=0 if uid == "m_off" else 1)
    run("UPDATE workspaces SET default_access_owner = 'admin', default_access_admin = 'dev',"
        " default_access_member = 'none', project_create_policy = 'owner_admin' WHERE workspace_id = 'ws_default'")
    _workspace(run, "ws_off", status="disabled")
    _workspace(run, "ws_two", member="dev")
    for pid, wid in (("p1", "ws_default"), ("p2", "ws_default"), ("p3", "ws_off"), ("p4", None),
                     ("p5", "ws_two"), ("p6", "ws_two")):
        _project(run, pid, wid)
    _member(run, "ws_default", "m_admin", "owner")
    _member(run, "ws_default", "m_a", "admin")      # default dev
    _member(run, "ws_default", "m_b", "member")     # exception review on p1
    _member(run, "ws_default", "m_c", "member")     # default none
    _member(run, "ws_default", "m_e", "admin")      # default dev, viewer row
    _member(run, "ws_default", "m_g", "member")     # exception review on p1
    _member(run, "ws_default", "m_pa", "admin")     # default dev, role_admin row on p2
    _exception(run, "ws_default", "p1", "m_b", "review")
    _exception(run, "ws_default", "p1", "m_g", "review")
    _grant(run, "m_a", "p1", "role_worker")          # repeats the workspace value; p2 row missing
    _grant(run, "m_b", "p1", "role_viewer")          # repeats 'review' -> reviewer (G2)
    _grant(run, "m_c", "p1", "role_manager")         # differs -> exception admin (G1); perm_user_read (L2)
    _grant(run, "m_e", "p1", "role_viewer")          # viewer row below the workspace value dev
    _grant(run, "m_g", "p1", "role_viewer")
    _grant(run, "m_g", "__SYSTEM__", "role_worker")  # create/update/run only through __SYSTEM__ (L1)
    _grant(run, "m_pa", "p2", "role_admin")          # a project role_admin carried system IDs (L2)
    _grant(run, "m_sys", "__SYSTEM__", "role_admin")  # system IDs only through __SYSTEM__ (L1)
    _grant(run, "m_off", "p1", "role_worker")        # disabled account
    _grant(run, "m_admin", "p2", "role_admin")
    conn.commit()


def _add_unlisted_shapes(conn: sqlite3.Connection) -> None:
    """Design 7.2 rows whose move brings a difference design 7.3 does not list (before 214).

    * a person with a project row outside their workspaces becomes a member there (grant
      boundary): workspace.view grows (proposed G6) -- m_d (member default dev in ws_two),
      m_h (disabled workspace ws_off), m_null (project without workspace_id);
    * the owner and the admin of the disabled ws_off lose member and team management under the
      state rule of design 2.2-B 4 (proposed L4) -- m_wo, m_wa.
    """
    run = conn.execute
    _member(run, "ws_off", "m_wo", "owner")
    _member(run, "ws_off", "m_wa", "admin")
    _grant(run, "m_d", "p5", "role_viewer")          # not a member of ws_two (member default dev)
    _grant(run, "m_h", "p3", "role_worker")          # disabled workspace
    _grant(run, "m_null", "p4", "role_worker")       # project without workspace_id


@pytest.fixture(scope="module")
def old_template():
    """Migrations before 214 + an old install's grants (built once, copied per test)."""
    directory = tempfile.mkdtemp(prefix="rbac0090-")
    path = os.path.join(directory, "template.db")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    _apply(conn, [f for f in _all_files() if _number(f) < 214])
    _seed_old_install(conn)
    conn.close()
    yield path
    shutil.rmtree(directory, ignore_errors=True)


def _open_old(old_template, tmp_path, prepare=None) -> _DB:
    """A copy of the old install; ``prepare(conn)`` changes it before deploy SQL 214 runs."""
    path = str(tmp_path / "old.db")
    shutil.copyfile(old_template, path)
    if prepare is not None:
        conn = sqlite3.connect(path)
        prepare(conn)
        conn.commit()
        conn.close()
    db = _DB(path)
    errors = _apply(db._conn, [f for f in _all_files() if _number(f) >= 214])
    assert errors == {}, errors
    return db


@pytest.fixture
def old_install(old_template, tmp_path):
    db = _open_old(old_template, tmp_path)
    with _using(db) as store:
        yield db, store
    db.close()


def _register_by_hand(ids, grants):
    """An install that registered some dotted IDs itself (the removed role editing API) and
    granted them to some roles, before deploy SQL 214."""
    def prepare(conn):
        for pid in ids:
            conn.execute("INSERT INTO permissions (permission_id, permission_name, description, created_at)"
                         " VALUES (?,?,?,?)", [pid, pid, "registered by hand", NOW])
        for role, pid in grants:
            conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES (?,?)", [role, pid])
    return prepare


def _diff(record) -> list[dict]:
    import json

    return json.loads(record["diff"])["diff"]


def _versions(store) -> dict:
    return {r["user_id"]: int(r["authz_version"]) for r in store._fetch_all("SELECT user_id, authz_version FROM users")}


class TestMigration:
    def test_old_install_moves_with_only_intended_changes(self, old_install):
        import json

        from modules.flow_gate.rbac import access, matrix, migration_0090
        from modules.flow_gate.rbac import permission_service as ps

        db, store = old_install
        assert store._fetch_one("SELECT 1 AS ok FROM rbac_migrations") is None
        old_rows = store._fetch_all("SELECT * FROM user_project_roles")
        before = _versions(store)
        result = migration_0090.run()
        assert result["status"] == "completed", result
        assert result["unexplained_rows"] == 0 and result["condition_1_mismatches"] == 0
        assert result["outside_allowed"] == {}
        assert {"L1", "L2", "L3", "G1", "G2", "G3", "G4", "G5"} <= set(result["categories"]), result["categories"]
        assert set(result["categories"]) <= set(matrix.ALLOWED_CATEGORIES), result["categories"]
        assert result["registry_source"] == "kept_before_214"
        roles = {(u, p): ps.effective_role(u, p) for u in OLD_USERS for p in ("p1", "p2", "p3", "p4", "p5", "p6")}
        assert roles[("m_a", "p1")] == "role_developer" and roles[("m_a", "p2")] is None
        assert roles[("m_b", "p1")] == "role_reviewer"
        assert roles[("m_c", "p1")] == "role_admin"
        assert roles[("m_e", "p1")] == "role_viewer"
        assert roles[("m_g", "p1")] == "role_reviewer"
        assert roles[("m_pa", "p2")] == "role_admin" and roles[("m_pa", "p1")] is None
        assert all(role is None for (u, _p), role in roles.items()
                   if u in ("m_sys", "m_off", "m_d", "m_h", "m_null", "m_wo", "m_wa"))
        left = {(r["user_id"], r["project_id"]): r["role_id"] for r in store._fetch_all(
            "SELECT * FROM user_project_roles")}
        assert left == {
            ("m_e", "p1"): "role_viewer", ("m_off", "p1"): "role_developer",
            ("m_g", "__SYSTEM__"): "role_worker", ("m_sys", "__SYSTEM__"): "role_admin",
        }
        exceptions = {(r["project_id"], r["user_id"]): r["access"] for r in store._fetch_all(
            "SELECT * FROM workspace_project_access")}
        assert exceptions[("p1", "m_c")] == "admin"
        assert exceptions[("p2", "m_a")] == "none"
        assert exceptions[("p1", "m_e")] == "none"
        assert exceptions[("p2", "m_pa")] == "admin" and exceptions[("p1", "m_pa")] == "none"
        assert store._fetch_one("SELECT workspace_id FROM projects WHERE project_id = 'p4'")["workspace_id"] == "ws_default"
        assert len(store._fetch_all("SELECT * FROM user_project_roles_legacy_0090")) == len(old_rows)
        after = _versions(store)
        assert all(after[u] == before[u] + 1 for u in before)
        record = migration_0090.get_record()
        assert record["status"] == "completed" and record["attempt"] == 1
        assert record["before_digest"] and record["after_digest"]
        diff = _diff(record)
        assert all(line["category"] and line["allowed"] for line in diff)
        # the before/after comparison, printed for the report (pytest -s)
        print("\nRBAC0090-PLAN", json.dumps(result["plan"], sort_keys=True))
        print("RBAC0090-CATEGORIES", json.dumps(result["categories"], sort_keys=True))
        print("RBAC0090-DIGESTS", result["before_digest"][:16], result["after_digest"][:16],
              result["before_workspace_digest"][:16], result["after_workspace_digest"][:16],
              result["before_role_scope_digest"][:16], result["after_role_scope_digest"][:16])
        for line in sorted((l for l in diff if l["scope"] != "path"),
                           key=lambda l: (l["scope"], l["user_id"], l["target"], l["category"], l["permission"])):
            print("RBAC0090-DIFF", line["scope"], line["user_id"], line["target"], line["before"], "->",
                  line["after"], line["category"], line["permission"])
        for line in (l for l in diff if l["scope"] == "path"):
            print("RBAC0090-PATH", line["item"], line["user_id"], "|", line["target"], "|", line["before"], "->",
                  line["after"], line["category"])
        losses = {(l["scope"], l["user_id"], l["target"], l["permission"], l["category"])
                  for l in diff if l["scope"] in ("project", "workspace") and l["after"] == "none"}
        assert losses == {
            ("project", "m_g", "p1", "project.document.create", "L1"),
            ("project", "m_g", "p1", "project.document.update", "L1"),
            ("project", "m_g", "p1", "project.run", "L1"),
        }
        assert [l for l in diff if l["scope"] == "workspace"] == []
        # L2: the system / workspace IDs a project role carried; what a __SYSTEM__ row carried is L1
        scope_out = [l for l in diff if l["scope"] == "role_scope"]
        assert scope_out and all(l["after"] == "none" and l["before"] in ("all", "partial") for l in scope_out)
        assert all(l["category"] == ("L1" if l["target"] == "__SYSTEM__" else "L2") for l in scope_out)
        lost = {}
        for l in scope_out:
            lost.setdefault((l["user_id"], l["target"]), set()).add(l["permission"])
        assert "system.user.read" in lost[("m_c", "p1")]               # role_manager: perm_user_read
        assert {"workspace.project.create", "system.settings.read", "system.settings.manage",
                "system.user.read", "system.user.create", "system.user.update",
                "system.user.disable"} <= lost[("m_pa", "p2")]          # role_admin held every old ID
        assert lost[("m_sys", "__SYSTEM__")] == lost[("m_pa", "p2")]    # the same role on __SYSTEM__
        assert set(lost) == {("m_c", "p1"), ("m_pa", "p2"), ("m_sys", "__SYSTEM__")}
        pa = {"user_id": "m_pa", "is_admin": 0}
        assert access.can(pa, "p2", "project.settings.edit")
        assert not access.can(pa, "p2", "system.settings.read") and not access.can(pa, "p2", "system.user.read")
        # L3: one fixed line per route of design 0.2 / 0.3, always recorded
        paths = [l for l in diff if l["scope"] == "path"]
        assert len(paths) == len(matrix.L3_PATHS) and all(l["category"] == "L3" for l in paths)
        assert result["categories"]["L3"] == len(matrix.L3_PATHS)

    def test_runs_once_and_never_recreates_a_removed_grant(self, old_install):
        import startup
        from modules.flow_gate.rbac import migration_0090, role_service
        from modules.flow_gate.rbac import permission_service as ps

        db, store = old_install
        startup.migrate_rbac_0090()
        assert migration_0090.get_record()["status"] == "completed"
        role_service.revoke_role("m_e", "p1")
        assert ps.effective_role("m_e", "p1") is None
        assert migration_0090.run() == {"status": "completed", "skipped": True, "retry_refused": False}
        startup.migrate_rbac_0090()
        assert migration_0090.run(retry=True)["retry_refused"] is True
        assert role_service.get_user_role("m_e", "p1") is None
        assert ps.effective_role("m_e", "p1") is None

    def test_failure_rolls_back_refuses_non_admins_and_is_not_retried(self, old_install):
        from modules.flow_gate.rbac import access, migration_0090

        db, store = old_install
        store._execute("INSERT INTO roles (role_id, role_name, is_system, created_at, updated_at)"
                       " VALUES ('role_custom', 'custom', 0, ?, ?)", [NOW, NOW])
        store._execute("UPDATE user_project_roles SET role_id = 'role_custom' WHERE user_id = 'm_a'")
        rows = store._fetch_all("SELECT * FROM user_project_roles ORDER BY user_id, project_id")
        exceptions = store._fetch_all("SELECT * FROM workspace_project_access ORDER BY project_id, user_id")
        before = _versions(store)
        result = migration_0090.run()
        assert result["status"] == "failed" and "fixed counterpart" in result["error"]
        record = migration_0090.get_record()
        assert record["status"] == "failed" and "m_a" in (record["summary"] or "") + (record["diff"] or "")
        assert store._fetch_all("SELECT * FROM user_project_roles ORDER BY user_id, project_id") == rows
        assert store._fetch_all("SELECT * FROM workspace_project_access ORDER BY project_id, user_id") == exceptions
        assert store._fetch_all("SELECT * FROM user_project_roles_legacy_0090") == []
        assert _versions(store) == before
        # until it completes only administrators are served
        member = {"user_id": "m_a", "is_admin": 0}
        assert not access.can(member, "p1", "project.document.read")
        with pytest.raises(HTTPException) as caught:
            access.require(member, "p1", "project.document.read")
        assert caught.value.detail == "rbac_migration_incomplete"
        assert access.can({"user_id": "m_admin", "is_admin": 1}, "p1", "project.document.read")
        # no automatic retry (the start hook calls run() again)
        assert migration_0090.run() == {"status": "failed", "skipped": True}
        assert migration_0090.get_record()["attempt"] == 1
        # a person fixes the data and starts it again
        store._execute("UPDATE user_project_roles SET role_id = 'role_worker' WHERE user_id = 'm_a'")
        assert migration_0090.run(retry=True)["status"] == "completed"
        assert migration_0090.get_record()["attempt"] == 2
        assert access.can(member, "p1", "project.document.read")

    def test_interrupted_run_stays_closed(self, old_install):
        from modules.flow_gate.rbac import access, migration_0090

        db, store = old_install
        store._execute("INSERT INTO rbac_migrations (migration_id, status, attempt, started_at, updated_at)"
                       " VALUES ('rbac_0090', 'running', 1, ?, ?)", [NOW, NOW])
        assert migration_0090.run() == {"status": "running", "skipped": True}
        assert not access.can({"user_id": "m_a", "is_admin": 0}, "p1", "project.document.read")
        # a person restarts it: the interrupted attempt 1 is taken over as attempt 2
        assert migration_0090.run(retry=True)["status"] == "completed"
        assert migration_0090.get_record()["attempt"] == 2
        assert access.can({"user_id": "m_a", "is_admin": 0}, "p1", "project.document.read")

    def test_an_unexplained_difference_fails_and_rolls_back(self, old_install, monkeypatch):
        from modules.flow_gate.rbac import matrix, migration_0090

        db, store = old_install
        rows = store._fetch_all("SELECT * FROM user_project_roles ORDER BY user_id, project_id")
        before = _versions(store)
        monkeypatch.setattr(matrix, "categorize", lambda *args, **kwargs: None)
        result = migration_0090.run()
        assert result["status"] == "failed" and "condition 2" in result["error"]
        assert result["unexplained_rows"] > 0
        assert store._fetch_all("SELECT * FROM user_project_roles ORDER BY user_id, project_id") == rows
        assert _versions(store) == before

    def test_moved_data_that_does_not_reproduce_the_grants_fails(self, old_install, monkeypatch):
        from modules.flow_gate.rbac import migration_0090

        db, store = old_install
        monkeypatch.setattr(migration_0090, "_apply", lambda store, snap, plan: None)
        result = migration_0090.run()
        assert result["status"] == "failed" and "condition 1" in result["error"]
        assert result["condition_1_mismatches"] > 0
        assert migration_0090.get_record()["status"] == "failed"

    def test_workspace_requests_wait_for_the_migration(self, old_install):
        from modules.flow_gate.auth.middleware import get_current_user
        from modules.flow_gate.rbac import access, matrix, migration_0090
        from modules.flow_gate.settings.routers.workspaces import router as ws_router

        db, store = old_install
        member = {"user_id": "m_b", "is_admin": 0, "is_active": 1}     # member of ws_default
        ws_admin = {"user_id": "m_a", "is_admin": 0, "is_active": 1}   # admin of ws_default
        sysadmin = {"user_id": "m_admin", "is_admin": 1, "is_active": 1}
        current = {}
        app = FastAPI()
        app.include_router(ws_router, prefix="/api/v1")
        app.dependency_overrides[get_current_user] = lambda: current["user"]
        client = TestClient(app)

        def call(user, method, url, **kwargs):
            current["user"] = user
            return client.request(method, url, **kwargs)

        def refused(response) -> bool:
            return response.status_code == 403 and response.json()["detail"]["code"] == "rbac_migration_incomplete"

        def default_member_access():
            return store._fetch_one("SELECT default_access_member FROM workspaces"
                                    " WHERE workspace_id = 'ws_default'")["default_access_member"]

        def closed():
            # the judgment refuses every non-administrator ...
            assert not access.can_workspace(member, "ws_default", "workspace.view")
            assert not access.can_workspace(ws_admin, "ws_default", "workspace.member.manage")
            # ... while the migration's table ③ still reads the stored data with the rule
            assert access.workspace_rule(member, "ws_default", "workspace.view")
            assert access.workspace_rule(ws_admin, "ws_default", "workspace.member.manage")
            table = matrix.current_workspace_table(matrix.snapshot(store))
            assert table[("m_b", "ws_default")] == {"workspace.view": "all"}
            assert table[("m_a", "ws_default")] == {p: "all" for p in (
                "workspace.view", "workspace.member.manage", "workspace.team.manage", "workspace.project.create")}
            # real requests
            assert refused(call(member, "GET", "/api/v1/workspaces/ws_default"))
            assert call(member, "GET", "/api/v1/workspaces").json()["workspaces"] == []
            assert refused(call(ws_admin, "POST", "/api/v1/workspaces/ws_default/members",
                                json={"user_id": "m_d", "role": "member"}))
            assert refused(call(ws_admin, "PUT", "/api/v1/workspaces/ws_default/access/defaults",
                                json={"member": "dev"}))
            assert call(sysadmin, "GET", "/api/v1/workspaces/ws_default").status_code == 200
            assert store._fetch_one("SELECT 1 AS ok FROM workspace_members"
                                    " WHERE workspace_id = 'ws_default' AND user_id = 'm_d'") is None
            assert default_member_access() == "none"

        assert migration_0090.get_record() is None      # no record yet (upgraded, not migrated)
        closed()
        store._execute("INSERT INTO rbac_migrations (migration_id, status, attempt, started_at, updated_at)"
                       " VALUES ('rbac_0090', 'running', 1, ?, ?)", [NOW, NOW])
        closed()                                        # interrupted
        store._execute("UPDATE rbac_migrations SET status = 'failed' WHERE migration_id = 'rbac_0090'")
        closed()                                        # failed
        assert migration_0090.run(retry=True)["status"] == "completed"
        assert call(member, "GET", "/api/v1/workspaces/ws_default").status_code == 200
        assert [w["workspace_id"] for w in call(member, "GET", "/api/v1/workspaces").json()["workspaces"]] == ["ws_default"]
        member_url = "/api/v1/workspaces/ws_default/members"
        payload = {"user_id": "m_d", "role": "member"}
        plan = call(ws_admin, "POST", member_url, params={"preview": True}, json=payload)
        assert plan.status_code == 201
        assert call(ws_admin, "POST", member_url, params={"revision": plan.json()["revision"]},
                    json=payload).status_code == 201
        response = call(member, "POST", "/api/v1/workspaces/ws_default/members", json={"user_id": "m_h", "role": "member"})
        assert response.status_code == 403 and response.json()["detail"]["code"] == "forbidden"

    def test_project_creation_from_a_boundary_membership_is_not_categorised(self, old_template, tmp_path):
        from modules.flow_gate.rbac import migration_0090

        def prepare(conn):
            _add_unlisted_shapes(conn)
            # m_d (a direct viewer grant on p5) becomes a member of ws_two; under the policy 'all'
            # that membership would also bring project creation, which has no category at all
            conn.execute("UPDATE workspaces SET project_create_policy = 'all' WHERE workspace_id = 'ws_two'")

        db = _open_old(old_template, tmp_path, prepare)
        try:
            with _using(db) as store:
                rows = store._fetch_all("SELECT * FROM user_project_roles ORDER BY user_id, project_id")
                members = store._fetch_all("SELECT * FROM workspace_members ORDER BY workspace_id, user_id")
                result = migration_0090.run()
                assert result["status"] == "failed" and "condition 2" in result["error"], result
                assert result["condition_1_mismatches"] == 0
                diff = _diff(migration_0090.get_record())
                assert {(l["user_id"], l["target"], l["permission"]) for l in diff if l["category"] is None} == {
                    ("m_d", "ws_two", "workspace.project.create")}
                assert any(l["user_id"] == "m_d" and l["target"] == "ws_two" and l["permission"] == "workspace.view"
                           and l["category"] == "G6" and not l["allowed"] for l in diff)
                assert store._fetch_all("SELECT * FROM user_project_roles ORDER BY user_id, project_id") == rows
                assert store._fetch_all("SELECT * FROM workspace_members ORDER BY workspace_id, user_id") == members
        finally:
            db.close()

    def test_dotted_ids_an_install_had_registered_and_granted_count_in_table_2(self, old_template, tmp_path):
        from modules.flow_gate.rbac import matrix, migration_0090

        grants = [("role_worker", "project.settings.read"), ("role_viewer", "project.settings.read"),
                  ("role_manager", "project.settings.read"), ("role_admin", "project.settings.read")]
        db = _open_old(old_template, tmp_path, _register_by_hand(["project.settings.read"], grants))
        try:
            with _using(db) as store:
                kept = {r["permission_id"] for r in store._fetch_all("SELECT permission_id FROM permissions_legacy_0090")}
                assert "project.settings.read" in kept
                assert not kept & {"project.settings.edit", "project.git.read", "project.git.write", "project.view",
                                   "workspace.view"}
                kept_rows = {(r["role_id"], r["permission_id"]) for r in store._fetch_all(
                    "SELECT role_id, permission_id FROM role_permissions_legacy_0090")}
                assert set(grants) <= kept_rows and ("role_developer", "project.git.write") not in kept_rows
                result = migration_0090.run()
                assert result["status"] == "completed", result
                assert result["registry_source"] == "kept_before_214"
                diff = _diff(migration_0090.get_record())
                # Git reading checked project.settings.read, which every role row held here: no change
                # (on an install without it every role row gains it as G5)
                assert [l for l in diff if l["permission"] == "project.git.read"] == []
                # Git writing / merging checked project.settings.edit, still never registered: G5
                git = [l for l in diff if l["permission"] in ("project.git.write", "project.git.merge")]
                assert git and all(l["category"] == "G5" for l in git), git
                for line in diff:
                    if line["category"] == "G5":
                        assert set(matrix.LEGACY_SOURCES[line["permission"]]) - kept, line
        finally:
            db.close()

    def test_a_registered_id_the_role_did_not_hold_is_not_g5(self, old_template, tmp_path):
        from modules.flow_gate.rbac import migration_0090

        db = _open_old(old_template, tmp_path, _register_by_hand(["project.settings.edit"], [("role_manager", "project.settings.edit")]))
        try:
            with _using(db) as store:
                rows = store._fetch_all("SELECT * FROM user_project_roles ORDER BY user_id, project_id")
                result = migration_0090.run()
                assert result["status"] == "failed" and "condition 2" in result["error"], result
                diff = _diff(migration_0090.get_record())
                # project.settings.edit was registered and only role_manager held it: the developer's
                # Git commit (and the role_admin row's Git commit and merge) are new grants, not routes
                # that were closed by an unregistered ID
                assert {(l["user_id"], l["target"], l["permission"]) for l in diff if l["category"] is None} == {
                    ("m_a", "p1", "project.git.write"), ("m_pa", "p2", "project.git.write"),
                    ("m_pa", "p2", "project.git.merge")}
                # project.settings.read stayed unregistered: Git reading is still G5
                assert ("m_a", "p1", "project.git.read", "G5") in {
                    (l["user_id"], l["target"], l["permission"], l["category"]) for l in diff}
                assert store._fetch_all("SELECT * FROM user_project_roles ORDER BY user_id, project_id") == rows
        finally:
            db.close()

    def test_preview_writes_nothing(self, old_install):
        from modules.flow_gate.rbac import migration_0090

        db, store = old_install
        rows = store._fetch_all("SELECT * FROM user_project_roles ORDER BY user_id, project_id")
        report = migration_0090.run(preview=True)
        assert report["status"] == "would_complete" and report["diff"]
        assert store._fetch_all("SELECT * FROM user_project_roles ORDER BY user_id, project_id") == rows
        assert migration_0090.get_record() is None

    def test_unlisted_categories_fail_until_the_design_lists_them(self, old_template, tmp_path, monkeypatch):
        """G6 and L4 follow from design rules (7.2 grant boundary, 2.2-B 4) but are not in the
        design 7.3 list: the migration fails and rolls back on them. With the two names allowed
        (what listing them in the design would change) the same install moves as 7.2 says."""
        import json

        from modules.flow_gate.rbac import access, matrix, migration_0090
        from modules.flow_gate.rbac import permission_service as ps

        db = _open_old(old_template, tmp_path, _add_unlisted_shapes)
        try:
            with _using(db) as store:
                rows = store._fetch_all("SELECT * FROM user_project_roles ORDER BY user_id, project_id")
                members = store._fetch_all("SELECT * FROM workspace_members ORDER BY workspace_id, user_id")
                exceptions = store._fetch_all("SELECT * FROM workspace_project_access ORDER BY project_id, user_id")
                before = _versions(store)
                result = migration_0090.run()
                assert result["status"] == "failed" and "condition 2" in result["error"], result
                assert result["condition_1_mismatches"] == 0
                assert result["outside_allowed"] == {"G6": 3, "L4": 4}, result["outside_allowed"]
                diff = _diff(migration_0090.get_record())
                outside = [l for l in diff if not l["allowed"]]
                assert all(l["category"] in matrix.PROPOSED_CATEGORIES for l in outside)
                assert {(l["user_id"], l["target"], l["permission"], l["category"]) for l in outside} == {
                    ("m_d", "ws_two", "workspace.view", "G6"),
                    ("m_h", "ws_off", "workspace.view", "G6"),
                    ("m_null", "ws_default", "workspace.view", "G6"),
                    ("m_wo", "ws_off", "workspace.member.manage", "L4"),
                    ("m_wo", "ws_off", "workspace.team.manage", "L4"),
                    ("m_wa", "ws_off", "workspace.member.manage", "L4"),
                    ("m_wa", "ws_off", "workspace.team.manage", "L4"),
                }
                for line in sorted(outside, key=lambda l: (l["category"], l["user_id"], l["permission"])):
                    print("RBAC0090-UNLISTED", line["scope"], line["user_id"], line["target"], line["before"], "->",
                          line["after"], line["category"], line["permission"])
                # nothing moved, nobody's version changed, non-administrators stay refused
                assert store._fetch_all("SELECT * FROM user_project_roles ORDER BY user_id, project_id") == rows
                assert store._fetch_all("SELECT * FROM workspace_members ORDER BY workspace_id, user_id") == members
                assert store._fetch_all("SELECT * FROM workspace_project_access ORDER BY project_id, user_id") == exceptions
                assert _versions(store) == before
                assert not access.can_workspace({"user_id": "m_wo", "is_admin": 0}, "ws_off", "workspace.view")
                assert migration_0090.run(preview=True)["status"] == "would_fail"

                # the same install once the design lists both names
                monkeypatch.setattr(matrix, "ALLOWED_CATEGORIES", matrix.ALLOWED_CATEGORIES | {"G6", "L4"})
                result = migration_0090.run(retry=True)
                assert result["status"] == "completed", result
                print("RBAC0090-UNLISTED-CATEGORIES", json.dumps(result["categories"], sort_keys=True))
                roles = {(u, p): ps.effective_role(u, p) for u in OLD_USERS for p in ("p1", "p3", "p4", "p5", "p6")}
                assert roles[("m_d", "p5")] == "role_viewer" and roles[("m_d", "p6")] is None
                assert roles[("m_h", "p3")] == "role_developer"
                assert roles[("m_null", "p4")] == "role_developer"
                moved = {(r["workspace_id"], r["user_id"]): r["role"] for r in store._fetch_all(
                    "SELECT * FROM workspace_members")}
                assert moved[("ws_two", "m_d")] == "member" and moved[("ws_off", "m_h")] == "member"
                assert moved[("ws_default", "m_null")] == "member"
                after = {(r["project_id"], r["user_id"]): r["access"] for r in store._fetch_all(
                    "SELECT * FROM workspace_project_access")}
                assert after[("p5", "m_d")] == "none" and after[("p6", "m_d")] == "none"
                wo, wa = ({"user_id": u, "is_admin": 0} for u in ("m_wo", "m_wa"))
                assert access.can_workspace(wo, "ws_off", "workspace.view")
                assert access.can_workspace(wo, "ws_off", "workspace.admin")
                assert access.can_workspace(wa, "ws_off", "workspace.view")
                assert not access.can_workspace(wo, "ws_off", "workspace.member.manage")
                assert not access.can_workspace(wa, "ws_off", "workspace.team.manage")
        finally:
            db.close()

    def test_route_losses_are_recorded_one_line_per_route(self, old_install):
        """Design 7.3 L3: every route of design 0.2 (a, b, c, f, g) and 0.3 is one line of the
        difference list, and the card 2 test each line names exists and checks that route."""
        from modules.flow_gate.api.v1 import legacy_misc_routes
        from modules.flow_gate.rbac import matrix, migration_0090

        db, store = old_install
        assert migration_0090.run()["status"] == "completed"
        paths = [l for l in _diff(migration_0090.get_record()) if l["scope"] == "path"]
        assert [(l["item"], l["target"], l["user_id"]) for l in paths] == [
            (item, route, who) for item, route, who, *_rest in matrix.L3_PATHS]
        assert {l["item"] for l in paths} == {"a", "b", "c", "f", "g"}
        assert all(l["category"] == "L3" and l["allowed"] and l["evidence"] for l in paths)
        guard = (_SERVER_DIR / "tests" / "modules" / "flow_gate" / "test_server_access_guard_v02test_0090.py").read_text(
            encoding="utf-8")
        for line in paths:
            for ref in line["evidence"]:
                _file, cls, name = ref.split("::")
                body = guard.split(f"\nclass {cls}", 1)
                assert len(body) == 2, ref
                assert re.search(rf"\n    def {name}\(", body[1].split("\nclass ", 1)[0]), ref
        kept = {route.path for route in legacy_misc_routes.router.routes}
        for line in paths:
            if line["after"] == "removed":
                path = line["target"].split(" ", 1)[1].split("?", 1)[0].split(" ", 1)[0]
                assert path not in kept, line

    def test_a_late_run_that_read_no_record_does_not_run_again(self, old_install, monkeypatch):
        """Two servers start on the same database and both read 'no record'. The one that
        INSERTs second loses on the primary key: it neither runs nor touches the record."""
        from modules.flow_gate.rbac import migration_0090

        db, store = old_install
        assert migration_0090.run()["status"] == "completed"
        record = migration_0090.get_record()
        rows = store._fetch_all("SELECT * FROM user_project_roles ORDER BY user_id, project_id")
        exceptions = store._fetch_all("SELECT * FROM workspace_project_access ORDER BY project_id, user_id")
        copied = len(store._fetch_all("SELECT * FROM user_project_roles_legacy_0090"))
        versions = _versions(store)
        real = migration_0090.get_record
        calls = []

        def stale(store=None):
            calls.append(1)
            return None if len(calls) == 1 else real(store)   # the read made before the other run

        monkeypatch.setattr(migration_0090, "get_record", stale)
        result = migration_0090.run()
        assert result == {"status": "completed", "skipped": True, "claim_lost": True}, result
        assert real() == record
        assert store._fetch_all("SELECT * FROM user_project_roles ORDER BY user_id, project_id") == rows
        assert store._fetch_all("SELECT * FROM workspace_project_access ORDER BY project_id, user_id") == exceptions
        assert len(store._fetch_all("SELECT * FROM user_project_roles_legacy_0090")) == copied
        assert _versions(store) == versions

    def test_a_retry_takes_the_record_with_a_compare_and_swap(self, old_install, monkeypatch):
        """Two people retry a failed migration from the same read: only one attempt runs."""
        from modules.flow_gate.rbac import migration_0090

        db, store = old_install
        store._execute("INSERT INTO roles (role_id, role_name, is_system, created_at, updated_at)"
                       " VALUES ('role_custom', 'custom', 0, ?, ?)", [NOW, NOW])
        store._execute("UPDATE user_project_roles SET role_id = 'role_custom' WHERE user_id = 'm_a'")
        assert migration_0090.run()["status"] == "failed"
        stale = migration_0090.get_record()
        assert (stale["status"], stale["attempt"]) == ("failed", 1)
        store._execute("UPDATE user_project_roles SET role_id = 'role_worker' WHERE user_id = 'm_a'")
        assert migration_0090.run(retry=True)["status"] == "completed"
        done = migration_0090.get_record()
        assert (done["status"], done["attempt"]) == ("completed", 2)
        versions = _versions(store)
        # the other person's retry still holds the old read (failed, attempt 1)
        assert migration_0090._claim(store, stale, retry=True) is None
        real = migration_0090.get_record
        calls = []
        monkeypatch.setattr(migration_0090, "get_record",
                            lambda store=None: (calls.append(1), stale if len(calls) == 1 else real(store))[1])
        result = migration_0090.run(retry=True)
        assert result == {"status": "completed", "skipped": True, "claim_lost": True}, result
        assert real() == done and _versions(store) == versions
        # a run whose claim was taken over meanwhile writes nothing at the end
        assert migration_0090._finish(store, "completed", attempt=1, report={"diff": []}) is False
        assert real() == done

    def test_two_connections_starting_together_apply_it_once(self, old_install, monkeypatch):
        """Two servers (two DB connections, two threads) read 'no record' at the same moment and
        both try to run: one migration is applied, the other run gives way."""
        from modules.flow_gate.db import connection as conn_mod
        from modules.flow_gate.rbac import migration_0090

        db, store = old_install
        other_db = _DB(db.path)

        class _OtherStore(conn_mod.FlowGateStore):
            def __init__(self):
                self._db = other_db
                self._sq = None

        rows = len(store._fetch_all("SELECT * FROM user_project_roles"))
        before = _versions(store)
        local = threading.local()
        monkeypatch.setattr(migration_0090, "_store", lambda: local.store)
        real = migration_0090.get_record
        barrier = threading.Barrier(2)

        def gated(store=None):
            record = real(store)
            if not getattr(local, "passed", False):
                local.passed = True
                barrier.wait(timeout=20)    # both have read the record before either claims
            return record

        monkeypatch.setattr(migration_0090, "get_record", gated)
        results = {}

        def server(name, its_store):
            local.store = its_store
            try:
                results[name] = migration_0090.run()
            except Exception as exc:  # noqa: BLE001 -- reported by the assertion below
                results[name] = {"status": "raised", "error": repr(exc)}

        threads = [threading.Thread(target=server, args=("one", store)),
                   threading.Thread(target=server, args=("two", _OtherStore()))]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
            assert not any(thread.is_alive() for thread in threads)
        finally:
            monkeypatch.undo()
        print("\nRBAC0090-CONCURRENT", sorted((k, v.get("status"), bool(v.get("claim_lost"))) for k, v in results.items()))
        done = [r for r in results.values() if r.get("status") == "completed" and not r.get("skipped")]
        gave_way = [r for r in results.values() if r.get("skipped") and r.get("claim_lost")]
        assert len(done) == 1 and len(gave_way) == 1, results
        record = real()
        assert record["status"] == "completed" and record["attempt"] == 1
        assert len(store._fetch_all("SELECT * FROM user_project_roles_legacy_0090")) == rows
        after = _versions(store)
        assert all(after[u] == before[u] + 1 for u in before), (before, after)
        other_db.close()


class TestManagementApi:
    def test_invite_team_access_revision_and_role_matrix(self, fresh, monkeypatch):
        from modules.flow_gate.api.v1 import module_routes
        from modules.flow_gate.auth import auth_api
        from modules.flow_gate.auth.jwt_service import create_access_token
        from modules.flow_gate.rbac.routers import rbac_admin
        from modules.flow_gate.settings.routers import project_settings, system_users, users, workspaces

        db, store = fresh
        execute = db._conn.execute
        _workspace(execute, "ws_card5")
        _workspace(execute, "ws_other_card5")
        _project(execute, "p_card5", "ws_card5")
        _project(execute, "p_other_card5", "ws_other_card5")
        for group_id, project_id in (("bound", "p_card5"), ("another", "p_card5"), ("outside", "p_other_card5")):
            execute("INSERT INTO groups (group_id, project_id, module, title, created_at, updated_at)"
                    " VALUES (?, ?, 'm', ?, ?, ?)", [group_id, project_id, group_id, NOW, NOW])
        _member(execute, "ws_card5", P_ADMIN["user_id"], "member")
        _grant(execute, P_ADMIN["user_id"], "p_card5", "role_admin")
        db._conn.commit()

        app = FastAPI()
        for router in (workspaces.router, system_users.router, rbac_admin.router, project_settings.router):
            app.include_router(router)
        app.include_router(auth_api.router, prefix="/auth")
        app.include_router(module_routes.router)
        app.include_router(users.router, prefix="/api/v1")
        client = TestClient(app)

        def call(person, method, url, **kwargs):
            token, _ = create_access_token(person["user_id"], person["user_id"], [])
            return client.request(method, url, headers={"Authorization": f"Bearer {token}"}, **kwargs)

        def preview(person, method, path, **kwargs):
            response = call(person, method, path, params={"preview": "true"}, **kwargs)
            assert response.is_success, response.text
            return response.json()

        def apply(person, method, path, **kwargs):
            plan = preview(person, method, path, **kwargs)
            response = call(person, method, path, params={"revision": plan["revision"]}, **kwargs)
            assert response.is_success, response.text
            return plan, response.json()["result"]

        ws_prefix = workspaces.router.prefix
        users_url = system_users.router.prefix + "/users"
        rbac_prefix = rbac_admin.router.prefix
        projects_prefix = project_settings.router.prefix
        teams_url = ws_prefix + "/workspaces/ws_card5/teams"
        grant_url = rbac_prefix + "/projects/p_card5/team-roles"
        caps_url = projects_prefix + "/projects/p_card5/capabilities"
        invite = call(SYSADMIN, "POST", users_url, json={
            "name": "Card Five Invite", "email": "card5@example.test", "workspace_id": "ws_card5",
        })
        assert invite.status_code == 201, invite.text
        invitation = invite.json()
        target = invitation["user"]
        assert target["state"] == "invited"
        target_id = target["user_id"]
        accepted = client.post("/auth/invite/accept", json={
            "token": invitation["invite_path"].split("?invite=", 1)[1], "password": "Secure-Card5-Password1!",
        })
        assert accepted.status_code == 200, accepted.text

        _, team = apply(SYSADMIN, "POST", teams_url, json={"name": "Card Five Team"})
        team_id = team["team_id"]
        membership_url = f'{teams_url}/{team_id}/members/{target_id}'
        apply(SYSADMIN, "PUT", membership_url)
        candidate_path = rbac_prefix + "/projects/p_card5/member-candidates"
        assert call(P_ADMIN, "GET", candidate_path).json()["teams"] == [{"team_id": team_id, "name": "Card Five Team"}]
        team_role_url = f"{grant_url}/{team_id}"
        plan, _ = apply(P_ADMIN, "PUT", team_role_url, json={"role_id": "role_developer"})
        assert plan["impact"][0]["user_id"] == target_id
        assert "project.document.update" in plan["impact"][0]["gained"]
        project = call(target, "GET", caps_url)
        assert project.status_code == 200, project.text
        caps = project.json()["capabilities"]
        assert caps["actions"]["project.document.update"] == {"allowed": True}
        assert caps["actions"]["project.document.approve"]["allowed"] is False
        assert caps["sources"][0]["team_id"] == team_id
        print("CAPABILITIES_EXAMPLE", {"project_id": project.json()["project_id"], "role": caps["role"],
              "actions": {key: caps["actions"][key] for key in ("project.document.update", "project.document.approve")}})
        assert call(target, "GET", "/auth/me").json()["teams"][0]["team_id"] == team_id
        projects = call(target, "GET", projects_prefix + "/projects").json()["projects"]
        assert [row["project_id"] for row in projects] == ["p_card5"]
        assert call(P_ADMIN, "GET", ws_prefix + "/workspaces/ws_other_card5").status_code == 403
        assert call(P_ADMIN, "GET", rbac_prefix + "/projects/p_other_card5/team-roles").status_code == 403
        assert call(P_ADMIN, "PUT", rbac_prefix + "/projects/p_other_card5/team-roles/" + team_id,
                    params={"preview": True}, json={"role_id": "role_developer"}).status_code == 403

        stale = preview(P_ADMIN, "PUT", team_role_url, json={"role_id": "role_reviewer"})
        apply(P_ADMIN, "PUT", team_role_url, json={"role_id": "role_viewer"})
        conflict = call(P_ADMIN, "PUT", team_role_url, params={"revision": stale["revision"]},
                        json={"role_id": "role_reviewer"})
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "revision_conflict"
        caps = call(target, "GET", caps_url).json()["capabilities"]
        assert caps["actions"]["project.document.update"]["allowed"] is False
        from modules.flow_gate.rbac import access
        assert not access.can(target, "p_card5", "project.document.update")

        current = preview(P_ADMIN, "PUT", team_role_url, json={"role_id": "role_viewer"})
        last = call(P_ADMIN, "POST", f"{rbac_prefix}/users/{P_ADMIN['user_id']}/roles",
                    params={"revision": current["revision"]}, json={"project_id": "p_card5", "role_id": "role_viewer"})
        assert last.status_code == 409
        assert last.json()["detail"]["code"] == "last_project_admin"
        legacy_revoke = f"/api/v1/users/{P_ADMIN['user_id']}/project-roles/p_card5"
        legacy_last = call(SYSADMIN, "DELETE", legacy_revoke, params={"revision": current["revision"]})
        assert legacy_last.status_code == 409
        assert legacy_last.json()["detail"]["code"] == "last_project_admin"
        assert store._fetch_one("SELECT COUNT(*) AS qty FROM rbac_change_audit")["qty"] >= 5

        matrix_cases = [
            (SYSADMIN, 200, 200, 200), (P_ADMIN, 403, 403, 200),
            (DEVELOPER, 403, 403, 200), (REVIEWER, 403, 403, 200), (VIEWER, 403, 403, 200),
            (OUTSIDER, 403, 403, 403), (DISABLED, 401, 401, 401),
        ]
        for person, users_status, teams_status, members_status in matrix_cases:
            assert call(person, "GET", users_url).status_code == users_status
            assert call(person, "GET", teams_url).status_code == teams_status
            members_url = rbac_prefix + "/projects/pr1/members"
            assert call(person, "GET", members_url).status_code == members_status
            expected_change = 201 if person in (SYSADMIN, P_ADMIN) else 401 if person is DISABLED else 403
            role_change = call(person, "POST", f"{rbac_prefix}/users/{target_id}/roles",
                               params={"preview": True}, json={"project_id": "p_card5", "role_id": "role_viewer"})
            assert role_change.status_code == expected_change
            expected_workspaces = 401 if person is DISABLED else 200
            assert call(person, "GET", ws_prefix + "/workspaces").status_code == expected_workspaces
        for bearer in ("ai-work-token", "remote-agent-token"):
            assert client.get(users_url, headers={"Authorization": f"Bearer {bearer}"}).status_code == 401
            assert client.put(team_role_url, headers={"Authorization": f"Bearer {bearer}"},
                              json={"role_id": "role_admin"}).status_code == 401

        from modules.flow_gate.services import auth_outbound, token_service
        for kind in ("ai", "agent"):
            bound = {"kind": kind, "issued_to": SYSADMIN["user_id"], "project": "p_card5", "group_id": "bound"}
            assert auth_outbound.can_read_project(bound, "p_card5")
            assert not auth_outbound.can_read_project(bound, "p_other_card5")
            monkeypatch.setattr(token_service, "verify", lambda _raw, bound=bound: bound)
            headers = {"Authorization": f"Bearer {kind}-fixture-token"}
            group_root = "/api/v1/modules/m/groups/"
            assert client.get(group_root + "bound/documents", headers=headers).status_code == 200
            assert client.get(group_root + "another/documents", headers=headers).status_code == 403
            assert client.get(group_root + "outside/documents", headers=headers).status_code == 403

        from modules.flow_gate.auth.session_store import create_session, get_session
        sid = create_session(target_id)
        version = _version(db.path, target_id)
        from modules.flow_gate.rbac import mutation

        def reject_audit(*args, **kwargs):
            raise RuntimeError("forced audit failure")

        with monkeypatch.context() as failure:
            failure.setattr(mutation, "audit", reject_audit)
            with pytest.raises(RuntimeError, match="forced audit failure"):
                call(SYSADMIN, "POST", f"{users_url}/{target_id}/disable")
        assert get_session(sid)["revoked_at"] is None
        assert _version(db.path, target_id) == version
        assert store._fetch_one("SELECT is_active FROM users WHERE user_id = ?", [target_id])["is_active"]

        disabled = call(SYSADMIN, "POST", f"{users_url}/{target_id}/disable")
        assert disabled.status_code == 200, disabled.text
        assert get_session(sid)["revoked_at"]
        assert _version(db.path, target_id) > version
