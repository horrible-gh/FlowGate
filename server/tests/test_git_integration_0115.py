"""Git integration (flowgate.default.0115) — db + service test suite.

Covers:
  - branch name generation / sanitization                      → L0006 §2.1
  - secret masking + AES-GCM encrypt/decrypt roundtrip          → L0006 §2.3
  - config CRUD: masking, keep/clear secret protocol, URL/enum
    validation, delete                                          → P0005 §1·§2
  - effective source-root resolution: fallback-first            → L0006 §2.2·§4.1
  - project git lock: acquire / busy / release / transfer       → L0006 §2.8
  - finalize guards (non-integrated group → 409)                → L0006 §4.2
  - REAL git end-to-end (skipped when git is absent): clone +
    worktree provisioning (idempotent), finalize merge/push/wait,
    conflict session → resolve/abort                            → L0006 §2.4~§2.7

Environment: TESTING=1, temporary SQLite with the real sqlite migrations
(mirrors test_project_test_commands.py), temporary FLOWGATE_STORAGE_DIR, and a
fixed FLOWGATE_GIT_ENCRYPT_KEY.
"""
from __future__ import annotations

import base64
import os
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_GIT_ENCRYPT_KEY"] = base64.b64encode(b"K" * 32).decode()

_TMP_STORAGE = tempfile.mkdtemp(prefix="fg-git-test-storage-")
os.environ["FLOWGATE_STORAGE_DIR"] = _TMP_STORAGE

import sys

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not _GIT, reason="git binary not available")


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetchone(self):
        row = self._cur.fetchone() if self._cur else None
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()] if self._cur else []


class _MockDB:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
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
        yield _MockTxn(self._conn)

    def close(self):
        self._conn.close()


@pytest.fixture(scope="module")
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    mock_db = _MockDB(db_path)
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    mock_db._conn.commit()
    yield mock_db, db_path
    mock_db.close()
    os.unlink(db_path)


@pytest.fixture(scope="module", autouse=True)
def patch_store(tmp_db):
    mock_db, _ = tmp_db
    from modules.flow_gate.db import connection as conn_mod

    original_store = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original_store


@pytest.fixture(scope="module")
def seed(tmp_db):
    from modules.flow_gate.db import projects

    projects.create({"project_id": "gitprj", "project_name": "GitProj"})
    projects.create({"project_id": "plainprj", "project_name": "PlainProj"})
    yield


def _migration_applied(tmp_db) -> bool:
    mock_db, _ = tmp_db
    row = mock_db.fetch_one(
        "SELECT 1 AS ok FROM sqlite_master WHERE type='table' AND name='project_git_config'"
    )
    return row is not None


# ── migration 056 sanity ──────────────────────────────────────────────────────

class TestMigration:
    def test_tables_exist(self, tmp_db):
        assert _migration_applied(tmp_db), "056_git_integration.sql did not apply"

    def test_grant_group_id_column(self, tmp_db):
        mock_db, _ = tmp_db
        cols = [r["name"] for r in mock_db.fetch_all("PRAGMA table_info(remote_tool_grant)")]
        assert "group_id" in cols


# ── branch naming (L0006 §2.1) ───────────────────────────────────────────────

class TestBranchName:
    def test_standard(self):
        from modules.flow_gate.services import git_service as svc

        assert (
            svc.worktree_branch_name("flowgate", "default", "flowgate.default.0115")
            == "flowgate_default_0115"
        )

    def test_sanitize_rules(self):
        from modules.flow_gate.services import git_service as svc

        assert svc.sanitize_branch("My Proj//módule.x") == "my-proj-m-dule.x"
        assert svc.sanitize_branch("-..--UPPER--..-") == "upper"
        assert len(svc.sanitize_branch("a" * 300)) == svc.BRANCH_MAX_LEN

    def test_invalid_raises(self):
        from modules.flow_gate.services import git_service as svc

        with pytest.raises(svc.GitServiceError):
            svc.sanitize_branch("///")
        with pytest.raises(svc.GitServiceError):
            svc.sanitize_branch("a..b")  # git refname escape sequences stay banned


# ── secret handling (L0006 §2.3) ─────────────────────────────────────────────

class TestSecrets:
    def test_mask(self):
        from modules.flow_gate.services import git_service as svc

        assert svc.mask_secret("short") == "********"
        masked = svc.mask_secret("ghp_AbCdEfGh1234567890IjKlMnOpQrStWXYZ")
        assert masked.startswith("ghp_") and masked.endswith("WXYZ") and "*" * 12 in masked

    def test_roundtrip(self):
        from modules.flow_gate.services import git_service as svc

        enc = svc.encrypt_secret("token-123")
        assert enc != "token-123"
        assert svc.decrypt_secret(enc) == "token-123"

    def test_wrong_key_unreadable(self):
        from modules.flow_gate.services import git_service as svc

        enc = svc.encrypt_secret("token-456")
        old = os.environ["FLOWGATE_GIT_ENCRYPT_KEY"]
        os.environ["FLOWGATE_GIT_ENCRYPT_KEY"] = base64.b64encode(b"X" * 32).decode()
        try:
            with pytest.raises(svc.GitServiceError) as exc:
                svc.decrypt_secret(enc)
            assert exc.value.code == "git_secret_unreadable"
        finally:
            os.environ["FLOWGATE_GIT_ENCRYPT_KEY"] = old


# ── config CRUD (P0005 §1·§2) ────────────────────────────────────────────────

class TestConfig:
    def test_unconfigured_view(self, seed):
        from modules.flow_gate.services import git_service as svc

        view = svc.get_config_view("plainprj")
        assert view == {"ok": True, "configured": False, "config": None}

    def test_save_and_mask(self, seed):
        from modules.flow_gate.services import git_service as svc

        out = svc.save_config("gitprj", {
            "repo_url": "https://example.com/team/repo.git",
            "provider": "github",
            "username": "bot",
            "secret": "ghp_AbCdEfGh1234567890IjKlMnOpQrStWXYZ",
            "base_branch": "main",
            "default_finalize_action": "merge",
            "enabled": True,
        })
        cfg = out["config"]
        assert out["configured"] is True
        assert cfg["has_secret"] is True
        assert cfg["secret_masked"].startswith("ghp_")
        assert "ghp_AbCdEfGh" not in str(cfg)  # plaintext never leaves the service

    def test_secret_keep_and_clear(self, seed):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        before = db_git.get_config("gitprj")["secret_enc"]
        out = svc.save_config("gitprj", {
            "repo_url": "https://example.com/team/repo.git",
            "secret": None,  # keep
            "enabled": True,
        })
        assert db_git.get_config("gitprj")["secret_enc"] == before
        assert out["config"]["has_secret"] is True

        svc.save_config("gitprj", {
            "repo_url": "https://example.com/team/repo.git",
            "secret": "",  # clear
            "enabled": True,
        })
        assert db_git.get_config("gitprj")["secret_enc"] is None

    def test_url_validation(self, seed):
        from modules.flow_gate.services import git_service as svc

        for bad in ("not-a-url", "ftp://x/y", "https://user:pw@host/repo.git"):
            with pytest.raises(svc.GitServiceError) as exc:
                svc.save_config("gitprj", {"repo_url": bad})
            assert exc.value.status == 422
        # ssh scp-like form is allowed
        out = svc.save_config("gitprj", {"repo_url": "git@github.com:org/repo.git"})
        assert out["configured"] is True

    def test_enum_validation(self, seed):
        from modules.flow_gate.services import git_service as svc

        with pytest.raises(svc.GitServiceError):
            svc.save_config("gitprj", {"repo_url": "https://x/y.git", "provider": "svn"})
        with pytest.raises(svc.GitServiceError):
            svc.save_config("gitprj", {
                "repo_url": "https://x/y.git", "default_finalize_action": "rebase",
            })

    def test_delete(self, seed):
        from modules.flow_gate.services import git_service as svc

        assert svc.delete_config("gitprj")["deleted"] is True
        assert svc.delete_config("gitprj")["deleted"] is False
        assert svc.get_config_view("gitprj")["configured"] is False


# ── effective source-root resolution (L0006 §2.2·§4.1 — fallback first) ─────

class TestEffectiveSrcRoot:
    def test_fallbacks(self, seed):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        # no group / no config
        assert svc.effective_src_root("gitprj", None) is None
        assert svc.effective_src_root("gitprj", "gitprj.default.0001") is None
        # config exists but disabled
        svc.save_config("gitprj", {"repo_url": "https://x/y.git", "enabled": False})
        assert svc.effective_src_root("gitprj", "gitprj.default.0001") is None
        # enabled but no worktree ledger entry
        svc.save_config("gitprj", {"repo_url": "https://x/y.git", "enabled": True})
        assert svc.effective_src_root("gitprj", "gitprj.default.0001") is None
        # ledger entry without a real directory → still fallback (E7/E13)
        db_git.register_worktree("gitprj.default.0001", "gitprj", "gitprj_default_0001")
        assert svc.effective_src_root("gitprj", "gitprj.default.0001") is None
        # directory appears → worktree wins
        from modules.flow_gate.storage.paths import src_root

        wt = src_root("GitProj", "gitprj_default_0001")
        wt.mkdir(parents=True, exist_ok=True)
        resolved = svc.effective_src_root("gitprj", "gitprj.default.0001")
        assert resolved is not None and resolved.name == "gitprj_default_0001"
        # cleanup for later git e2e tests
        shutil.rmtree(wt)
        svc.delete_config("gitprj")

    def test_paths_resolver_group_param(self, seed):
        from modules.flow_gate.storage.paths import resolve_project_src_root

        # group-less call keeps the pre-0115 behavior (project branch folder)
        root = resolve_project_src_root("gitprj")
        assert root is not None and root.name == "main"
        # unknown group falls back identically
        with_group = resolve_project_src_root("gitprj", group_id="gitprj.default.9999")
        assert with_group == root


# ── project lock (L0006 §2.8) ────────────────────────────────────────────────

class TestLock:
    def test_acquire_busy_release_transfer(self, seed):
        from modules.flow_gate.db import git_integration as db_git

        assert db_git.try_acquire_lock("gitprj", "op:a") is True
        assert db_git.try_acquire_lock("gitprj", "op:b") is False
        db_git.release_lock("gitprj", "op:b")  # non-holder release is a no-op
        assert db_git.get_lock("gitprj")["holder"] == "op:a"
        db_git.transfer_lock("gitprj", "op:a", "merge:7")
        assert db_git.get_lock("gitprj")["holder"] == "merge:7"
        db_git.release_lock("gitprj", "merge:7")
        assert db_git.get_lock("gitprj") is None


# ── finalize guards (L0006 §4.2) ─────────────────────────────────────────────

class TestFinalizeGuards:
    def test_non_integrated_group(self, seed):
        from modules.flow_gate.services import git_service as svc

        state = svc.get_finalize_state("plainprj.default.0001")
        assert state["state"]["status"] == "none"
        with pytest.raises(svc.GitServiceError) as exc:
            svc.finalize("plainprj.default.0001", "merge")
        assert exc.value.status == 409


# ── REAL git end-to-end (L0006 §2.4~§2.7) ────────────────────────────────────

def _git(args, cwd=None, env_extra=None):
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t",
    })
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, env=env
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout


@pytest.fixture(scope="class")
def origin_repo(seed):
    """A local bare origin with an initial commit on main + enabled config."""
    from modules.flow_gate.services import git_service as svc

    tmp = Path(tempfile.mkdtemp(prefix="fg-git-origin-"))
    bare = tmp / "origin.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("hello\n", encoding="utf-8")
    (seedwt / "shared.txt").write_text("line1\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)

    svc.save_config("gitprj", {
        "repo_url": bare.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    yield {"bare": bare, "seedwt": seedwt, "tmp": tmp}
    svc.delete_config("gitprj")
    shutil.rmtree(tmp, ignore_errors=True)


@needs_git
class TestGitEndToEnd:
    GROUP = "gitprj.default.0100"

    def test_connection_ok(self, origin_repo):
        from modules.flow_gate.services import git_service as svc

        result = svc.test_connection("gitprj", {})
        assert result["reachable"] is True
        assert result["authenticated"] is True
        assert result["base_branch_exists"] is True

    def test_ensure_worktree_and_idempotence(self, origin_repo):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        assert svc.ensure_worktree("gitprj", "default", self.GROUP) == "ok"
        wt = src_root("GitProj", "gitprj_default_0100")
        assert wt.is_dir() and (wt / "README.md").is_file()
        state = db_git.get_state(self.GROUP)
        assert state["worktree_registered"] == 1
        assert state["branch"] == "gitprj_default_0100"
        # second call: idempotent (created=false path), still ok
        assert svc.ensure_worktree("gitprj", "default", self.GROUP) == "ok"
        # worker CRUD resolution now points at the worktree
        assert svc.effective_src_root("gitprj", self.GROUP) == wt.resolve()

    def test_finalize_wait_then_merge(self, origin_repo):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        wt = src_root("GitProj", "gitprj_default_0100")
        (wt / "work.txt").write_text("group work\n", encoding="utf-8")

        db_git.set_status(self.GROUP, "awaiting_choice")
        out = svc.finalize(self.GROUP, "wait")
        assert out["result"]["status"] == "waiting"

        state = svc.get_finalize_state(self.GROUP)["state"]
        assert state["status"] == "waiting"
        assert state["choices"] == ["merge", "push", "wait"]

        out = svc.finalize(self.GROUP, "merge")
        assert out["result"]["status"] == "merged"
        assert out["result"]["pushed"] is True
        assert out["result"]["merge_commit"]
        # origin main actually contains the group's work
        files = _git(
            ["ls-tree", "--name-only", "main"], cwd=origin_repo["bare"]
        ).split()
        assert "work.txt" in files
        # re-finalize is rejected (already finalized)
        with pytest.raises(svc.GitServiceError) as exc:
            svc.finalize(self.GROUP, "merge")
        assert exc.value.status == 409

    def test_push_action(self, origin_repo):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0101"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0101")
        (wt / "feature.txt").write_text("branch only\n", encoding="utf-8")
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "push")
        assert out["result"]["status"] == "pushed"
        heads = _git(["ls-remote", "--heads", str(origin_repo["bare"])])
        assert "refs/heads/gitprj_default_0101" in heads

    def test_conflict_resolve_flow(self, origin_repo):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0102"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0102")

        # group edits shared.txt …
        (wt / "shared.txt").write_text("group version\n", encoding="utf-8")
        # … while origin main moves the same line via the seed worktree
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)  # catch up (earlier merges landed)
        (seedwt / "shared.txt").write_text("mainline version\n", encoding="utf-8")
        _git(["commit", "-am", "mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]
        assert out["result"]["conflict_files"] == ["shared.txt"]
        # the conflict session holds the project lock (L0006 §2.8)
        assert db_git.get_lock("gitprj")["holder"] == f"merge:{merge_id}"
        # a competing finalize on another group is locked out (409 git_busy)
        group2 = "gitprj.default.0103"
        assert svc.ensure_worktree("gitprj", "default", group2) == "failed"  # lock busy

        conflicts = svc.list_conflicts(group, merge_id)
        assert conflicts["files"][0]["path"] == "shared.txt"
        assert "<<<<<<<" in conflicts["files"][0]["content"]
        assert conflicts["files"][0]["conflict_count"] == 1

        # markers left in the submitted content → 422, nothing written
        with pytest.raises(svc.GitServiceError) as exc:
            svc.resolve_conflicts(group, merge_id, [{
                "path": "shared.txt",
                "content": conflicts["files"][0]["content"],
            }], True)
        assert exc.value.code == "conflict_markers_remain"

        # a file outside the session → 422 (E12)
        with pytest.raises(svc.GitServiceError) as exc:
            svc.resolve_conflicts(group, merge_id, [{
                "path": "README.md", "content": "x\n",
            }], True)
        assert exc.value.status == 422

        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.txt",
            "content": "group version\nmainline version\n",
        }], True)
        assert out["result"]["status"] == "merged"
        assert out["result"]["pushed"] is True
        assert db_git.get_lock("gitprj") is None
        session = db_git.get_session(merge_id)
        assert session["status"] == "done"

    def test_abort_flow(self, origin_repo):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0104"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0104")
        (wt / "shared.txt").write_text("another group version\n", encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.txt").write_text("mainline again\n", encoding="utf-8")
        _git(["commit", "-am", "mainline again"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]

        out = svc.abort_merge(group, merge_id)
        assert out["result"]["status"] == "waiting"
        assert db_git.get_lock("gitprj") is None
        assert db_git.get_session(merge_id)["status"] == "aborted"
        # after abort the group can re-choose (wait keeps it re-selectable)
        state = svc.get_finalize_state(group)["state"]
        assert state["status"] == "waiting"


# ── base-slot provisioning: lossless adopt + ledger (flowgate.default.0161) ──

@pytest.fixture(scope="class")
def adopt_origin(seed):
    """A dedicated project whose base slot is OCCUPIED before provisioning
    (the B0001 situation), plus a local bare origin."""
    from modules.flow_gate.db import projects
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.storage.paths import src_root

    projects.create({"project_id": "adoptprj", "project_name": "AdoptProj"})
    tmp = Path(tempfile.mkdtemp(prefix="fg-git-adopt-"))
    bare = tmp / "origin.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("remote readme\n", encoding="utf-8")
    (seedwt / "shared.txt").write_text("remote version\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)

    # occupy the slot BEFORE git integration exists
    base = src_root("AdoptProj", "main")
    base.mkdir(parents=True, exist_ok=True)
    (base / "crud.py").write_text("local only\n", encoding="utf-8")
    (base / "shared.txt").write_text("local version\n", encoding="utf-8")

    svc.save_config("adoptprj", {
        "repo_url": bare.as_uri(),
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    yield {"bare": bare, "seedwt": seedwt, "tmp": tmp, "base": base}
    svc.delete_config("adoptprj")
    shutil.rmtree(tmp, ignore_errors=True)


@needs_git
class TestProvision0161:
    def test_judge_occupied_before(self, adopt_origin):
        from modules.flow_gate.services import git_service as svc

        assert svc._judge_base_slot(adopt_origin["base"], "main") == "occupied"
        view = svc.provision_view("adoptprj")
        assert view["configured"] is True
        assert view["base_path_state"] == "occupied"
        assert view["base_checkout_exists"] is False
        assert view["last_attempt"] is None  # no attempts recorded yet

    def test_adopt_is_lossless_and_records(self, adopt_origin):
        from modules.flow_gate.services import git_service as svc

        base = adopt_origin["base"]
        out = svc.provision_base("adoptprj", "manual")
        assert out["status"] == "ok"
        assert out["mode"] == "adopt"
        assert out["snapshot_commit"]  # local↔remote difference existed
        # lossless: pre-existing bytes are untouched (local content wins)
        assert (base / "crud.py").read_text(encoding="utf-8") == "local only\n"
        assert (base / "shared.txt").read_text(encoding="utf-8") == "local version\n"
        # remote-only file restored, NOT recorded as a deletion
        assert (base / "README.md").read_text(encoding="utf-8") == "remote readme\n"
        # completion: marker gone, branch ref established
        assert not (base / ".git" / "flowgate_adopt_pending").exists()
        assert svc._judge_base_slot(base, "main") == "checkout"
        # ledger + view
        view = svc.provision_view("adoptprj")
        assert view["base_checkout_exists"] is True
        assert view["last_attempt"]["result"] == "ok"
        assert view["last_attempt"]["trigger"] == "manual"
        assert view["adopt_snapshot"]["commit"] == out["snapshot_commit"]
        # the base checkout is clean → finalize's base_dirty guard passes
        assert svc._dirty(base) is False

    def test_reprovision_is_noop_without_record_update(self, adopt_origin):
        from modules.flow_gate.services import git_service as svc

        before = svc.provision_view("adoptprj")["last_attempt"]
        out = svc.provision_base("adoptprj", "manual")
        assert out["status"] == "ok"
        assert out["mode"] == "none"
        assert svc.provision_view("adoptprj")["last_attempt"] == before

    def test_worktree_after_adopt_and_finalize_carries_snapshot(self, adopt_origin):
        """B0001 end-to-end: the hook path succeeds on an occupied slot, and the
        adopt snapshot rides along with the finalize push (D0003 §3)."""
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "adoptprj.default.0001"
        assert svc.ensure_worktree("adoptprj", "default", group) == "ok"
        wt = src_root("AdoptProj", "adoptprj_default_0001")
        # worktree branches still start from origin/main (0115 behavior, unchanged)
        assert (wt / "shared.txt").read_text(encoding="utf-8") == "remote version\n"
        (wt / "work.txt").write_text("group work\n", encoding="utf-8")
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "merged"
        # origin main now holds the group work AND the adopt snapshot
        files = _git(["ls-tree", "--name-only", "main"], cwd=adopt_origin["bare"]).split()
        assert "work.txt" in files
        assert "crud.py" in files
        # local-content-wins snapshot survived the merge
        assert _git(["show", "main:shared.txt"], cwd=adopt_origin["bare"]) == "local version\n"
        # once the snapshot reached the remote, the view stops advertising it
        assert svc.provision_view("adoptprj")["adopt_snapshot"] is None

    def test_manual_provision_response_shape(self, adopt_origin):
        from modules.flow_gate.services import git_service as svc

        out = svc.provision_manual("adoptprj")
        assert out["ok"] is True
        assert out["result"]["status"] == "ok"
        assert out["result"]["mode"] == "none"
        assert out["result"]["provision"]["base_checkout_exists"] is True

    def test_manual_not_enabled_409(self, adopt_origin):
        from modules.flow_gate.services import git_service as svc

        with pytest.raises(svc.GitServiceError) as exc:
            svc.provision_manual("plainprj")
        assert exc.value.status == 409
        assert exc.value.code == "not_enabled"
        # status view for a non-integrated project is the fixed scenario-7 shape
        view = svc.provision_view("plainprj")
        assert view == {"configured": False, "enabled": False, "base_branch": None,
                        "base_path_state": "empty", "base_checkout_exists": False,
                        "adopt_snapshot": None, "last_attempt": None}

    def test_provision_view_unknown_project_404(self, adopt_origin):
        from modules.flow_gate.services import git_service as svc

        with pytest.raises(svc.GitServiceError) as exc:
            svc.provision_view("ghost")
        assert exc.value.status == 404

    def test_failed_attempt_recorded_then_reentry_completes(self, adopt_origin):
        """Interrupted adopt leaves debris + marker; a re-run resumes and
        finishes (L0005 §2.3 idempotent re-entry, P0004 scenario 5)."""
        from modules.flow_gate.db import projects
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        projects.create({"project_id": "retryprj", "project_name": "RetryProj"})
        base = src_root("RetryProj", "main")
        base.mkdir(parents=True, exist_ok=True)
        (base / "keep.txt").write_text("keep me\n", encoding="utf-8")
        # unreachable origin → fetch fails mid-adopt
        svc.save_config("retryprj", {
            "repo_url": (Path(adopt_origin["tmp"]) / "missing.git").as_uri(),
            "base_branch": "main",
            "enabled": True,
        })
        out = svc.provision_base("retryprj", "manual")
        assert out["status"] == "failed"
        assert out["mode"] == "adopt"
        # debris: .git + pending marker stay; the slot still reports occupied
        assert (base / ".git" / "flowgate_adopt_pending").exists()
        assert svc._judge_base_slot(base, "main") == "occupied"
        view = svc.provision_view("retryprj")
        assert view["base_checkout_exists"] is False
        assert view["last_attempt"]["result"] == "failed"
        # fix the config → the re-run resumes from the debris and completes
        svc.save_config("retryprj", {
            "repo_url": adopt_origin["bare"].as_uri(),
            "base_branch": "main",
            "enabled": True,
        })
        out = svc.provision_base("retryprj", "manual")
        assert out["status"] == "ok"
        assert out["mode"] == "adopt"
        assert (base / "keep.txt").read_text(encoding="utf-8") == "keep me\n"
        assert svc._judge_base_slot(base, "main") == "checkout"
        svc.delete_config("retryprj")
