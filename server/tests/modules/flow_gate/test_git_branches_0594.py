from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "*")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite3")
_SERVER_DIR = Path(__file__).resolve().parents[3]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import git_routes
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services import git_service
from modules.flow_gate.services.git import branches as branch_service
from modules.flow_gate.services.git.credentials import GitServiceError


_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"


from group_lock_stub import group_store  # noqa: F401
import pytest as _pytest_locks

# Group/base/remote work takes domain locks from the real lock manager (0669): these tests
# run on the real SQLite lock/job store instead of stubbing the removed project mutex.
pytestmark = _pytest_locks.mark.usefixtures("group_store")


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._conn.execute(sql, params or [])
        self._conn.commit()

    @property
    def cursor(self):
        return self._cur

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
def attempt_db(tmp_path_factory):
    """every branch merge now writes a persistent attempt row
    (git_merge_session, owner branch_merge) before `git merge` runs, so the merge
    cases need a real store with the real sqlite migrations (123 included)."""
    db_path = tmp_path_factory.mktemp("bm-db") / "flowgate.db"
    db = _MockDB(str(db_path))
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            db._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    db._conn.commit()
    from modules.flow_gate.db import connection as conn_mod

    original_store = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = db
            self._sq = None

    conn_mod.STORE = _PatchedStore()
    yield db
    conn_mod.STORE = original_store
    db.close()


@pytest.fixture
def repo(tmp_path, monkeypatch, attempt_db):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=root, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=root, check=True, capture_output=True)

    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(git_service, "get_storage_root", lambda: storage)
    states = [{"project_id": "flowgate", "group_id": "flowgate.default.1", "branch": "slot-live"}]
    monkeypatch.setattr(branch_service, "_branch_context", lambda project_id: ({"enabled": 1}, root, "main"))
    monkeypatch.setattr(git_service, "_base_root_of", lambda project_id: root)
    monkeypatch.setattr(git_service, "_load_secret_for", lambda cfg: "")
    monkeypatch.setattr(git_service.db_git, "list_states_of_project", lambda project_id: list(states))
    monkeypatch.setattr(git_service.db_git, "list_open_sessions", lambda: [])
    # the delete guard also asks which live groups pin a branch as their
    # Base Branch; none do in these catalog/delete scenarios.
    from modules.flow_gate.db import groups as db_groups
    monkeypatch.setattr(db_groups, "list_open_groups_by_work_base", lambda project_id, ref: [])
    subprocess.run(["git", "branch", "slot-live"], cwd=root, check=True)
    return root


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)


def test_catalog_classifies_local_remote_only_and_registered_slot(repo):
    _git(repo, "branch", "local-only")
    main_sha = _git(repo, "rev-parse", "main").stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", main_sha)
    _git(repo, "update-ref", "refs/remotes/origin/remote-only", main_sha)

    result = git_service.list_branches("flowgate")
    rows = {row["name"]: row for row in result["branches"]}

    assert rows["main"]["kind"] == "base"
    assert rows["main"]["has_remote_counterpart"] is True
    assert rows["local-only"]["kind"] == "local"
    assert rows["slot-live"]["kind"] == "internal_slot"
    assert rows["slot-live"]["connected_group_id"] == "flowgate.default.1"
    assert rows["slot-live"]["can_be_create_source"] is False
    assert rows["remote-only"]["kind"] == "remote_only"
    assert rows["remote-only"]["can_delete"] is False
    assert rows["remote-only"]["can_be_create_source"] is False
    assert rows["remote-only"]["ahead_of_base"] is None


def test_name_validation_is_git_check_ref_format_based(repo):
    with pytest.raises(GitServiceError) as caught:
        git_service.validate_new_branch_name("flowgate", repo, "main", "bad..name")
    assert caught.value.status == 422
    assert caught.value.code == "branch_ref_format_invalid"
    assert "git_stderr" in caught.value.details

    with pytest.raises(GitServiceError) as caught:
        git_service.validate_new_branch_name("flowgate", repo, "main", "main")
    assert caught.value.code == "branch_reserved_name"


def test_create_rejects_remote_only_and_actual_slot_then_creates_local_only(repo, monkeypatch):
    main_sha = _git(repo, "rev-parse", "main").stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/remote-only", main_sha)

    with pytest.raises(GitServiceError) as caught:
        git_service.create_branch("flowgate", "new-a", "remote-only")
    assert caught.value.code == "branch_source_remote_only"

    with pytest.raises(GitServiceError) as caught:
        git_service.create_branch("flowgate", "new-b", "slot-live")
    assert caught.value.code == "branch_source_internal_slot"

    result = git_service.create_branch("flowgate", "feature/ok", "main")
    assert result["published"] is False
    assert _git(repo, "show-ref", "--verify", "refs/heads/feature/ok").returncode == 0
    assert _git(repo, "show-ref", "--verify", "refs/remotes/origin/feature/ok").returncode != 0


def test_create_rechecks_after_lock_and_fails_closed(repo, monkeypatch):
    calls = 0

    def changing_states(project_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            return []
        return [{"group_id": "flowgate.default.2", "branch": "source"}]

    _git(repo, "branch", "source")
    monkeypatch.setattr(git_service.db_git, "list_states_of_project", changing_states)
    with pytest.raises(GitServiceError) as caught:
        git_service.create_branch("flowgate", "new-branch", "source")
    assert caught.value.code == "branch_source_internal_slot"
    assert _git(repo, "show-ref", "--verify", "refs/heads/new-branch").returncode != 0


def test_delete_guards_and_local_only_remote_preservation(repo, monkeypatch):
    assert git_service.check_branch_delete("flowgate", "missing") == "branch_not_found"
    assert git_service.check_branch_delete("flowgate", "main") == "branch_is_base"
    assert git_service.check_branch_delete("flowgate", "slot-live") == "branch_is_internal_slot"

    _git(repo, "branch", "in-use")
    monkeypatch.setattr(
        git_service.db_git,
        "list_open_sessions",
        lambda: [{"merge_id": 7, "group_id": "flowgate.default.9", "context": '{"target_branch":"in-use"}'}],
    )
    monkeypatch.setattr(git_service.db_git, "get_state", lambda group_id: None)
    assert git_service.check_branch_delete("flowgate", "in-use") == "branch_in_use"
    monkeypatch.setattr(git_service.db_git, "list_open_sessions", lambda: [])

    _git(repo, "branch", "merged")
    main_sha = _git(repo, "rev-parse", "main").stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/merged", main_sha)
    result = git_service.delete_branch("flowgate", "merged")
    assert result["remote_counterpart_remains"] is True
    assert _git(repo, "show-ref", "--verify", "refs/heads/merged").returncode != 0
    assert _git(repo, "show-ref", "--verify", "refs/remotes/origin/merged").returncode == 0


def test_delete_blocks_unregistered_linked_worktree_and_prunable_until_pruned(repo):
    _git(repo, "branch", "linked-worktree")
    wt = repo.parent / "linked-wt"
    added = _git(repo, "worktree", "add", str(wt), "linked-worktree")
    assert added.returncode == 0

    rows = {row["name"]: row for row in git_service.list_branches("flowgate")["branches"]}
    assert rows["linked-worktree"]["kind"] == "external_worktree"
    assert rows["linked-worktree"]["can_be_create_source"] is False
    assert rows["linked-worktree"]["can_delete"] is False
    assert rows["linked-worktree"]["delete_blocked_reason"] == "branch_checked_out_in_worktree"

    with pytest.raises(GitServiceError) as caught:
        git_service.delete_branch("flowgate", "linked-worktree")
    assert caught.value.status == 409
    assert caught.value.code == "branch_checked_out_in_worktree"

    # Removing only the directory leaves a prunable Git registration. Git still
    # rejects branch -d until worktree prune, so the FlowGate guard must do the same.
    shutil.rmtree(wt)
    porcelain = _git(repo, "worktree", "list", "--porcelain").stdout
    assert "branch refs/heads/linked-worktree" in porcelain
    assert "prunable" in porcelain
    assert git_service.check_branch_delete("flowgate", "linked-worktree") == (
        "branch_checked_out_in_worktree"
    )

    assert _git(repo, "worktree", "prune").returncode == 0
    result = git_service.delete_branch("flowgate", "linked-worktree")
    assert result["deleted"] is True


def test_detached_worktree_does_not_pin_an_unrelated_branch(repo):
    _git(repo, "branch", "free-after-detached")
    wt = repo.parent / "detached-wt"
    added = _git(repo, "worktree", "add", "--detach", str(wt), "main")
    assert added.returncode == 0
    assert git_service.check_branch_delete("flowgate", "free-after-detached") is None














# merge/push separation. The existing
# test_branch_merge_clean_pushes_target_without_switching_base above already
# covers T1 (push omitted defaults to the old always-push behavior).


















def _client(*, is_admin=True):
    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "admin", "is_admin": is_admin}
    return TestClient(app, raise_server_exceptions=False)
