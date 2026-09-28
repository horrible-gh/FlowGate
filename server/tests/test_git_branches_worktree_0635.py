from __future__ import annotations

import os
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
_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import git_routes
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services import git_service
from modules.flow_gate.services.git import branches as branch_service
from modules.flow_gate.services.git.credentials import GitServiceError
from modules.flow_gate.services import ai_invoke_service
from modules.flow_gate.db import group_ai_leases

_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"


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
    db_path = tmp_path_factory.mktemp("bm-wt-db") / "flowgate.db"
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
    monkeypatch.setattr(git_service, "_acquire_lock", lambda project_id, holder: True)
    monkeypatch.setattr(git_service.db_git, "release_lock", lambda project_id, holder: None)
    monkeypatch.setattr(git_service.db_git, "list_states_of_project", lambda project_id: list(states))
    monkeypatch.setattr(git_service.db_git, "list_open_sessions", lambda: [])
    from modules.flow_gate.db import groups as db_groups
    monkeypatch.setattr(db_groups, "list_open_groups_by_work_base", lambda project_id, ref: [])
    subprocess.run(["git", "branch", "slot-live"], cwd=root, check=True)
    return root


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)


def _client(*, is_admin=True):
    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "admin", "is_admin": is_admin}
    return TestClient(app, raise_server_exceptions=False)


def test_resolve_source_ordinary_and_internal_slot_guard(repo):
    _git(repo, "branch", "feature-x")
    src = branch_service.resolve_source("flowgate", repo, "main", branch_service.MergeEndpointIdentity(kind="branch", branch="feature-x"))
    assert src.branch == "feature-x"
    assert src.kind == "branch"
    assert src.root == repo

    # internal_slot under kind=branch must fail-closed
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_source("flowgate", repo, "main", branch_service.MergeEndpointIdentity(kind="branch", branch="slot-live"))
    assert exc.value.code == "branch_merge_source_internal_slot"


def test_resolve_source_worktree_validations(repo, tmp_path, monkeypatch):
    wt_dir = tmp_path / "wt_1"
    _git(repo, "worktree", "add", str(wt_dir), "-b", "wt-feature")
    wt_head = _git(wt_dir, "rev-parse", "HEAD").stdout.strip()

    groups_records = {
        "flowgate.default.101": {
            "group_id": "flowgate.default.101",
            "project_id": "flowgate",
            "deleted_at": None,
        }
    }
    git_states = {
        "flowgate.default.101": {
            "group_id": "flowgate.default.101",
            "project_id": "flowgate",
            "worktree_registered": True,
            "branch": "wt-feature",
        }
    }
    wt_map = {
        "wt-feature": wt_dir,
    }

    from modules.flow_gate.db import groups as db_groups
    monkeypatch.setattr(db_groups, "get_by_id", lambda gid: groups_records.get(gid))
    monkeypatch.setattr(git_service, "_project_name", lambda pid: "FlowGate")
    monkeypatch.setattr(git_service, "_project_of_group", lambda gid: "flowgate" if gid != "flowgate.default.other" else "other_proj")
    monkeypatch.setattr(git_service.db_git, "get_state", lambda gid: git_states.get(gid))
    monkeypatch.setattr(git_service, "src_root", lambda pname, b: wt_map.get(b))

    # 1. Missing group_id
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_source("flowgate", repo, "main", branch_service.MergeEndpointIdentity(kind="worktree", branch="wt-feature", group_id=None))
    assert exc.value.code == "branch_merge_worktree_group_required"

    # 2. Group not found / deleted in groups table
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_source("flowgate", repo, "main", branch_service.MergeEndpointIdentity(kind="worktree", branch="wt-feature", group_id="missing.group"))
    assert exc.value.code == "branch_merge_worktree_group_not_found"

    groups_records["flowgate.default.101"]["deleted_at"] = "2026-09-28T12:00:00Z"
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_source("flowgate", repo, "main", branch_service.MergeEndpointIdentity(kind="worktree", branch="wt-feature", group_id="flowgate.default.101"))
    assert exc.value.code == "branch_merge_worktree_group_not_found"
    groups_records["flowgate.default.101"]["deleted_at"] = None

    # 3. Project mismatch in groups record
    groups_records["flowgate.default.other"] = {"group_id": "flowgate.default.other", "project_id": "other_proj", "deleted_at": None}
    git_states["flowgate.default.other"] = {"group_id": "flowgate.default.other", "project_id": "other_proj"}
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_source("flowgate", repo, "main", branch_service.MergeEndpointIdentity(kind="worktree", branch="wt-feature", group_id="flowgate.default.other"))
    assert exc.value.code == "branch_merge_worktree_project_mismatch"

    # 4. Worktree not registered
    git_states["flowgate.default.101"]["worktree_registered"] = False
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_source("flowgate", repo, "main", branch_service.MergeEndpointIdentity(kind="worktree", branch="wt-feature", group_id="flowgate.default.101"))
    assert exc.value.code == "branch_merge_worktree_not_registered"
    git_states["flowgate.default.101"]["worktree_registered"] = True

    # 5. Ledger branch mismatch
    git_states["flowgate.default.101"]["branch"] = "other-branch"
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_source("flowgate", repo, "main", branch_service.MergeEndpointIdentity(kind="worktree", branch="wt-feature", group_id="flowgate.default.101"))
    assert exc.value.code == "branch_merge_worktree_branch_mismatch"
    git_states["flowgate.default.101"]["branch"] = "wt-feature"

    # 6. Worktree dir missing
    wt_map["wt-feature"] = tmp_path / "non_existent_dir"
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_source("flowgate", repo, "main", branch_service.MergeEndpointIdentity(kind="worktree", branch="wt-feature", group_id="flowgate.default.101"))
    assert exc.value.code == "branch_merge_worktree_not_found"
    wt_map["wt-feature"] = wt_dir

    # 7. Git metadata: foreign repository with different common-dir rejects
    foreign_repo = tmp_path / "foreign_repo"
    foreign_repo.mkdir()
    _git(foreign_repo, "init", "-b", "wt-feature")
    (foreign_repo / "test.txt").write_text("foreign\n", encoding="utf-8")
    _git(foreign_repo, "add", "test.txt")
    _git(foreign_repo, "commit", "-m", "init")
    wt_map["wt-feature"] = foreign_repo
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_source("flowgate", repo, "main", branch_service.MergeEndpointIdentity(kind="worktree", branch="wt-feature", group_id="flowgate.default.101"))
    assert exc.value.code == "branch_merge_worktree_identity_mismatch"
    wt_map["wt-feature"] = wt_dir

    # 8. Git operation in progress
    git_path = _git(wt_dir, "rev-parse", "--git-path", "MERGE_HEAD").stdout.strip()
    merge_head_file = Path(git_path)
    if not merge_head_file.is_absolute():
        merge_head_file = wt_dir / merge_head_file
    merge_head_file.write_text(wt_head + "\n", encoding="utf-8")
    try:
        with pytest.raises(GitServiceError) as exc:
            branch_service.resolve_source("flowgate", repo, "main", branch_service.MergeEndpointIdentity(kind="worktree", branch="wt-feature", group_id="flowgate.default.101"))
        assert exc.value.code == "branch_merge_worktree_git_operation_in_progress"
    finally:
        if merge_head_file.exists():
            merge_head_file.unlink()

    # 9. Success: Pinned HEAD sha even if worktree working tree is dirty
    (wt_dir / "uncommitted.txt").write_text("dirty uncommitted data\n", encoding="utf-8")
    resolved = branch_service.resolve_source("flowgate", repo, "main", branch_service.MergeEndpointIdentity(kind="worktree", branch="wt-feature", group_id="flowgate.default.101"))
    assert resolved.kind == "worktree"
    assert resolved.group_id == "flowgate.default.101"
    assert resolved.head_sha == wt_head
    assert resolved.root == wt_dir.resolve()


def test_resolve_target_guards(repo, tmp_path, monkeypatch):
    wt_dir = tmp_path / "wt_target"
    _git(repo, "worktree", "add", str(wt_dir), "-b", "wt-target")
    wt_head = _git(wt_dir, "rev-parse", "HEAD").stdout.strip()

    groups_records = {
        "flowgate.default.201": {
            "group_id": "flowgate.default.201",
            "project_id": "flowgate",
            "deleted_at": None,
        }
    }
    git_states = {
        "flowgate.default.201": {
            "group_id": "flowgate.default.201",
            "project_id": "flowgate",
            "worktree_registered": True,
            "branch": "wt-target",
        }
    }
    wt_map = {
        "wt-target": wt_dir,
    }

    from modules.flow_gate.db import groups as db_groups
    monkeypatch.setattr(db_groups, "get_by_id", lambda gid: groups_records.get(gid))
    monkeypatch.setattr(git_service, "_project_name", lambda pid: "FlowGate")
    monkeypatch.setattr(git_service, "_project_of_group", lambda gid: "flowgate")
    monkeypatch.setattr(git_service.db_git, "get_state", lambda gid: git_states.get(gid))
    monkeypatch.setattr(git_service, "src_root", lambda pname, b: wt_map.get(b))

    source_resolved = branch_service.ResolvedSource(kind="branch", branch="main", group_id=None, head_sha="1234", root=None)

    # Reject internal slot target under kind=branch
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_target("flowgate", repo, "main", branch_service.MergeEndpointIdentity(kind="branch", branch="slot-live"), source_resolved)
    assert exc.value.code == "branch_merge_target_internal_slot"

    # Foreign repository worktree target rejects
    foreign_target = tmp_path / "foreign_target"
    foreign_target.mkdir()
    _git(foreign_target, "init", "-b", "wt-target")
    wt_map["wt-target"] = foreign_target
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_target("flowgate", repo, "main", branch_service.MergeEndpointIdentity(kind="worktree", branch="wt-target", group_id="flowgate.default.201"), source_resolved)
    assert exc.value.code == "branch_merge_worktree_identity_mismatch"
    wt_map["wt-target"] = wt_dir

    # Deleted group target rejects
    groups_records["flowgate.default.201"]["deleted_at"] = "2026-09-28T12:00:00Z"
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_target("flowgate", repo, "main", branch_service.MergeEndpointIdentity(kind="worktree", branch="wt-target", group_id="flowgate.default.201"), source_resolved)
    assert exc.value.code == "branch_merge_worktree_group_not_found"
    groups_records["flowgate.default.201"]["deleted_at"] = None

    # Target worktree dirty rejects
    (wt_dir / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_target("flowgate", repo, "main", branch_service.MergeEndpointIdentity(kind="worktree", branch="wt-target", group_id="flowgate.default.201"), source_resolved)
    assert exc.value.code == "branch_merge_target_worktree_dirty"
    (wt_dir / "untracked.txt").unlink()

    # Active AI run / lease rejects
    monkeypatch.setattr(ai_invoke_service, "has_active_run", lambda gid: True)
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_target("flowgate", repo, "main", branch_service.MergeEndpointIdentity(kind="worktree", branch="wt-target", group_id="flowgate.default.201"), source_resolved)
    assert exc.value.code == "branch_merge_target_group_ai_active"
    monkeypatch.setattr(ai_invoke_service, "has_active_run", lambda gid: False)
    monkeypatch.setattr(group_ai_leases, "get_active", lambda gid: {"lease_id": "test-lease"})
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_target("flowgate", repo, "main", branch_service.MergeEndpointIdentity(kind="worktree", branch="wt-target", group_id="flowgate.default.201"), source_resolved)
    assert exc.value.code == "branch_merge_target_group_ai_active"
    monkeypatch.setattr(group_ai_leases, "get_active", lambda gid: None)

    # Clean target resolves successfully
    resolved = branch_service.resolve_target("flowgate", repo, "main", branch_service.MergeEndpointIdentity(kind="worktree", branch="wt-target", group_id="flowgate.default.201"), source_resolved)
    assert resolved.kind == "worktree"
    assert resolved.group_id == "flowgate.default.201"
    assert resolved.root == wt_dir.resolve()


def test_merge_branches_with_worktree_source(repo, tmp_path, monkeypatch):
    wt_dir = tmp_path / "wt_source_merge"
    _git(repo, "worktree", "add", str(wt_dir), "-b", "wt-source-merge")
    (wt_dir / "new_file.txt").write_text("committed in worktree\n", encoding="utf-8")
    _git(wt_dir, "add", "new_file.txt")
    _git(wt_dir, "commit", "-m", "worktree commit")
    wt_head = _git(wt_dir, "rev-parse", "HEAD").stdout.strip()
    (wt_dir / "dirty_ignored.txt").write_text("do not include\n", encoding="utf-8")

    _git(repo, "branch", "develop")

    groups_records = {
        "flowgate.default.301": {
            "group_id": "flowgate.default.301",
            "project_id": "flowgate",
            "deleted_at": None,
        }
    }
    git_states = {
        "flowgate.default.301": {
            "group_id": "flowgate.default.301",
            "project_id": "flowgate",
            "worktree_registered": True,
            "branch": "wt-source-merge",
        }
    }
    wt_map = {
        "wt-source-merge": wt_dir,
    }

    from modules.flow_gate.db import groups as db_groups
    monkeypatch.setattr(db_groups, "get_by_id", lambda gid: groups_records.get(gid))
    monkeypatch.setattr(git_service, "_project_name", lambda pid: "FlowGate")
    monkeypatch.setattr(git_service, "_project_of_group", lambda gid: "flowgate")
    monkeypatch.setattr(git_service.db_git, "get_state", lambda gid: git_states.get(gid))
    monkeypatch.setattr(git_service, "src_root", lambda pname, b: wt_map.get(b))

    # Reject same worktree/same branch
    with pytest.raises(GitServiceError) as exc:
        git_service.merge_branches(
            "flowgate",
            source_branch="wt-source-merge",
            target_branch="wt-source-merge",
            source_kind="worktree",
            source_group_id="flowgate.default.301",
            target_kind="worktree",
            target_group_id="flowgate.default.301",
        )
    assert exc.value.code in ("branch_merge_same_worktree", "branch_merge_same_branch")

    # Worktree source merged into ordinary target
    result = git_service.merge_branches(
        "flowgate",
        source_branch="wt-source-merge",
        target_branch="develop",
        source_kind="worktree",
        source_group_id="flowgate.default.301",
        target_kind="branch",
        push=False,
    )
    assert result["ok"] is True
    assert result["source_head"] == wt_head
    assert _git(repo, "show", "develop:new_file.txt").stdout == "committed in worktree\n"
    assert _git(repo, "show", "develop:dirty_ignored.txt").returncode != 0

    # Clean dirty file from wt_dir so target validation passes clean check
    (wt_dir / "dirty_ignored.txt").unlink()

    # Worktree target merge raises worktree_target_merge_pending (501)
    with pytest.raises(GitServiceError) as exc:
        git_service.merge_branches(
            "flowgate",
            source_branch="develop",
            target_branch="wt-source-merge",
            source_kind="branch",
            target_kind="worktree",
            target_group_id="flowgate.default.301",
            push=False,
        )
    assert exc.value.code == "worktree_target_merge_pending"
    assert exc.value.status == 501


def test_branch_merge_route_supports_typed_identity_payload(monkeypatch):
    calls = []

    def fake_merge(project_id, source_branch, target_branch, **kwargs):
        calls.append({
            "project_id": project_id,
            "source_branch": source_branch,
            "target_branch": target_branch,
            "kwargs": kwargs,
        })
        return {"ok": True, "pushed": kwargs.get("push", True)}

    monkeypatch.setattr(git_routes.git_service, "merge_branches", fake_merge)
    client = _client()

    resp = client.post(
        "/api/v1/projects/flowgate/git/branches/merge",
        json={
            "source_branch": "wt-feat",
            "target_branch": "main",
            "source_kind": "worktree",
            "source_group_id": "flowgate.default.123",
            "target_kind": "branch",
            "push": False,
        },
    )
    assert resp.status_code == 200
    assert calls[-1]["source_branch"] == "wt-feat"
    assert calls[-1]["target_branch"] == "main"
    assert calls[-1]["kwargs"]["source_kind"] == "worktree"
    assert calls[-1]["kwargs"]["source_group_id"] == "flowgate.default.123"
    assert calls[-1]["kwargs"]["target_kind"] == "branch"
    assert calls[-1]["kwargs"]["push"] is False

    resp2 = client.post(
        "/api/v1/projects/flowgate/git/branches/merge",
        json={
            "source": {"branch": "wt-feat", "kind": "worktree", "group_id": "flowgate.default.123"},
            "target": {"branch": "main", "kind": "branch"},
            "push": True,
        },
    )
    assert resp2.status_code == 200
    assert calls[-1]["source_branch"] == "wt-feat"
    assert calls[-1]["target_branch"] == "main"
    assert calls[-1]["kwargs"]["source_kind"] == "worktree"
    assert calls[-1]["kwargs"]["push"] is True
