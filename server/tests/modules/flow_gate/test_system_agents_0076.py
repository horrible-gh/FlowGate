"""flowgate.v02.0076 T#4 — the server side of 시스템 › 시스템 Agent (D §2.6, §5.2 V06·V16·V17).

The new screen uses one API family, ``/system/agents`` of ``api/v1/agent_routes.py``. What it relies
on is pinned here against the real routers, mounted under the production prefixes
(``routers/main.py``): ``/flowgate/auth`` and ``/flowgate/api/v1``. Nothing in the request path is
mocked — no ``get_current_user``/``require_permission`` override, no ``svc`` replacement — and the
database is a sqlite file built from every migration and nothing else (``migrated_sqlite_db``), so
roles and permissions are the ones a deployment gets. The store is the deployment one:
``FlowGateStore`` on sqloader's ``SQLiteWrapper`` (``.env.sample`` DB_TYPE=sqlite3).

Accounts (D §5.1), as in test_system_general_users_0076: A active is_admin; P project admin without
a system permission; M project member; N not a member; R is_admin=false with the strongest seeded
system role (role_admin on __SYSTEM__, which in the migration schema carries no system.* permission);
D is_admin=false with ``system.settings.manage`` explicitly delegated on a custom role inside the
test that observes D (no migration ships that permission row); anonymous; a garbage token; a session
ended by logout. D's results are observations of the existing delegated-permission check — never
release evidence and never an is_admin-only result.

The Agent runtime (``/agent/enrollments``, ``/agent/heartbeat``) is only ever driven here by the
test acting as the Agent, to put an Agent into the online / offline / incompatible / revoked states
the screen has to show. The management GUI never calls those routes (client spec afterEach).
The test app has no ``routers/main.py`` RequestValidationError handler, so a 422 here has FastAPI's
default body; only its status is pinned (production wraps it as ``validation_failed``).
"""
from __future__ import annotations

import os
import sqlite3

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

PASSWORD = "Agents0076!Passw0rd"
API = "/flowgate/api/v1"
AGENT_TABLES = ["agents", "agent_credentials", "agent_enrollments"]
LOCAL_BODY = {"name": "local-probe", "enabled": True, "location": "local", "connection_mode": None,
              "local_executable_path": "/opt/flowgate-agent", "configured_capabilities": {"ai_cli": True, "ai_api": False, "storage": False}}
REMOTE_BODY = {"name": "remote-probe"}


# ── fixture ──────────────────────────────────────────────────────────────────

def _snapshot(db_path, tables=None):
    conn = sqlite3.connect(db_path)
    try:
        names = tables or [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {name: sorted(repr(tuple(r)) for r in conn.execute(f"SELECT * FROM {name}").fetchall()) for name in names}
    finally:
        conn.close()


def _rows(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


class Env:
    def __init__(self, db_path, store, client):
        self.db_path = db_path
        self.store = store
        self.client = client
        self.tokens: dict[str, str] = {}
        self.refresh: dict[str, str] = {}

    def login(self, username):
        r = self.client.post("/flowgate/auth/login", json={"username": username, "password": PASSWORD})
        assert r.status_code == 200, r.text
        body = r.json()
        return body["access_token"], body["refresh_token"]

    def h(self, who):
        if who == "anon":
            return {}
        if who == "garbage":
            return {"Authorization": "Bearer not-a-jwt"}
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    # The test acting as the Agent runtime (never the GUI).
    def enroll(self, token, protocol_version=1):
        return self.client.post(f"{API}/agent/enrollments", json={
            "enrollment_token": token, "protocol_version": protocol_version, "agent_version": "1.4.2",
            "os": "linux", "architecture": "amd64"})

    def heartbeat(self, agent_id, credential, protocol_version=1):
        return self.client.post(f"{API}/agent/heartbeat", headers={"Authorization": f"Bearer {credential}"}, json={
            "agent_id": agent_id, "protocol_version": protocol_version, "agent_version": "1.4.2", "os": "linux",
            "architecture": "amd64", "reported_capabilities": {"ai_cli": True, "ai_api": False, "storage": False}})

    def agent(self, aid, who="A"):
        return self.client.get(f"{API}/system/agents/{aid}", headers=self.h(who))


@pytest.fixture
def env(migrated_sqlite_db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqloader.sqlite3 import SQLiteWrapper

    import modules.flow_gate.db.connection as connection
    from modules.flow_gate.api.v1.agent_routes import router as agent_router
    from modules.flow_gate.auth import auth_cache
    from modules.flow_gate.auth.auth_api import router as auth_router
    from modules.flow_gate.auth.password import hash_password
    from modules.flow_gate.db.connection import now_iso
    from modules.flow_gate.rbac import permission_service, role_service

    monkeypatch.setenv("FLOWGATE_AGENT_PEPPER", "test-only-pepper-0076")
    db_path = migrated_sqlite_db("test_system_agents_0076.db")
    store = object.__new__(connection.FlowGateStore)
    store._db = SQLiteWrapper(db_path)
    store._sq = None
    monkeypatch.setattr(connection, "STORE", store)
    auth_cache.invalidate_everything()
    permission_service.clear_all_cache()

    now = now_iso()
    store._execute(
        "INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
        "VALUES ('p1_ag0076', 'P1 agents 0076', 1, ?, ?)", [now, now])
    people = {
        "ag_a0076": ("usr_ag_a0076", 1, "role_admin", None),
        "ag_p0076": ("usr_ag_p0076", 0, "role_viewer", "role_admin"),
        "ag_m0076": ("usr_ag_m0076", 0, "role_viewer", "role_worker"),
        "ag_n0076": ("usr_ag_n0076", 0, "role_viewer", None),
        "ag_r0076": ("usr_ag_r0076", 0, "role_admin", None),  # R: seeded role_admin, no system.* permission
        "ag_d0076": ("usr_ag_d0076", 0, "role_viewer", None),  # D: delegation granted by _delegate()
        "ag_x0076": ("usr_ag_x0076", 1, "role_admin", None),  # session ended by logout below
    }
    for username, (uid, is_admin, system_role, p1_role) in people.items():
        store._execute(
            "INSERT INTO users (user_id, username, email, password, is_active, is_admin, "
            "first_login_required, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, 0, ?, ?)",
            [uid, username, f"{username}@example.com", hash_password(PASSWORD), is_admin, now, now])
        role_service.assign_role(uid, role_service.SYSTEM_PROJECT, system_role)
        if p1_role:
            role_service.assign_role(uid, "p1_ag0076", p1_role)

    app = FastAPI()
    app.include_router(auth_router, prefix="/flowgate/auth")
    app.include_router(agent_router, prefix=API)
    e = Env(db_path, store, TestClient(app))
    for who, username in (("A", "ag_a0076"), ("P", "ag_p0076"), ("M", "ag_m0076"), ("N", "ag_n0076"),
                          ("R", "ag_r0076"), ("D", "ag_d0076"), ("X", "ag_x0076")):
        e.tokens[who], e.refresh[who] = e.login(username)
    out = e.client.post("/flowgate/auth/logout", json={"refresh_token": e.refresh["X"]}, headers=e.h("X"))
    assert out.status_code == 200
    e.tokens["revoked"] = e.tokens.pop("X")
    return e


def _create(env, body, who="A"):
    r = env.client.post(f"{API}/system/agents", json=body, headers=env.h(who))
    assert r.status_code == 201, r.text
    return r.json()


def _registered(env, name="registered-probe"):
    """A remote Agent enrolled by the test acting as the runtime; returns (agent_id, credential)."""
    aid = _create(env, {**REMOTE_BODY, "name": name})["agent_id"]
    token = env.client.post(f"{API}/system/agents/{aid}/enrollments", json={}, headers=env.h("A")).json()["enrollment_token"]
    enrolled = env.enroll(token)
    assert enrolled.status_code == 201, enrolled.text
    return aid, enrolled.json()["agent_credential"]


# ── V06: direct server permission on every §2.6 path (GUI bypassed) ─────────

def _fixtures(env):
    remote = _create(env, {**REMOTE_BODY, "name": "v06-remote"})["agent_id"]
    unregistered = _create(env, {**REMOTE_BODY, "name": "v06-unregistered"})["agent_id"]
    registered, _ = _registered(env, "v06-registered")
    return remote, unregistered, registered


def _agent_calls(remote, unregistered, registered):
    """Every §2.6 management action with a valid payload, in an order that is valid for A."""
    return [
        ("GET", f"{API}/system/agents", None),
        ("GET", f"{API}/system/agents/{remote}", None),
        ("POST", f"{API}/system/agents", REMOTE_BODY),
        ("PATCH", f"{API}/system/agents/{remote}", {"name": "v06-renamed"}),
        ("POST", f"{API}/system/agents/{remote}/enrollments", {}),
        ("PATCH", f"{API}/system/agents/{remote}", {"enabled": False}),
        ("POST", f"{API}/system/agents/{registered}/credential/revoke", {}),
        ("DELETE", f"{API}/system/agents/{unregistered}", None),
    ]


def _send(client, method, url, body, headers):
    if body is None:
        return client.request(method, url, headers=headers)
    return client.request(method, url, json=body, headers=headers)


def _short(url):
    return url.replace(API, "")


@pytest.mark.parametrize("who,expected", [
    ("P", 403), ("M", 403), ("N", 403), ("R", 403),
    ("anon", 401), ("garbage", 401), ("revoked", 401),
])
def test_v06_without_system_permission_every_agent_call_is_refused_and_nothing_changes(env, who, expected):
    calls = _agent_calls(*_fixtures(env))
    before = _snapshot(env.db_path, AGENT_TABLES)
    seen = [(m, _short(u), _send(env.client, m, u, b, env.h(who)).status_code) for m, u, b in calls]
    print(f"V06 {who}: {seen}")
    assert all(status == expected for _, _, status in seen), seen
    assert _snapshot(env.db_path, AGENT_TABLES) == before


def test_v06_admin_gets_the_existing_success_statuses(env):
    calls = _agent_calls(*_fixtures(env))
    seen = []
    for method, url, body in calls:
        r = _send(env.client, method, url, body, env.h("A"))
        seen.append((method, _short(url), r.status_code))
    print(f"V06 A: {seen}")
    assert [status for _, _, status in seen] == [200, 200, 201, 200, 201, 200, 200, 204], seen


def _delegate(env, uid="usr_ag_d0076"):
    """Test-only explicit delegation (D §5.1 D) of the one permission every §2.6 route checks."""
    from modules.flow_gate.rbac import permission_service, role_service

    s = env.store
    s._execute("INSERT OR IGNORE INTO permissions (permission_id, permission_name, created_at) VALUES "
               "('system.settings.manage', 'test-only 0076 system.settings.manage', '2026-10-20T00:00:00+09:00')")
    s._execute("INSERT INTO roles (role_id, role_name, is_system, created_at, updated_at) VALUES "
               "('role_t0076_agents', 'agents delegate 0076', 0, '2026-10-20T00:00:00+09:00', '2026-10-20T00:00:00+09:00')")
    s._execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ('role_t0076_agents', 'system.settings.manage')")
    role_service.assign_role(uid, role_service.SYSTEM_PROJECT, "role_t0076_agents")
    permission_service.clear_all_cache()


# What the real router/auth/RBAC answered for D on every §2.6 route after the delegation, as observed.
# Pinned so a change in the server's behaviour shows up; NOT an is_admin-only result, NOT a release basis.
D_OBSERVED = [200, 200, 201, 200, 201, 200, 200, 204]


def test_v06_d_delegated_observation_every_agent_route(env):
    calls = _agent_calls(*_fixtures(env))
    undelegated = [(m, _short(u), _send(env.client, m, u, b, env.h("D")).status_code) for m, u, b in calls[:2]]
    assert {status for _, _, status in undelegated} == {403}, undelegated  # role_viewer only
    _delegate(env)
    before = _snapshot(env.db_path)
    seen = [(m, _short(u), _send(env.client, m, u, b, env.h("D")).status_code) for m, u, b in calls]
    after = _snapshot(env.db_path)
    changed = sorted(name for name in after if after[name] != before.get(name))
    print(f"V06 D(before delegation): {undelegated}")
    print(f"V06 D(delegated system.settings.manage): {seen}; tables changed: {changed}")
    assert [status for _, _, status in seen] == D_OBSERVED, seen
    assert set(changed) <= set(AGENT_TABLES), changed


# ── V16: register, status, enable toggle ─────────────────────────────────────











# ── V17: token, revoke, delete ───────────────────────────────────────────────











def _history(env, aid):
    return {t: _rows(env.db_path, f"SELECT COUNT(*) FROM {t} WHERE agent_id=?", (aid,))[0][0]
            for t in ("agent_credentials", "agent_enrollments")}






def _job(env, job_id, status, target=None, owner=None):
    ts = "2026-11-11T00:00:00Z"
    env.store._execute(
        "INSERT INTO agent_jobs(job_id,attempt_id,execution_type,command_line,stdin_text,cwd,timeout_seconds,status,"
        "target_agent_id,payload_digest,lease_owner_agent_id,lease_token,created_at,updated_at) "
        "VALUES(?,?,'remote_cli','echo','','/tmp',60,?,?,'d',?,?,?,?)",
        [job_id, "att_1", status, target, owner, "lsh_x" if owner else None, ts, ts])






# ── flowgate.v02test.0019: workspace cleanup policy ──────────────────────────
