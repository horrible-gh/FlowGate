"""flowgate.v02.0076 T#3 — the server side of 시스템 › 일반 / 사용자 (D §2.5, §2.7, §5.2).

What the new 시스템 일반 and 사용자 screens rely on is pinned here against the real routers,
mounted under the production prefixes (``routers/main.py``): ``/flowgate/auth``,
``/flowgate/api/v1/system/...``. Nothing in the request path is mocked — no
``get_current_user``/``require_permission`` override — and the database is a sqlite file built
from every migration and nothing else (``migrated_sqlite_db``), so the roles and permissions are
the ones a deployment gets. conftest's ``all_migrations_db`` seed (``system.settings.manage`` on
role_admin) is NOT used: a result that only holds with that seed is not a release result.

The store is the deployment one: ``FlowGateStore`` on sqloader's ``SQLiteWrapper`` (``.env.sample``
DB_TYPE=sqlite3), so V13 exercises the real ``transaction()`` → ``begin_transaction()`` path.

Accounts (D §5.1): A active is_admin; P project admin (role_admin on p1) without a system
permission; M project member; N not a member; R is_admin=false holding the strongest *seeded*
system role (role_admin on __SYSTEM__), which in the migration schema carries no system.*
permission (the seeded IDs are perm_system_settings_*) — so R is a no-permission account, not D;
D is_admin=false with the target system permissions explicitly delegated (system.settings.manage,
system.user.read/create/update on a custom role, granted inside the test that observes D, since
no migration ships those permission rows); anonymous; a garbage token; a session ended by logout.
D's results are observations of the existing delegated-permission check — never release evidence
and never an is_admin-only result.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

PASSWORD = "SysGen0076!Passw0rd"
API = "/flowgate/api/v1"
SCRATCH_KEYS = ("scratch_ttl_value", "scratch_ttl_unit")


# ── fixture ──────────────────────────────────────────────────────────────────

def _snapshot(db_path, tables=None):
    """Every row of every (or the given) table, for before/after comparisons."""
    conn = sqlite3.connect(db_path)
    try:
        names = tables or [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        out = {}
        for name in names:
            rows = conn.execute(f"SELECT * FROM {name}").fetchall()
            out[name] = sorted(repr(tuple(r)) for r in rows)
        return out
    finally:
        conn.close()


def _raw_settings(db_path, keys=SCRATCH_KEYS):
    """Read straight from the file, outside the app's store and caches."""
    conn = sqlite3.connect(db_path)
    try:
        rows = dict(conn.execute("SELECT setting_key, setting_value FROM system_settings").fetchall())
    finally:
        conn.close()
    return tuple(rows.get(k) for k in keys)


class Env:
    def __init__(self, db_path, store, client, unsafe_client):
        self.db_path = db_path
        self.store = store
        self.client = client
        self.unsafe_client = unsafe_client
        self.tokens: dict[str, str] = {}
        self.refresh: dict[str, str] = {}

    def login(self, username):
        r = self.client.post("/flowgate/auth/login", json={"username": username, "password": PASSWORD})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "access_token" in body, body
        return body["access_token"], body["refresh_token"]

    def h(self, who):
        if who == "anon":
            return {}
        if who == "garbage":
            return {"Authorization": "Bearer not-a-jwt"}
        return {"Authorization": f"Bearer {self.tokens[who]}"}


@pytest.fixture
def env(migrated_sqlite_db, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqloader.sqlite3 import SQLiteWrapper

    import modules.flow_gate.db.connection as connection
    from modules.flow_gate.auth import auth_cache
    from modules.flow_gate.auth.auth_api import router as auth_router
    from modules.flow_gate.auth.password import hash_password
    from modules.flow_gate.db.connection import now_iso
    from modules.flow_gate.rbac import permission_service, role_service
    from modules.flow_gate.settings.routers.system import router as system_router
    from modules.flow_gate.settings.routers.system_users import router as system_users_router

    monkeypatch.delenv("FLOWGATE_STORAGE_DIR", raising=False)
    db_path = migrated_sqlite_db("test_system_general_users_0076.db")
    store = object.__new__(connection.FlowGateStore)
    store._db = SQLiteWrapper(db_path)
    store._sq = None
    monkeypatch.setattr(connection, "STORE", store)
    auth_cache.invalidate_everything()
    permission_service.clear_all_cache()

    now = now_iso()
    store._execute(
        "INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
        "VALUES ('p1_0076', 'P1 0076', 1, ?, ?)", [now, now])
    people = {
        # username: (user_id, is_admin, system role, p1 role)
        "a0076": ("usr_a0076", 1, "role_admin", None),
        "p0076": ("usr_p0076", 0, "role_viewer", "role_admin"),
        "m0076": ("usr_m0076", 0, "role_viewer", "role_worker"),
        "n0076": ("usr_n0076", 0, "role_viewer", None),
        "d0076": ("usr_d0076", 0, "role_admin", None),  # R: seeded role_admin, no system.* permission
        "dd0076": ("usr_dd0076", 0, "role_viewer", None),  # D: delegation granted by _delegate()
        "x0076": ("usr_x0076", 1, "role_admin", None),  # session ended by logout below
    }
    for username, (uid, is_admin, system_role, p1_role) in people.items():
        store._execute(
            "INSERT INTO users (user_id, username, email, password, is_active, is_admin, "
            "first_login_required, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, 0, ?, ?)",
            [uid, username, f"{username}@example.com", hash_password(PASSWORD), is_admin, now, now])
        role_service.assign_role(uid, role_service.SYSTEM_PROJECT, system_role)
        if p1_role:
            role_service.assign_role(uid, "p1_0076", p1_role)

    app = FastAPI()
    app.include_router(auth_router, prefix="/flowgate/auth")
    app.include_router(system_router, prefix=API)
    app.include_router(system_users_router, prefix=API)
    e = Env(db_path, store, TestClient(app), TestClient(app, raise_server_exceptions=False))
    for who, username in (("A", "a0076"), ("P", "p0076"), ("M", "m0076"), ("N", "n0076"), ("R", "d0076"), ("D", "dd0076"), ("X", "x0076")):
        e.tokens[who], e.refresh[who] = e.login(username)
    # X: a session that was ended (POST /auth/logout). Its access token must stop working.
    out = e.client.post("/flowgate/auth/logout", json={"refresh_token": e.refresh["X"]}, headers=e.h("X"))
    assert out.status_code == 200
    e.tokens["revoked"] = e.tokens.pop("X")
    # X stays an active administrator, so A is not the last one; the last-admin case below
    # disables X first.
    return e


def _settings_map(body):
    return {row["setting_key"]: row["setting_value"] for row in body["settings"]}


# ── V06: direct server permission (GUI bypassed) ─────────────────────────────

def _system_calls(target_uid):
    """§2.5 GET/PATCH and every §2.7 action, with valid payloads."""
    return [
        ("GET", f"{API}/system/storage-root", None),
        ("GET", f"{API}/system/settings", None),
        ("PATCH", f"{API}/system/settings", {"updates": {"scratch_ttl_value": "6", "scratch_ttl_unit": "hour"}}),
        ("PATCH", f"{API}/system/settings", {"updates": {"ai_repeat_count_max": "5"}}),
        ("GET", f"{API}/system/users", None),
        ("POST", f"{API}/system/users", {"name": "v06 probe", "email": "v06probe@example.com", "start": "invite", "is_admin": False}),
        ("PUT", f"{API}/system/users/{target_uid}/admin", {"is_admin": True}),
        ("POST", f"{API}/system/users/{target_uid}/disable", {}),
        ("POST", f"{API}/system/users/{target_uid}/enable", {}),
        ("POST", f"{API}/system/users/{target_uid}/invite", {}),
        ("POST", f"{API}/system/users/{target_uid}/password-reset", {}),
        ("POST", f"{API}/system/users/{target_uid}/tfa-reset", {}),
    ]


def _send(client, method, url, body, headers):
    if body is None:
        return client.request(method, url, headers=headers)
    return client.request(method, url, json=body, headers=headers)


WATCHED = ["system_settings", "users", "user_project_roles", "workspace_members", "workspaces"]


@pytest.mark.parametrize("who,expected", [
    ("P", 403), ("M", 403), ("N", 403), ("R", 403),
    ("anon", 401), ("garbage", 401), ("revoked", 401),
])
def test_v06_without_system_permission_every_call_is_refused_and_nothing_changes(env, who, expected):
    before = _snapshot(env.db_path, WATCHED)
    seen = []
    for method, url, body in _system_calls("usr_n0076"):
        r = _send(env.client, method, url, body, env.h(who))
        seen.append((method, url.replace(API, ""), r.status_code))
    print(f"V06 {who}: {seen}")
    assert all(status == expected for _, _, status in seen), seen
    assert _snapshot(env.db_path, WATCHED) == before


def test_v06_admin_gets_the_existing_success_statuses(env):
    a = env.h("A")
    # GETs
    assert env.client.get(f"{API}/system/storage-root", headers=a).status_code == 200
    assert env.client.get(f"{API}/system/settings", headers=a).status_code == 200
    assert env.client.get(f"{API}/system/users", headers=a).status_code == 200
    # PATCHes
    r = env.client.patch(f"{API}/system/settings", json={"updates": {"scratch_ttl_value": "6", "scratch_ttl_unit": "hour"}}, headers=a)
    assert r.status_code == 200, r.text
    r = env.client.patch(f"{API}/system/settings", json={"updates": {"ai_repeat_count_max": "5"}}, headers=a)
    assert r.status_code == 200, r.text
    # users: create without workspace_id at all (the screen no longer sends one)
    created = env.client.post(f"{API}/system/users", json={"name": "v06 new", "email": "v06new@example.com", "start": "invite", "is_admin": False}, headers=a)
    assert created.status_code == 201, created.text
    new_uid = created.json()["user"]["user_id"]
    assert created.json()["invite_path"].startswith("/login?invite=")
    statuses = []
    for method, suffix, body in (
        ("POST", "invite", {}),
        ("PUT", "admin", {"is_admin": True}),
        ("PUT", "admin", {"is_admin": False}),
        ("POST", "password-reset", {}),
        ("POST", "tfa-reset", {}),
        ("POST", "disable", {}),
        ("POST", "enable", {}),
    ):
        r = _send(env.client, method, f"{API}/system/users/{new_uid}/{suffix}", body, a)
        statuses.append((f"{method} {suffix} {body or ''}".strip(), r.status_code))
    print(f"V06 A statuses: POST /system/users=201, {statuses}")
    assert {status for _, status in statuses} == {200}, statuses


DELEGATED = ("system.settings.manage", "system.user.read", "system.user.create", "system.user.update")


def _delegate(env, uid="usr_dd0076"):
    """Test-only explicit delegation (D D): the four system permissions the the earlier implementation routes check,
    on a custom role assigned at __SYSTEM__. No migration ships these permission rows, so this is
    the shape a deployment would have only if someone created them — it is not deployment data."""
    from modules.flow_gate.rbac import permission_service, role_service

    store = env.store
    for permission_id in DELEGATED:
        store._execute(
            "INSERT OR IGNORE INTO permissions (permission_id, permission_name, created_at) VALUES (?, ?, '2026-10-20T00:00:00+09:00')",
            [permission_id, f"test-only 0076 {permission_id}"])
    store._execute("INSERT INTO roles (role_id, role_name, is_system, created_at, updated_at) VALUES ('role_t0076_delegate', 'delegate 0076', 0, '2026-10-20T00:00:00+09:00', '2026-10-20T00:00:00+09:00')")
    for permission_id in DELEGATED:
        store._execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ('role_t0076_delegate', ?)", [permission_id])
    role_service.assign_role(uid, role_service.SYSTEM_PROJECT, "role_t0076_delegate")
    permission_service.clear_all_cache()


# What the real router/auth/RBAC answered for D (is_admin=0, the four permissions delegated) on
# every the earlier implementation route, as observed. Pinned so a change in the server's behaviour shows up; it records
# the existing delegated-permission check, it is NOT an is_admin-only result and NOT a release basis.
D_OBSERVED = {
    ("GET", "/system/storage-root"): 200,
    ("GET", "/system/settings"): 200,
    ("PATCH", "/system/settings"): 200,
    ("GET", "/system/users"): 200,
    ("POST", "/system/users"): 201,
    ("PUT", "/system/users/usr_n0076/admin"): 200,
    ("POST", "/system/users/usr_n0076/disable"): 200,
    ("POST", "/system/users/usr_n0076/enable"): 200,
    ("POST", "/system/users/usr_n0076/invite"): 409,  # past RBAC: N is active, not invited (service rule)
    ("POST", "/system/users/usr_n0076/password-reset"): 200,
    ("POST", "/system/users/usr_n0076/tfa-reset"): 200,
}


def test_v06_d_delegated_observation_every_t3_route_and_what_it_changed(env):
    """D §5.1 D: is_admin=false with the target system permissions explicitly delegated, through the
    real router/auth/RBAC (no override, no conftest seed). Every §2.5/§2.7 call is made and its
    status and the tables it changed are recorded. Nothing here requires a 403: the server's check
    is a permission check, and with the delegation it lets D through — the observed result."""
    env.client.get(f"{API}/system/users", headers=env.h("A"))  # the list GET may create the default workspace; start after it
    before_undelegated = [(m, u.replace(API, ""), _send(env.client, m, u, b, env.h("D")).status_code)
                          for m, u, b in _system_calls("usr_n0076")[:2]]
    assert {status for _, _, status in before_undelegated} == {403}, before_undelegated  # role_viewer only

    _delegate(env)
    db_before = _snapshot(env.db_path)
    seen = []
    for method, url, body in _system_calls("usr_n0076"):
        r = _send(env.client, method, url, body, env.h("D"))
        seen.append((method, url.replace(API, ""), r.status_code))
    db_after = _snapshot(env.db_path)
    changed = sorted(name for name in db_after if db_after[name] != db_before.get(name))
    removed = {name: sorted(set(db_before[name]) - set(db_after[name])) for name in ("workspace_members", "workspaces", "workspace_project_access", "workspace_ai_allowlist")}
    settings = _raw_settings(env.db_path, ("scratch_ttl_value", "scratch_ttl_unit", "ai_repeat_count_max"))
    print(f"V06 D(delegated {list(DELEGATED)}) before delegation: {before_undelegated}")
    print(f"V06 D(delegated) statuses: {seen}")
    print(f"V06 D(delegated) tables changed: {changed}; workspace rows removed: {removed}; scratch/ceiling now: {settings}")

    assert seen == [(method, path, D_OBSERVED[(method, path)]) for method, path, _ in seen]
    assert settings == ("6", "hour", "5")
    # The change range of these calls: settings rows, users and their roles/sessions — no Workspace
    # row removed, no other setting touched.
    assert set(changed) <= {"system_settings", "users", "user_project_roles", "auth_sessions", "refresh_tokens", "totp_backup_codes", "workspace_members", "user_invites"}, changed
    assert all(not rows for rows in removed.values())
    others_before = {r for r in db_before["system_settings"] if "scratch_ttl" not in r and "ai_repeat_count_max" not in r}
    others_after = {r for r in db_after["system_settings"] if "scratch_ttl" not in r and "ai_repeat_count_max" not in r}
    assert others_after == others_before




D_ADMIN_FLAG_OBSERVED = {
    "self_admin": (200, True),
    "disable_admin": (200, "disabled"),
}


# ── V11: system Root, read only ──────────────────────────────────────────────



# ── V12: Scratch pair, normal save and validation ────────────────────────────



# ── V13: Scratch atomicity on the deployment store ───────────────────────────



def _fail_second_settings_write(store, monkeypatch):
    original = store._execute
    count = {"n": 0}

    def execute(sql, params=None):
        if "INSERT INTO system_settings" in sql:
            count["n"] += 1
            if count["n"] == 2:
                raise sqlite3.OperationalError("injected failure on the second settings write")
        return original(sql, params)

    monkeypatch.setattr(store, "_execute", execute)
    return count










# ── V14 / V15: repeat ceiling ────────────────────────────────────────────────





# ── V20 (this screen's share): hidden values and files are kept ──────────────



# ── V19: user protections ────────────────────────────────────────────────────
