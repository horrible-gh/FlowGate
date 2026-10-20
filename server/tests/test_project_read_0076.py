"""flowgate.v02.0076 T#5 — the server side of the 프로젝트 조회 screens (D §2.3 / §2.4 / §2.8, §5.2 V24 · V25).

The new screens use three existing GETs: ``GET /projects?status=all`` (settings/routers/project_settings),
``GET /projects/{project_id}/storage-root`` (same router) and ``GET /projects/{project_id}/git/config``
(api/v1/git_routes). They are pinned here against the real routers mounted as ``routers/main.py`` does
(project_settings under ``/flowgate/api/v1``; git_routes, whose own prefix is ``/api/v1``, under
``/flowgate``; auth under ``/flowgate/auth``). Nothing in the request path is mocked — no
``get_current_user`` / ``require_permission`` override — and the database is a sqlite file built from
every migration and nothing else (``migrated_sqlite_db``), with the deployment store
(``FlowGateStore`` on sqloader's ``SQLiteWrapper``).

V24 is an OBSERVATION of the existing server permissions, not a release gate for these screens
(D §5.1): A must get the existing 200 contracts and anonymous / invalid / logged-out sessions the
existing 401. For P / M / N / R / D the observed status and response scope are printed and pinned so
a change shows up; a full project list or a non-member reading p1 is the known, unresolved 0075 RBAC
defect and is recorded as such — never as "RBAC resolved", and the GUI's administrator-only exposure
does not close it. D is is_admin=false with ``project.settings.read`` delegated on __SYSTEM__ by a
custom role inside the test (no migration ships that grant).

V25 (server part): every GET above, for every account, leaves the whole database (projects, project
settings, the Git config row with its encrypted secret and hidden fields) and the storage files
byte-for-byte as they were. The storage fixture is the V20 one: under the isolated root every project
here resolves to (FLOWGATE_STORAGE_DIR) there are documents, scratch, the Git master key file (the key
actually in use: no key in the environment / settings), a project source tree that is a real Git
repository and a linked Git worktree with an uncommitted change — all non-empty; the relative path and
sha256 of every file under that root (``.git`` internals included) are compared before and after.
"""
from __future__ import annotations

import base64
import hashlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

PASSWORD = "Projects0076!Passw0rd"
API = "/flowgate/api/v1"
P1 = "p1_pr0076"
P2 = "p2_pr0076"
P1_OVERRIDE = "/data/p1-override-0076"
SYSTEM_ROOT = "/srv/flowgate-system-0076"
SECRET = "ghp_projectread0076secretvalue"
ACCOUNTS = ("A", "P", "M", "N", "R", "D")
UNAUTHENTICATED = ("anon", "garbage", "revoked")


def _snapshot(db_path):
    conn = sqlite3.connect(db_path)
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {name: sorted(repr(tuple(r)) for r in conn.execute(f"SELECT * FROM {name}").fetchall()) for name in names}
    finally:
        conn.close()


def _session_columns_changed(db_path, before_rows):
    """auth_sessions columns whose value moved between two reads, keyed by the session row id."""
    conn = sqlite3.connect(db_path)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(auth_sessions)")]
        after_rows = {r[0]: r for r in conn.execute("SELECT * FROM auth_sessions")}
    finally:
        conn.close()
    moved = set()
    for key, row in after_rows.items():
        old = before_rows.get(key)
        for i, col in enumerate(cols):
            if old is None or old[i] != row[i]:
                moved.add(col)
    return sorted(moved), sorted(after_rows)


def _session_rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {r[0]: r for r in conn.execute("SELECT * FROM auth_sessions")}
    finally:
        conn.close()


def _manifest(root: Path):
    return sorted(
        (p.relative_to(root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir")
        for p in root.rglob("*")
    )


def _git(root, *args):
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr
    return result.stdout


def _put(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


P1_NAME = "P1 read 0076"
KEY_FILE = ".flowgate-git-key"
SRC_MAIN = f"src/{P1_NAME}/main"  # storage/paths.src_root(project_name, "main")
WORKTREE = f"src/{P1_NAME}/grp_0076"  # a group branch worktree beside it (src_root(project_name, branch))
# V20's isolated categories, each with at least one non-empty file under the storage root.
CATEGORIES = {
    "documents": "documents/",
    "scratch": "scratch/",
    "key": KEY_FILE,
    "src": f"{SRC_MAIN}/",
    "worktree": f"{WORKTREE}/",
}


def _build_storage(env_root: Path) -> None:
    """The V20 file fixture under the isolated storage root (documents / scratch / key / src / worktree)."""
    _put(env_root, f"documents/{P1}/doc.md", "p1 doc")
    _put(env_root, "scratch/note.txt", "scratch")
    # The Git master key the server really uses here (credentials._get_current_key: env / settings
    # first, then this file under the storage root) — the environment and settings carry none.
    (env_root / KEY_FILE).write_text(base64.b64encode(b"k" * 32).decode("ascii"), encoding="ascii")
    main = env_root / SRC_MAIN
    _put(main, "README.md", "# p1 read 0076\n")
    _put(main, "app/main.py", "print('p1 0076')\n")
    _git(main, "init", "-b", "main")
    _git(main, "config", "user.name", "Test")
    _git(main, "config", "user.email", "test@example.test")
    _git(main, "config", "core.autocrlf", "false")
    _git(main, "add", ".")
    _git(main, "commit", "-m", "initial")
    _git(main, "worktree", "add", "-b", "grp_0076", str(env_root / WORKTREE), "main")
    worktree = env_root / WORKTREE
    _put(worktree, "app/feature.py", "FEATURE = 76\n")
    _git(worktree, "add", "app/feature.py")
    _git(worktree, "commit", "-m", "feature")
    _put(worktree, "app/wip.py", "WIP = 'uncommitted'\n")  # a working change a read must not touch


def _categories(manifest) -> dict[str, int]:
    """Non-empty files per V20 category in a manifest (a file whose sha256 is not the empty one)."""
    empty = hashlib.sha256(b"").hexdigest()
    return {
        name: sum(1 for path, digest in manifest
                  if digest not in ("dir", empty) and (path == prefix or path.startswith(prefix)))
        for name, prefix in CATEGORIES.items()
    }


class Env:
    def __init__(self, db_path, store, client, env_root):
        self.db_path = db_path
        self.store = store
        self.client = client
        self.env_root = env_root
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

    def get(self, url, who):
        return self.client.get(url, headers=self.h(who))


@pytest.fixture
def env(migrated_sqlite_db, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqloader.sqlite3 import SQLiteWrapper

    import modules.flow_gate.db.connection as connection
    from modules.flow_gate.api.v1.git_routes import router as git_router
    from modules.flow_gate.auth import auth_cache
    from modules.flow_gate.auth.auth_api import router as auth_router
    from modules.flow_gate.auth.password import hash_password
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.db import projects as projects_db
    from modules.flow_gate.db import system_settings as system_settings_db
    from modules.flow_gate.db.connection import now_iso
    from modules.flow_gate.rbac import permission_service, role_service
    from modules.flow_gate.services.git.credentials import encrypt_secret
    from modules.flow_gate.settings.routers.project_settings import router as project_router

    # FLOWGATE_STORAGE_DIR shadows p1's override (V22's fixture) and is the isolated V20 file root:
    # documents / scratch / the Git key file / p1's source repository / a linked worktree.
    env_root = tmp_path / "env-root"
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(env_root))
    # credentials._load_key_material reads the environment, then config.settings (when importable).
    app_config = sys.modules.get("config")
    for name in ("FLOWGATE_GIT_ENCRYPT_KEY", "FLOWGATE_GIT_ENCRYPT_KEY_PREV"):
        monkeypatch.delenv(name, raising=False)
        if app_config is not None and hasattr(app_config, "settings"):
            monkeypatch.setattr(app_config.settings, name, None, raising=False)
    _build_storage(env_root)

    db_path = migrated_sqlite_db("test_project_read_0076.db")
    store = object.__new__(connection.FlowGateStore)
    store._db = SQLiteWrapper(db_path)
    store._sq = None
    monkeypatch.setattr(connection, "STORE", store)
    auth_cache.invalidate_everything()
    permission_service.clear_all_cache()

    now = now_iso()
    store._execute(
        "INSERT INTO projects (project_id, project_name, description, is_active, created_at, updated_at) "
        "VALUES (?, ?, 'first', 1, ?, ?)", [P1, P1_NAME, now, now])
    store._execute(
        "INSERT INTO projects (project_id, project_name, description, is_active, created_at, updated_at) "
        "VALUES (?, 'P2 read 0076', NULL, 0, ?, ?)", [P2, now, now])
    projects_db.upsert_settings(P1, {"storage_root_override": P1_OVERRIDE})
    system_settings_db.set_value("storage_root", SYSTEM_ROOT)
    db_git.upsert_config(P1, {
        "repo_url": "https://git.example.com/team/p1.git", "provider": "gitea", "username": "bot-0076",
        "secret_enc": encrypt_secret(SECRET), "base_branch": "develop", "default_finalize_action": "push",
        "enabled": True, "translate_url": "http://translate.local", "author_name": "P1 Bot",
        "author_email": "p1-bot@example.com", "tr_scope_stage": "warn",
    })

    people = {
        "pr_a0076": ("usr_pr_a0076", 1, "role_admin", None),
        "pr_p0076": ("usr_pr_p0076", 0, "role_viewer", "role_admin"),
        "pr_m0076": ("usr_pr_m0076", 0, "role_viewer", "role_worker"),
        "pr_n0076": ("usr_pr_n0076", 0, "role_viewer", None),
        "pr_r0076": ("usr_pr_r0076", 0, "role_admin", None),
        "pr_d0076": ("usr_pr_d0076", 0, "role_viewer", None),
        "pr_x0076": ("usr_pr_x0076", 1, "role_admin", None),
    }
    for username, (uid, is_admin, system_role, p1_role) in people.items():
        store._execute(
            "INSERT INTO users (user_id, username, email, password, is_active, is_admin, "
            "first_login_required, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, 0, ?, ?)",
            [uid, username, f"{username}@example.com", hash_password(PASSWORD), is_admin, now, now])
        role_service.assign_role(uid, role_service.SYSTEM_PROJECT, system_role)
        if p1_role:
            role_service.assign_role(uid, P1, p1_role)

    # D: project.settings.read delegated on __SYSTEM__ (test-only custom role; no migration grants it).
    store._execute("INSERT OR IGNORE INTO permissions (permission_id, permission_name, created_at) VALUES "
                   "('project.settings.read', 'project.settings.read', ?)", [now])
    store._execute("INSERT INTO roles (role_id, role_name, is_system, created_at, updated_at) VALUES "
                   "('role_t0076_projread', 'project read delegate 0076', 0, ?, ?)", [now, now])
    store._execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ('role_t0076_projread', 'project.settings.read')")
    role_service.assign_role("usr_pr_d0076", role_service.SYSTEM_PROJECT, "role_t0076_projread")
    permission_service.clear_all_cache()

    app = FastAPI()
    app.include_router(auth_router, prefix="/flowgate/auth")
    app.include_router(project_router, prefix=API)
    app.include_router(git_router, prefix="/flowgate")
    e = Env(db_path, store, TestClient(app), env_root)
    for who, username in (("A", "pr_a0076"), ("P", "pr_p0076"), ("M", "pr_m0076"), ("N", "pr_n0076"),
                          ("R", "pr_r0076"), ("D", "pr_d0076"), ("X", "pr_x0076")):
        e.tokens[who], e.refresh[who] = e.login(username)
    out = e.client.post("/flowgate/auth/logout", json={"refresh_token": e.refresh["X"]}, headers=e.h("X"))
    assert out.status_code == 200
    e.tokens["revoked"] = e.tokens.pop("X")
    return e


CALLS = (
    ("list_active", f"{API}/projects?status=active"),
    ("list_all", f"{API}/projects?status=all"),
    ("p1_root", f"{API}/projects/{P1}/storage-root"),
    ("p1_git", f"{API}/projects/{P1}/git/config"),
)


def _scope(name, response):
    """What a 200 answer exposed: the project ids of a list, else whether p1's values came back."""
    if response.status_code != 200:
        return None
    body = response.json()
    if name.startswith("list"):
        return sorted(p["project_id"] for p in body["projects"])
    if name == "p1_root":
        return body.get("project_override")
    return body.get("configured")


# ── V24 A: the existing 200 contracts the screens read ──────────────────────

def test_v24_a_list_contract(env):
    r = env.get(f"{API}/projects?status=all", "A")
    assert r.status_code == 200, r.text
    rows = {p["project_id"]: p for p in r.json()["projects"]}
    assert {P1, P2} <= set(rows)
    for pid, active in ((P1, 1), (P2, 0)):
        row = rows[pid]
        assert {"project_id", "project_name", "description", "is_active", "modules"} <= set(row)
        assert row["is_active"] == active
        assert isinstance(row["modules"], list)
    assert rows[P1]["description"] == "first" and rows[P2]["description"] is None
    active = env.get(f"{API}/projects?status=active", "A").json()["projects"]
    assert P1 in {p["project_id"] for p in active} and P2 not in {p["project_id"] for p in active}
    print(f"V24 A list(status=all) ids: {sorted(rows)}; is_active types: {sorted({type(p['is_active']).__name__ for p in rows.values()})}")


def test_v24_a_storage_root_contract_with_shadowed_override(env):
    r = env.get(f"{API}/projects/{P1}/storage-root", "A")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "project_id": P1,
        "configured_value": SYSTEM_ROOT,
        "project_override": P1_OVERRIDE,
        "effective_value": str(env.env_root),
        "effective_source": "env",
        "env_override": True,
    }
    p2 = env.get(f"{API}/projects/{P2}/storage-root", "A").json()
    assert p2["project_override"] is None and p2["effective_source"] == "env"
    missing = env.get(f"{API}/projects/p404_pr0076/storage-root", "A")
    assert missing.status_code == 404
    print(f"V24 A p1 storage-root: {body}; p404 → {missing.status_code}")


def test_v24_a_git_config_contract(env):
    r = env.get(f"{API}/projects/{P1}/git/config", "A")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["configured"] is True
    config = body["config"]
    for key, value in (("project_id", P1), ("repo_url", "https://git.example.com/team/p1.git"), ("provider", "gitea"),
                       ("enabled", True), ("base_branch", "develop"), ("default_finalize_action", "push"),
                       ("author_name", "P1 Bot"), ("author_email", "p1-bot@example.com"), ("has_secret", True)):
        assert config[key] == value, key
    assert SECRET not in r.text  # only a masked form travels; the screen shows only has_secret
    assert config["secret_masked"] and config["secret_masked"] != SECRET
    unconfigured = env.get(f"{API}/projects/{P2}/git/config", "A").json()
    assert unconfigured == {"ok": True, "configured": False, "config": None}
    print(f"V24 A p1 git/config keys: {sorted(config)}; p2: {unconfigured}")


# ── V24 unauthenticated: existing 401 ────────────────────────────────────────

@pytest.mark.parametrize("who", UNAUTHENTICATED)
def test_v24_unauthenticated_401(env, who):
    seen = [(name, env.get(url, who).status_code) for name, url in CALLS]
    print(f"V24 {who}: {seen}")
    assert [status for _, status in seen] == [401, 401, 401, 401], seen


# ── V24 P / M / N / R / D: observation, pinned (NOT a release gate, NOT RBAC resolved) ─

# What the real router/auth/RBAC answered on the migration-only schema, as observed by this test's
# first run (name, status, scope). list scope = project ids returned; p1_root scope = project_override
# returned; p1_git scope = configured flag returned. Pinned so a server change shows up here.
# - Every signed-in account gets the lists unscoped by its project roles (get_current_user only):
#   status=active answers every active project (__SYSTEM__, p1 — p2 is inactive, so not there) and
#   status=all every project, inactive p2 included. Projects outside the account's membership are in
#   both: the unresolved 0075 defect, reproduced.
# - P (role_admin on p1) and M (role_worker on p1) get 403 on p1's own storage-root / git config: the
#   migration roles carry no ``project.settings.read`` (the 0075 permission-ID mismatch), so this is
#   NOT a correct per-project restriction either.
# - D reads p1 (not a member) through the delegated system permission: the existing system-role path.
_LISTS = [("list_active", 200, ["__SYSTEM__", P1]), ("list_all", 200, ["__SYSTEM__", P1, P2])]
_DENIED = [("p1_root", 403, None), ("p1_git", 403, None)]
OBSERVED: dict[str, list] = {
    "P": _LISTS + _DENIED,
    "M": _LISTS + _DENIED,
    "N": _LISTS + _DENIED,
    "R": _LISTS + _DENIED,
    "D": _LISTS + [("p1_root", 200, P1_OVERRIDE), ("p1_git", 200, True)],
}


@pytest.mark.parametrize("who", ("P", "M", "N", "R", "D"))
def test_v24_observation_non_admins(env, who):
    seen = []
    for name, url in CALLS:
        r = env.get(url, who)
        seen.append((name, r.status_code, _scope(name, r)))
    print(f"V24 {who}: {seen}")
    lists = [s for s in seen if s[0].startswith("list")]
    full_list = [s for s in lists if s[1] == 200 and P1 in (s[2] or []) and (s[0] == "list_active" or P2 in (s[2] or []))]
    if full_list:
        print(f"V24 {who}: UNRESOLVED 0075 defect reproduced - lists unscoped by membership are returned to a "
              f"non-administrator (status=active: every active project; status=all: every project): {full_list}")
    if who in ("N", "R", "D") and any(s[1] == 200 for s in seen if s[0].startswith("p1_")):
        print(f"V24 {who}: a non-member read p1's storage-root / git config (system-role path; recorded, not resolved)")
    assert seen == OBSERVED[who], seen


# ── V25 server part: no GET changes the database or the storage files ───────

def test_v25_reads_change_nothing(env):
    before_db = _snapshot(env.db_path)
    before_sessions = _session_rows(env.db_path)
    before_files = _manifest(env.env_root)
    # The fixture really holds every V20 category, and the key file is the key in use.
    per_category = _categories(before_files)
    assert all(per_category.values()), per_category
    assert os.environ.get("FLOWGATE_GIT_ENCRYPT_KEY") is None
    before_key = (env.env_root / KEY_FILE).read_bytes()
    worktree_status = _git(env.env_root / WORKTREE, "status", "--porcelain")
    statuses = []
    for who in ACCOUNTS + UNAUTHENTICATED:
        for _name, url in CALLS + (("p2_root", f"{API}/projects/{P2}/storage-root"), ("p2_git", f"{API}/projects/{P2}/git/config")):
            statuses.append(env.get(url, who).status_code)
    after_db = _snapshot(env.db_path)
    after_files = _manifest(env.env_root)
    after_key = (env.env_root / KEY_FILE).read_bytes()
    changed = sorted(name for name in after_db if after_db[name] != before_db.get(name))
    # get_current_user's own session bookkeeping is the one write a signed-in GET makes; it is the
    # auth layer's, not a project / settings / Git value. Recorded with the columns that moved.
    moved, session_ids = _session_columns_changed(env.db_path, before_sessions)
    print(f"V25 {len(statuses)} GETs, statuses {sorted(set(statuses))}; tables changed: {changed}; "
          f"auth_sessions columns moved: {moved}")
    files = [entry for entry in after_files if entry[1] != "dir"]
    print(f"V25/V20 manifest of {env.env_root.name}: {len(after_files)} entries ({len(files)} files, "
          f"{len(after_files) - len(files)} dirs); non-empty files per category {per_category}; "
          f"identical before/after: {after_files == before_files}")
    assert [name for name in changed if name != "auth_sessions"] == []
    assert session_ids == sorted(before_sessions)  # no session created or removed
    assert after_files == before_files
    assert after_key == before_key
    assert _git(env.env_root / WORKTREE, "status", "--porcelain") == worktree_status == "?? app/wip.py\n"
    git_row = sqlite3.connect(env.db_path).execute(
        "SELECT secret_enc, translate_url, tr_scope_stage, username FROM project_git_config WHERE project_id = ?", [P1]).fetchone()
    assert git_row[1:] == ("http://translate.local", "warn", "bot-0076") and git_row[0]


def test_v20_git_config_read_uses_the_key_file(env):
    """The fixture's key file is the key in use: A's git/config decrypts the stored secret with it."""
    from modules.flow_gate.services.git import credentials

    assert credentials._load_key_material(credentials.SECRET_ENV_KEY) is None  # no env / settings key
    assert credentials._key_file_path() == env.env_root / KEY_FILE
    assert credentials._get_current_key() == b"k" * 32
    r = env.get(f"{API}/projects/{P1}/git/config", "A")
    assert r.status_code == 200, r.text
    config = r.json()["config"]
    assert config["has_secret"] is True
    assert config["secret_masked"] and config["secret_masked"] != SECRET
    assert config["secret_masked"][:4] == SECRET[:4] and config["secret_masked"][-4:] == SECRET[-4:]
