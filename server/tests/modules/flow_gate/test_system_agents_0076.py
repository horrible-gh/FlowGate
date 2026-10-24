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
REMOTE_BODY = {"name": "remote-probe", "enabled": True, "location": "remote", "connection_mode": "agent_pull",
               "local_executable_path": None, "configured_capabilities": {"ai_cli": True, "ai_api": True, "storage": True}}


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
        ("POST", f"{API}/system/agents", LOCAL_BODY),
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

def test_v16_register_local_and_remote_with_the_existing_contract(env):
    a = env.h("A")
    r = env.client.post(f"{API}/system/agents", json=LOCAL_BODY, headers=a)
    assert r.status_code == 201, r.text
    local = r.json()
    assert r.headers["Location"] == f"/flowgate/api/v1/system/agents/{local['agent_id']}"
    assert r.headers["Cache-Control"] == "no-store"
    assert (local["location"], local["connection_mode"], local["local_executable_path"]) == ("local", None, "/opt/flowgate-agent")
    assert (local["online_status"], local["registration_status"], local["credential_status"]) == ("not_applicable", "unregistered", "none")
    assert local["reported_capabilities"] == {"ai_cli": None, "ai_api": None, "storage": None}
    remote = _create(env, REMOTE_BODY)
    assert (remote["connection_mode"], remote["local_executable_path"], remote["online_status"]) == ("agent_pull", None, "offline")
    listed = {x["agent_id"]: x for x in env.client.get(f"{API}/system/agents", headers=a).json()["agents"]}
    assert listed[local["agent_id"]] == local and listed[remote["agent_id"]] == remote  # list = detail
    assert env.agent(remote["agent_id"]).json() == remote
    print(f"V16 created local={local['agent_id']} remote={remote['agent_id']}: list entries equal the POST/GET detail")


@pytest.mark.parametrize("body,why", [
    ({**LOCAL_BODY, "connection_mode": "agent_pull"}, "local with a connection mode"),
    ({**REMOTE_BODY, "local_executable_path": "/opt/x"}, "remote with a path"),
    ({**REMOTE_BODY, "connection_mode": None}, "remote without agent_pull"),
    ({**REMOTE_BODY, "host": "10.0.0.1"}, "an extra field (host)"),
    ({k: v for k, v in LOCAL_BODY.items() if k != "connection_mode"}, "connection_mode left out"),
    ({**REMOTE_BODY, "name": "   "}, "a blank name"),
    ({**REMOTE_BODY, "configured_capabilities": {"ai_cli": True, "ai_api": True}}, "a missing capability"),
])
def test_v16_register_refuses_other_shapes(env, body, why):
    before = _snapshot(env.db_path, AGENT_TABLES)
    r = env.client.post(f"{API}/system/agents", json=body, headers=env.h("A"))
    print(f"V16 POST with {why}: {r.status_code}")
    assert r.status_code == 422, r.text
    assert _snapshot(env.db_path, AGENT_TABLES) == before


def test_v16_patch_changed_fields_only_location_immutable_and_enabled_toggle(env):
    a = env.h("A")
    aid = _create(env, REMOTE_BODY)["agent_id"]
    r = env.client.patch(f"{API}/system/agents/{aid}", json={"name": "renamed"}, headers=a)
    assert r.status_code == 200 and r.json()["name"] == "renamed"
    assert r.json()["configured_capabilities"] == REMOTE_BODY["configured_capabilities"]  # untouched
    r = env.client.patch(f"{API}/system/agents/{aid}", json={"configured_capabilities": {"ai_cli": False, "ai_api": True, "storage": False}}, headers=a)
    assert r.status_code == 200 and r.json()["name"] == "renamed"
    loc = env.client.patch(f"{API}/system/agents/{aid}", json={"location": "local"}, headers=a)
    assert loc.status_code == 422 and loc.json()["error"]["details"] == {"field": "location", "reason": "immutable"}
    assert env.client.patch(f"{API}/system/agents/{aid}", json={}, headers=a).status_code == 422
    off = env.client.patch(f"{API}/system/agents/{aid}", json={"enabled": False}, headers=a)
    assert off.status_code == 200 and off.json()["enabled"] is False
    assert env.agent(aid).json()["enabled"] is False
    on = env.client.patch(f"{API}/system/agents/{aid}", json={"enabled": True}, headers=a)
    assert on.status_code == 200 and env.agent(aid).json()["enabled"] is True
    got = env.agent(aid).json()
    print(f"V16 PATCH name/caps 200, location 422 immutable, {{}} 422, enabled false/true 200 -> {got['name']}, {got['location']}, {got['configured_capabilities']}")
    assert got["location"] == "remote" and got["connection_mode"] == "agent_pull"


def test_v16_online_offline_incompatible_revoked_and_disabled_as_the_get_reports_them(env):
    a = env.h("A")
    aid, credential = _registered(env, "status-probe")
    assert env.heartbeat(aid, credential).status_code == 200
    online = env.agent(aid).json()
    assert (online["online_status"], online["registration_status"], online["credential_status"], online["protocol_compatibility"]) == ("online", "registered", "active", "compatible")
    assert online["reported_capabilities"] == {"ai_cli": True, "ai_api": False, "storage": False}
    assert online["effective_capabilities"] == {"ai_cli": True, "ai_api": False, "storage": False}  # configured ∧ reported
    assert (online["agent_version"], online["os"], online["architecture"], online["protocol_version"]) == ("1.4.2", "linux", "amd64", 1)

    # offline: the last heartbeat is older than the 90 s TTL
    env.store._execute("UPDATE agent_credentials SET last_heartbeat_at='2026-01-01T00:00:00Z' WHERE agent_id=?", [aid])
    assert env.agent(aid).json()["online_status"] == "offline"

    # disabled: heartbeat refused, offline while disabled, registration/credential kept
    assert env.heartbeat(aid, credential).status_code == 200
    env.client.patch(f"{API}/system/agents/{aid}", json={"enabled": False}, headers=a)
    hb = env.heartbeat(aid, credential)
    disabled = env.agent(aid).json()
    assert hb.status_code == 403 and hb.json()["error"]["code"] == "agent_disabled"
    assert (disabled["online_status"], disabled["credential_status"]) == ("offline", "active")
    env.client.patch(f"{API}/system/agents/{aid}", json={"enabled": True}, headers=a)

    # incompatible: a heartbeat with an unsupported protocol
    bad = env.heartbeat(aid, credential, protocol_version=2)
    incompatible = env.agent(aid).json()
    assert bad.status_code == 409
    assert (incompatible["protocol_compatibility"], incompatible["online_status"], incompatible["last_rejected_protocol_version"]) == ("incompatible", "offline", 2)

    # revoked
    assert env.client.post(f"{API}/system/agents/{aid}/credential/revoke", json={}, headers=a).status_code == 200
    revoked = env.agent(aid).json()
    assert (revoked["registration_status"], revoked["credential_status"], revoked["online_status"], revoked["has_registration_history"]) == ("revoked", "revoked", "offline", True)
    print("V16 states via GET: online -> offline(ttl) -> disabled(403 heartbeat, offline, credential active) -> incompatible(409, v2) -> revoked")


# ── V17: token, revoke, delete ───────────────────────────────────────────────

def test_v17_enrollment_needs_the_empty_body_and_its_token_is_never_returned_again(env):
    a = env.h("A")
    aid = _create(env, REMOTE_BODY)["agent_id"]
    no_body = env.client.post(f"{API}/system/agents/{aid}/enrollments", headers=a)
    extra = env.client.post(f"{API}/system/agents/{aid}/enrollments", json={"ttl": 60}, headers=a)
    assert (no_body.status_code, extra.status_code) == (422, 422)
    assert _rows(env.db_path, "SELECT COUNT(*) FROM agent_enrollments")[0][0] == 0
    r = env.client.post(f"{API}/system/agents/{aid}/enrollments", json={}, headers=a)
    assert r.status_code == 201 and r.headers["Cache-Control"] == "no-store"
    body = r.json()
    assert set(body) == {"agent_id", "enrollment_token", "expires_at"} and body["agent_id"] == aid
    token = body["enrollment_token"]
    listed = env.client.get(f"{API}/system/agents", headers=a)
    detail = env.agent(aid)
    assert token not in listed.text and token not in detail.text
    assert detail.json()["enrollment_status"] == "pending" and detail.json()["enrollment_expires_at"] == body["expires_at"]
    stored = _rows(env.db_path, "SELECT * FROM agent_enrollments")
    assert all(token not in repr(row) for row in stored)  # digest only
    print(f"V17 enrollments: no body 422, extra field 422, {{}} 201 (keys {sorted(body)}); GET list/detail carry no token; DB keeps a digest only")


def test_v17_reissue_replaces_the_pending_token(env):
    a = env.h("A")
    aid = _create(env, REMOTE_BODY)["agent_id"]
    first = env.client.post(f"{API}/system/agents/{aid}/enrollments", json={}, headers=a).json()["enrollment_token"]
    second = env.client.post(f"{API}/system/agents/{aid}/enrollments", json={}, headers=a).json()["enrollment_token"]
    pending = _rows(env.db_path, "SELECT COUNT(*) FROM agent_enrollments WHERE agent_id=? AND consumed_at IS NULL AND revoked_at IS NULL", (aid,))[0][0]
    assert pending == 1
    old = env.enroll(first)
    new = env.enroll(second)
    print(f"V17 re-issue: pending rows={pending}; first token enroll={old.status_code}, second={new.status_code}")
    assert old.status_code == 409 and old.json()["error"]["code"] == "enrollment_unavailable"
    assert new.status_code == 201


@pytest.mark.parametrize("setup", ["disabled_remote", "local"])
def test_v17_enrollment_only_for_an_enabled_remote_agent(env, setup):
    a = env.h("A")
    if setup == "local":
        aid = _create(env, LOCAL_BODY)["agent_id"]
    else:
        aid = _create(env, {**REMOTE_BODY, "enabled": False})["agent_id"]
    r = env.client.post(f"{API}/system/agents/{aid}/enrollments", json={}, headers=a)
    assert r.status_code == 409 and r.json()["error"]["code"] == "enrollment_not_allowed"
    assert _rows(env.db_path, "SELECT COUNT(*) FROM agent_enrollments")[0][0] == 0


def test_v17_missing_secret_service_answers_503_and_writes_nothing(env, monkeypatch):
    aid = _create(env, REMOTE_BODY)["agent_id"]
    monkeypatch.delenv("FLOWGATE_AGENT_PEPPER")
    before = _snapshot(env.db_path, AGENT_TABLES)
    r = env.client.post(f"{API}/system/agents/{aid}/enrollments", json={}, headers=env.h("A"))
    print(f"V17 issue without FLOWGATE_AGENT_PEPPER: {r.status_code} {r.json()}")
    assert r.status_code == 503 and r.json()["error"]["code"] == "temporarily_unavailable"
    assert _snapshot(env.db_path, AGENT_TABLES) == before


def test_v17_revoke_invalidates_the_credential_and_the_pending_token(env):
    a = env.h("A")
    aid, credential = _registered(env, "revoke-probe")
    pending_token = env.client.post(f"{API}/system/agents/{aid}/enrollments", json={}, headers=a).json()["enrollment_token"]
    assert env.client.post(f"{API}/system/agents/{aid}/credential/revoke", headers=a).status_code == 422  # body required
    r = env.client.post(f"{API}/system/agents/{aid}/credential/revoke", json={}, headers=a)
    assert r.status_code == 200
    after = env.agent(aid).json()
    assert (after["credential_status"], after["enrollment_status"], after["registration_status"]) == ("revoked", "none", "revoked")
    hb = env.heartbeat(aid, credential)
    enroll = env.enroll(pending_token)
    print(f"V17 revoke: 200 -> credential {after['credential_status']}, enrollment {after['enrollment_status']}; old credential heartbeat {hb.status_code}; old pending token enroll {enroll.status_code}")
    assert hb.status_code == 401 and enroll.status_code == 409


def test_v17_delete_only_without_registration_history(env):
    a = env.h("A")
    unregistered = _create(env, REMOTE_BODY)["agent_id"]
    env.client.post(f"{API}/system/agents/{unregistered}/enrollments", json={}, headers=a)  # a pending token goes with it
    r = env.client.delete(f"{API}/system/agents/{unregistered}", headers=a)
    assert r.status_code == 204 and r.content == b""
    gone = env.agent(unregistered)
    assert gone.status_code == 404 and gone.json()["error"]["code"] == "agent_not_found"
    assert unregistered not in env.client.get(f"{API}/system/agents", headers=a).text
    left = _rows(env.db_path, "SELECT COUNT(*) FROM agent_enrollments WHERE agent_id=?", (unregistered,))[0][0]

    registered, _ = _registered(env, "history-probe")
    env.client.post(f"{API}/system/agents/{registered}/credential/revoke", json={}, headers=a)
    before = _snapshot(env.db_path, AGENT_TABLES)
    refused = env.client.delete(f"{API}/system/agents/{registered}", headers=a)
    print(f"V17 delete: unregistered 204 then GET 404 (enrollment rows left={left}); with history {refused.status_code} {refused.json()['error']['code']}")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "agent_has_registration_history"
    assert _snapshot(env.db_path, AGENT_TABLES) == before
    assert env.agent(registered).status_code == 200
