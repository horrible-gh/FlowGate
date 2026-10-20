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
    """Test-only explicit delegation (D §5.1 D): the four system permissions the T#3 routes check,
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
# every T#3 route, as observed. Pinned so a change in the server's behaviour shows up; it records
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


def test_v06_d_delegated_observation_administrator_flag_changes(env):
    """Observation of what system.user.update lets a delegated non-admin do to administrator flags:
    raise itself to administrator and disable an administrator. Recorded as the existing server
    behaviour for the 0075 RBAC follow-up; not changed here and not a release basis."""
    _delegate(env)
    d = env.h("D")
    self_admin = env.client.put(f"{API}/system/users/usr_dd0076/admin", json={"is_admin": True}, headers=d)
    users = {u["user_id"]: u for u in env.client.get(f"{API}/system/users", headers=env.h("A")).json()["users"]}
    print(f"V06 D(delegated) PUT own admin=true: {self_admin.status_code} {self_admin.json()}; D is_admin now={users['usr_dd0076']['is_admin']}")
    disable_admin = env.client.post(f"{API}/system/users/usr_x0076/disable", json={}, headers=d)
    users = {u["user_id"]: u for u in env.client.get(f"{API}/system/users", headers=env.h("A")).json()["users"]}
    print(f"V06 D(delegated) disables administrator X: {disable_admin.status_code}; X state now={users['usr_x0076']['state']}")
    assert (self_admin.status_code, users["usr_dd0076"]["is_admin"]) == D_ADMIN_FLAG_OBSERVED["self_admin"]
    assert (disable_admin.status_code, users["usr_x0076"]["state"]) == D_ADMIN_FLAG_OBSERVED["disable_admin"]


D_ADMIN_FLAG_OBSERVED = {
    "self_admin": (200, True),
    "disable_admin": (200, "disabled"),
}


# ── V11: system Root, read only ──────────────────────────────────────────────

def test_v11_storage_root_reports_stored_and_effective_separately(env, monkeypatch, tmp_path):
    stored = str(tmp_path / "stored-root")
    shadowing = str(tmp_path / "env-root")
    env.store._execute("UPDATE system_settings SET setting_value = ? WHERE setting_key = 'storage_root'", [stored])
    if env.store._fetch_one("SELECT 1 AS ok FROM system_settings WHERE setting_key = 'storage_root'") is None:
        env.store._execute("INSERT INTO system_settings (setting_key, setting_value, value_type, updated_at) VALUES ('storage_root', ?, 'string', '2026-10-20T00:00:00+09:00')", [stored])
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", shadowing)
    r = env.client.get(f"{API}/system/storage-root", headers=env.h("A"))
    assert r.status_code == 200
    body = r.json()
    print(f"V11 GET /system/storage-root: {body}")
    assert body["configured_value"] == stored
    assert body["effective_value"] == shadowing
    assert body["effective_source"] == "env"
    assert body["env_override"] is True
    assert body["project_id"] is None and body["project_override"] is None
    monkeypatch.delenv("FLOWGATE_STORAGE_DIR")
    body = env.client.get(f"{API}/system/storage-root", headers=env.h("A")).json()
    assert body["effective_value"] == stored and body["effective_source"] == "system_setting"
    assert body["env_override"] is False


# ── V12: Scratch pair, normal save and validation ────────────────────────────

def test_v12_scratch_pair_saved_in_one_patch_and_invalid_input_is_422_with_both_keys_unchanged(env):
    a = env.h("A")
    first = _settings_map(env.client.get(f"{API}/system/settings", headers=a).json())
    assert (first["scratch_ttl_value"], first["scratch_ttl_unit"]) == ("7", "day")
    r = env.client.patch(f"{API}/system/settings", json={"updates": {"scratch_ttl_value": "6", "scratch_ttl_unit": "hour"}}, headers=a)
    assert r.status_code == 200
    updated = {row["setting_key"]: (row["setting_value"], row["value_type"]) for row in r.json()["updated"]}
    print(f"V12 PATCH 6 hour → {r.status_code} updated={updated}")
    assert updated == {"scratch_ttl_value": ("6", "integer"), "scratch_ttl_unit": ("hour", "string")}
    again = _settings_map(env.client.get(f"{API}/system/settings", headers=a).json())
    assert (again["scratch_ttl_value"], again["scratch_ttl_unit"]) == ("6", "hour")
    assert _raw_settings(env.db_path) == ("6", "hour")

    refused = []
    for value, unit in (("0", "hour"), ("-1", "hour"), ("1.5", "hour"), ("6", "week"), ("999999999999999999", "day")):
        r = env.client.patch(f"{API}/system/settings", json={"updates": {"scratch_ttl_value": value, "scratch_ttl_unit": unit}}, headers=a)
        refused.append((value, unit, r.status_code, r.json().get("detail")))
        assert r.status_code == 422
        assert _raw_settings(env.db_path) == ("6", "hour")
    print(f"V12 refused: {refused}")
    after = _settings_map(env.client.get(f"{API}/system/settings", headers=a).json())
    assert (after["scratch_ttl_value"], after["scratch_ttl_unit"]) == ("6", "hour")


# ── V13: Scratch atomicity on the deployment store ───────────────────────────

def test_v13_deployment_store_has_a_real_transaction(env):
    from sqloader.sqlite3 import SQLiteWrapper

    from modules.flow_gate.db import connection
    from modules.flow_gate.settings import system_settings_service

    store = system_settings_service._connection.get_store()
    assert store is env.store and type(store) is connection.FlowGateStore
    assert isinstance(store._db, SQLiteWrapper)
    # The service only falls back to nullcontext when getattr(store, "transaction") is None.
    assert callable(getattr(store, "transaction", None))


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


def test_v13_second_write_failure_rolls_both_keys_back(env, monkeypatch):
    a = env.h("A")
    before = _raw_settings(env.db_path)
    assert before == ("7", "day")
    with monkeypatch.context() as patch:
        count = _fail_second_settings_write(env.store, patch)
        r = env.unsafe_client.patch(f"{API}/system/settings", json={"updates": {"scratch_ttl_value": "6", "scratch_ttl_unit": "hour"}}, headers=a)
    print(f"V13 second write fails: HTTP {r.status_code}, writes attempted={count['n']}, raw after={_raw_settings(env.db_path)}")
    assert count["n"] == 2
    assert r.status_code == 500
    assert _raw_settings(env.db_path) == before
    reread = _settings_map(env.client.get(f"{API}/system/settings", headers=a).json())
    assert (reread["scratch_ttl_value"], reread["scratch_ttl_unit"]) == before


def test_v13_exception_in_the_transaction_body_after_both_writes_rolls_both_keys_back(env, monkeypatch):
    """Both keys are written, then an exception is raised INSIDE the adapter's transaction block,
    right after its body yields — so the original context exits through its error/rollback path
    and never reaches its normal exit (where it would commit). This is "an exception in the
    transaction body before commit", not a failure of the commit call itself; a real commit-call
    failure is not exercised here. The V13 release evidence is the second-write failure above."""
    a = env.h("A")
    before = _raw_settings(env.db_path)
    adapter = env.store._db
    original = adapter.begin_transaction

    @contextmanager
    def raise_before_commit():
        with original() as txn:
            yield txn
            raise sqlite3.OperationalError("injected body exception after both writes, before commit")

    with monkeypatch.context() as patch:
        patch.setattr(adapter, "begin_transaction", raise_before_commit)
        r = env.unsafe_client.patch(f"{API}/system/settings", json={"updates": {"scratch_ttl_value": "30", "scratch_ttl_unit": "minute"}}, headers=a)
    print(f"V13 body exception before commit: HTTP {r.status_code}, raw after={_raw_settings(env.db_path)}")
    assert r.status_code == 500
    assert _raw_settings(env.db_path) == before
    reread = _settings_map(env.client.get(f"{API}/system/settings", headers=a).json())
    assert (reread["scratch_ttl_value"], reread["scratch_ttl_unit"]) == before


def test_v13_store_without_transaction_is_not_atomic(env, monkeypatch):
    """The nullcontext branch D §2.5 refuses to count as atomic: on a store that has no
    transaction() the first key survives a failed second write. The deployment store is not
    such a store (test above), which is what lets the Scratch editor ship."""
    from modules.flow_gate.settings import system_settings_service

    class NoTxnStore:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            if name in ("transaction", "with_transaction"):
                raise AttributeError(name)
            return getattr(self._inner, name)

    no_txn = NoTxnStore(env.store)
    assert getattr(no_txn, "transaction", None) is None
    with monkeypatch.context() as patch:
        _fail_second_settings_write(env.store, patch)
        patch.setattr(system_settings_service._connection, "get_store", lambda: no_txn)
        with pytest.raises(sqlite3.OperationalError):
            system_settings_service.set_values({"scratch_ttl_value": "6", "scratch_ttl_unit": "hour"}, updated_by="usr_a0076")
    print(f"V13 no-transaction store after failed 2nd write: raw={_raw_settings(env.db_path)}")
    assert _raw_settings(env.db_path) == ("6", "day")  # half written: not atomic


def test_v13_other_backends_transaction_api_presence():
    """Information only: whether sqloader's other adapters expose begin_transaction. The
    deployment DB type is sqlite3 (.env.sample); MySQL/PostgreSQL are not exercised here."""
    found = {}
    for module_name in ("sqloader.mysql", "sqloader.postgresql"):
        try:
            module = __import__(module_name, fromlist=["*"])
        except Exception as exc:  # driver not installed in this environment
            found[module_name] = f"import failed: {type(exc).__name__}"
            continue
        classes = [obj for obj in vars(module).values() if isinstance(obj, type) and obj.__module__ == module.__name__]
        found[module_name] = {cls.__name__: hasattr(cls, "begin_transaction") for cls in classes}
    print(f"V13 other adapters: {found}")


# ── V14 / V15: repeat ceiling ────────────────────────────────────────────────

def test_v14_lowering_the_ceiling_keeps_stored_picks_and_existing_reads_degrade(env):
    from modules.flow_gate.services.ai_invoke import admission, review

    a = env.h("A")
    assert env.client.patch(f"{API}/system/settings", json={"updates": {"ai_repeat_count_max": "10"}}, headers=a).status_code == 200
    # A paused chain whose review and restart picks are 8 (stored under ceiling 10).
    conn = sqlite3.connect(env.db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO ai_invoke_paused_chains (group_id, doc_ref, mode, paused_by, paused_at, docs_reached, "
        "created_at, updated_at, continuation_review_count_overrides, continuation_restart_max_attempts) "
        "VALUES ('g_0076', 'd_0076', 'continuous', 'usr_a0076', '2026-10-20T00:00:00+09:00', 0, "
        "'2026-10-20T00:00:00+09:00', '2026-10-20T00:00:00+09:00', '{\"1\": 8}', 8)")
    conn.commit()
    conn.close()
    assert review.resolve_review_count({1: 8}, 1) == 8
    assert admission._resolve_restart_max_attempts(8) == 9

    paused_before = _snapshot(env.db_path, ["ai_invoke_paused_chains"])
    everything_before = _snapshot(env.db_path)
    r = env.client.patch(f"{API}/system/settings", json={"updates": {"ai_repeat_count_max": "3"}}, headers=a)
    assert r.status_code == 200
    assert [(row["setting_key"], row["setting_value"]) for row in r.json()["updated"]] == [("ai_repeat_count_max", "3")]
    assert _settings_map(env.client.get(f"{API}/system/settings", headers=a).json())["ai_repeat_count_max"] == "3"
    assert _snapshot(env.db_path, ["ai_invoke_paused_chains"]) == paused_before
    everything_after = _snapshot(env.db_path)
    changed = sorted(name for name in everything_after if everything_after[name] != everything_before.get(name))
    print(f"V14 tables changed by the PATCH (and its auth): {changed}")
    assert set(changed) <= {"system_settings", "auth_sessions"}
    # The existing read paths now degrade: review → 0 (not reviewed), restart → default 1 (2 attempts).
    review_now = review.resolve_review_count({1: 8}, 1)
    restart_now = admission._resolve_restart_max_attempts(8)
    print(f"V14 after ceiling 3: resolve_review_count(8)={review_now}, _resolve_restart_max_attempts(8)={restart_now}")
    assert review_now == 0
    assert restart_now == 2


def test_v15_ceiling_bounds_and_codes(env):
    a = env.h("A")
    results = []
    for value, expected in (("1", 200), ("30", 200), ("0", 422), ("31", 422), ("2.5", 422)):
        r = env.client.patch(f"{API}/system/settings", json={"updates": {"ai_repeat_count_max": value}}, headers=a)
        results.append((value, r.status_code, r.json().get("detail") if r.status_code != 200 else r.json()["updated"][0]["setting_value"]))
        assert r.status_code == expected, (value, r.text)
        if expected == 422:
            assert r.json()["detail"] == {"code": "ai_repeat_count_out_of_range", "params": {"min": 1, "max": 30}}
    print(f"V15: {results}")
    assert _settings_map(env.client.get(f"{API}/system/settings", headers=a).json())["ai_repeat_count_max"] == "30"


# ── V20 (this screen's share): hidden values and files are kept ──────────────

def test_v20_settings_and_user_saves_touch_only_their_own_rows_and_no_file(env, monkeypatch, tmp_path):
    """The saves 시스템 › 일반 / 사용자 can make — the Scratch pair, the repeat ceiling and the user
    actions — leave every other table and every file under the (isolated) storage root as they
    were. Async GC is not part of this: the settings write does not run it."""
    import hashlib

    storage = tmp_path / "storage"
    for rel, data in (("documents/p1/0001-R.md", b"doc"), ("src/p1/app.py", b"print(1)"),
                      ("scratch/run_1/out.txt", b"tmp"), ("keys/secret.key", b"k" * 32),
                      ("worktrees/p1/g1/README.md", b"wt")):
        path = storage / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(storage))

    def manifest():
        return sorted((str(p.relative_to(storage)), hashlib.sha256(p.read_bytes()).hexdigest())
                      for p in storage.rglob("*") if p.is_file())

    a = env.h("A")
    env.client.get(f"{API}/system/users", headers=a)  # the list GET may create the default workspace
    files_before, db_before = manifest(), _snapshot(env.db_path)
    calls = [
        ("PATCH", f"{API}/system/settings", {"updates": {"scratch_ttl_value": "6", "scratch_ttl_unit": "hour"}}),
        ("PATCH", f"{API}/system/settings", {"updates": {"ai_repeat_count_max": "4"}}),
        ("POST", f"{API}/system/users", {"name": "v20 new", "email": "v20new@example.com", "start": "invite", "is_admin": False}),
        ("POST", f"{API}/system/users/usr_m0076/disable", {}),
        ("POST", f"{API}/system/users/usr_m0076/enable", {}),
        ("PUT", f"{API}/system/users/usr_n0076/admin", {"is_admin": True}),
        ("PUT", f"{API}/system/users/usr_n0076/admin", {"is_admin": False}),
        ("POST", f"{API}/system/users/usr_n0076/tfa-reset", {}),
        ("GET", f"{API}/system/storage-root", None),
        ("GET", f"{API}/system/settings", None),
        ("GET", f"{API}/system/users", None),
    ]
    for method, url, body in calls:
        assert _send(env.client, method, url, body, a).status_code in (200, 201)
    db_after = _snapshot(env.db_path)
    changed = sorted(name for name in db_after if db_after[name] != db_before.get(name))
    removed = {name: sorted(set(db_before[name]) - set(db_after[name])) for name in ("workspace_members", "workspaces", "workspace_project_access", "workspace_ai_allowlist")}
    print(f"V20 tables changed: {changed}; workspace rows removed: {removed}; files unchanged: {manifest() == files_before}")
    assert manifest() == files_before
    assert set(changed) <= {"system_settings", "users", "user_project_roles", "auth_sessions", "refresh_tokens", "totp_backup_codes", "workspace_members"}  # disable ends sessions
    assert all(not rows for rows in removed.values())
    settings_before = {r for r in db_before["system_settings"] if "scratch_ttl" not in r and "ai_repeat_count_max" not in r}
    settings_after = {r for r in db_after["system_settings"] if "scratch_ttl" not in r and "ai_repeat_count_max" not in r}
    assert settings_after == settings_before  # storage_root, source_mode, … untouched


# ── V19: user protections ────────────────────────────────────────────────────

def test_v19_last_admin_and_self_disable_are_refused_other_changes_apply(env):
    a = env.h("A")
    users = lambda: {u["user_id"]: u for u in env.client.get(f"{API}/system/users", headers=a).json()["users"]}
    users()  # the list GET may create the default workspace (existing behaviour); start after it
    relations_before = _snapshot(env.db_path, ["workspace_members", "workspaces"])

    # A disabling A while another administrator (X) is active → the self guard.
    r = env.client.post(f"{API}/system/users/usr_a0076/disable", headers=a, json={})
    print(f"V19 A disables self (2 admins): {r.status_code} {r.json()}")
    assert r.status_code == 409 and r.json()["detail"] == {"code": "cannot_disable_self"}
    assert users()["usr_a0076"]["state"] == "active"

    # Leave A as the only active administrator.
    assert env.client.post(f"{API}/system/users/usr_x0076/disable", headers=a, json={}).status_code == 200
    r = env.client.put(f"{API}/system/users/usr_a0076/admin", headers=a, json={"is_admin": False})
    print(f"V19 last admin demoted: {r.status_code} {r.json()}")
    assert r.status_code == 409 and r.json()["detail"] == {"code": "last_admin"}
    r = env.client.post(f"{API}/system/users/usr_a0076/disable", headers=a, json={})
    print(f"V19 last admin disables self: {r.status_code} {r.json()}")
    assert r.status_code == 409 and r.json()["detail"] == {"code": "last_admin"}
    now = users()["usr_a0076"]
    assert now["is_admin"] is True and now["state"] == "active"

    # Another user's state changes are applied and read back.
    assert env.client.post(f"{API}/system/users/usr_m0076/disable", headers=a, json={}).json()["state"] == "disabled"
    assert users()["usr_m0076"]["state"] == "disabled"
    assert env.client.post(f"{API}/system/users/usr_m0076/enable", headers=a, json={}).json()["state"] == "active"
    assert users()["usr_m0076"]["state"] == "active"

    # Create without workspace_id: no membership row is created or removed.
    created = env.client.post(f"{API}/system/users", headers=a, json={"name": "v19 new", "email": "v19new@example.com", "start": "password", "is_admin": False})
    assert created.status_code == 201
    assert created.json()["user"]["workspaces"] == [] and created.json()["temp_password"]
    users()
    relations_after = _snapshot(env.db_path, ["workspace_members", "workspaces"])
    removed = {t: sorted(set(relations_before[t]) - set(relations_after[t])) for t in relations_before}
    added = {t: sorted(set(relations_after[t]) - set(relations_before[t])) for t in relations_before}
    print(f"V19 workspace rows removed={removed} added={added}")
    assert all(not rows for rows in removed.values())
