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
from modules.flow_gate.services import git_service, remote_tool_service
from modules.flow_gate.services.git import (
    branches as branch_service,
    branch_merge,
    cleanup as cleanup_service,
    commit as commit_service,
    finalize as finalize_service,
    merge_target,
    worktree as worktree_service,
)
from modules.flow_gate.services.git.credentials import GitServiceError
from modules.flow_gate.services import ai_invoke_service
from modules.flow_gate.services.ai_invoke import admission
from modules.flow_gate.db import group_ai_leases, groups as db_groups

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
    db._conn.execute(
        "INSERT OR IGNORE INTO projects (project_id, project_name, created_at, updated_at) "
        "VALUES ('flowgate', 'FlowGate', '2026-01-01', '2026-01-01')"
    )
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
    monkeypatch.setattr(git_service.db_git, "get_config", lambda pid: {"enabled": 1, "base_branch": "main", "repo_url": ""})
    monkeypatch.setattr(git_service, "_base_root_of", lambda project_id: root)
    monkeypatch.setattr(git_service, "_load_secret_for", lambda cfg: "")
    monkeypatch.setattr(git_service, "_acquire_lock", lambda project_id, holder, **kw: True)
    monkeypatch.setattr(git_service.db_git, "release_lock", lambda project_id, holder: None)
    monkeypatch.setattr(git_service.db_git, "list_states_of_project", lambda project_id: list(states))
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

    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_source("flowgate", repo, "main", branch_service.MergeEndpointIdentity(kind="branch", branch="slot-live"))
    assert exc.value.code == "branch_merge_source_internal_slot"


def test_resolve_source_worktree_validations(repo, tmp_path, monkeypatch):
    wt_dir = tmp_path / "wt1"
    _git(repo, "worktree", "add", str(wt_dir), "-b", "wt-branch-1")
    (wt_dir / "foo.txt").write_text("hello\n", encoding="utf-8")
    _git(wt_dir, "add", "foo.txt")
    _git(wt_dir, "commit", "-m", "worktree commit")
    expected_sha = _git(wt_dir, "rev-parse", "HEAD").stdout.strip()

    groups_records = {}
    git_states = {}
    wt_map = {}

    monkeypatch.setattr(db_groups, "get_by_id", lambda gid: groups_records.get(gid))
    monkeypatch.setattr(git_service, "_project_name", lambda pid: "FlowGate")
    monkeypatch.setattr(git_service, "_project_of_group", lambda gid: "flowgate")
    monkeypatch.setattr(git_service.db_git, "get_state", lambda gid: git_states.get(gid))
    monkeypatch.setattr(git_service, "src_root", lambda pname, b: wt_map.get(b))

    ep = branch_service.MergeEndpointIdentity(kind="worktree", branch="wt-branch-1", group_id="flowgate.default.101")
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_source("flowgate", repo, "main", ep)
    assert exc.value.code == "branch_merge_worktree_group_not_found"

    groups_records["flowgate.default.101"] = {
        "group_id": "flowgate.default.101",
        "project_id": "flowgate",
        "deleted_at": "2026-09-28T00:00:00Z",
    }
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_source("flowgate", repo, "main", ep)
    assert exc.value.code == "branch_merge_worktree_group_not_found"

    groups_records["flowgate.default.101"]["deleted_at"] = None
    groups_records["flowgate.default.101"]["project_id"] = "other_project"
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_source("flowgate", repo, "main", ep)
    assert exc.value.code == "branch_merge_worktree_project_mismatch"

    groups_records["flowgate.default.101"]["project_id"] = "flowgate"
    git_states["flowgate.default.101"] = {
        "group_id": "flowgate.default.101",
        "project_id": "flowgate",
        "worktree_registered": False,
        "branch": "wt-branch-1",
    }
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_source("flowgate", repo, "main", ep)
    assert exc.value.code == "branch_merge_worktree_not_registered"

    git_states["flowgate.default.101"]["worktree_registered"] = True
    git_states["flowgate.default.101"]["branch"] = "different-branch"
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_source("flowgate", repo, "main", ep)
    assert exc.value.code == "branch_merge_worktree_branch_mismatch"

    git_states["flowgate.default.101"]["branch"] = "wt-branch-1"
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_source("flowgate", repo, "main", ep)
    assert exc.value.code == "branch_merge_worktree_not_found"

    wt_map["wt-branch-1"] = tmp_path / "non_existent_dir"
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_source("flowgate", repo, "main", ep)
    assert exc.value.code == "branch_merge_worktree_not_found"

    foreign_dir = tmp_path / "foreign"
    foreign_dir.mkdir()
    subprocess.run(["git", "init"], cwd=foreign_dir, check=True, capture_output=True)
    wt_map["wt-branch-1"] = foreign_dir
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_source("flowgate", repo, "main", ep)
    assert exc.value.code == "branch_merge_worktree_identity_mismatch"

    wt_map["wt-branch-1"] = wt_dir
    src = branch_service.resolve_source("flowgate", repo, "main", ep)
    assert src.kind == "worktree"
    assert src.branch == "wt-branch-1"
    assert src.group_id == "flowgate.default.101"
    assert src.head_sha == expected_sha
    assert src.root == wt_dir.resolve()


def test_resolve_target_guards(repo, tmp_path, monkeypatch):
    wt_dir = tmp_path / "wt_target"
    _git(repo, "worktree", "add", str(wt_dir), "-b", "wt-target-1")

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
            "branch": "wt-target-1",
        }
    }
    wt_map = {
        "wt-target-1": wt_dir,
    }

    monkeypatch.setattr(db_groups, "get_by_id", lambda gid: groups_records.get(gid))
    monkeypatch.setattr(git_service, "_project_name", lambda pid: "FlowGate")
    monkeypatch.setattr(git_service, "_project_of_group", lambda gid: "flowgate")
    monkeypatch.setattr(git_service.db_git, "get_state", lambda gid: git_states.get(gid))
    monkeypatch.setattr(git_service, "src_root", lambda pname, b: wt_map.get(b))

    _git(repo, "branch", "src-feature")
    src = branch_service.resolve_source(
        "flowgate", repo, "main",
        branch_service.MergeEndpointIdentity(kind="branch", branch="src-feature")
    )
    target_ep = branch_service.MergeEndpointIdentity(
        kind="worktree", branch="wt-target-1", group_id="flowgate.default.201"
    )

    # Active claim on target group blocks resolve_target
    monkeypatch.setattr(
        branch_merge, "get_branch_merge_group_claim",
        lambda gid: {"merge_id": 999, "group_id": gid} if gid == "flowgate.default.201" else None
    )
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_target("flowgate", repo, "main", target_ep, src)
    assert exc.value.code == "branch_merge_target_group_busy"
    monkeypatch.setattr(branch_merge, "get_branch_merge_group_claim", lambda gid: None)

    # Open finalize session on target group blocks resolve_target
    monkeypatch.setattr(
        git_service.db_git, "get_open_session_by_group",
        lambda gid: {"merge_id": 888, "group_id": gid} if gid == "flowgate.default.201" else None
    )
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_target("flowgate", repo, "main", target_ep, src)
    assert exc.value.code == "branch_merge_target_group_busy"
    monkeypatch.setattr(git_service.db_git, "get_open_session_by_group", lambda gid: None)

    # Target dirty guard
    (wt_dir / "dirty_file.txt").write_text("uncommitted dirty\n", encoding="utf-8")
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_target("flowgate", repo, "main", target_ep, src)
    assert exc.value.code == "branch_merge_target_worktree_dirty"
    (wt_dir / "dirty_file.txt").unlink()

    # Target AI run active guard
    monkeypatch.setattr(ai_invoke_service, "has_active_run", lambda gid: True)
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_target("flowgate", repo, "main", target_ep, src)
    assert exc.value.code == "branch_merge_target_group_ai_active"
    monkeypatch.setattr(ai_invoke_service, "has_active_run", lambda gid: False)

    # Target AI lease active guard
    monkeypatch.setattr(group_ai_leases, "get_active", lambda gid: {"run_id": "r1"})
    with pytest.raises(GitServiceError) as exc:
        branch_service.resolve_target("flowgate", repo, "main", target_ep, src)
    assert exc.value.code == "branch_merge_target_group_ai_active"
    monkeypatch.setattr(group_ai_leases, "get_active", lambda gid: None)

    resolved_target = branch_service.resolve_target("flowgate", repo, "main", target_ep, src)
    assert resolved_target.kind == "worktree"
    assert resolved_target.branch == "wt-target-1"
    assert resolved_target.group_id == "flowgate.default.201"
    assert resolved_target.root == wt_dir.resolve()
    assert resolved_target.managed_workspace is False


def test_merge_worktree_final_preflight_runs_after_own_claim(repo, tmp_path, monkeypatch):
    """A lease that wins the old preflight->claim gap must stop the merge before Git mutation."""
    wt_dir = tmp_path / "wt_claim_handoff"
    _git(repo, "worktree", "add", str(wt_dir), "-b", "wt-claim-handoff")

    _git(repo, "checkout", "-b", "claim-race-source")
    (repo / "race.txt").write_text("source\n", encoding="utf-8")
    _git(repo, "add", "race.txt")
    _git(repo, "commit", "-m", "claim race source")
    _git(repo, "checkout", "main")

    gid = "flowgate.default.299"
    groups_records = {
        gid: {"group_id": gid, "project_id": "flowgate", "deleted_at": None}
    }
    git_states = {
        gid: {
            "group_id": gid,
            "project_id": "flowgate",
            "worktree_registered": True,
            "branch": "wt-claim-handoff",
            "status": "active",
        }
    }
    wt_map = {"wt-claim-handoff": wt_dir}

    monkeypatch.setattr(db_groups, "get_by_id", lambda g: groups_records.get(g))
    monkeypatch.setattr(git_service, "_project_name", lambda pid: "FlowGate")
    monkeypatch.setattr(git_service, "_project_of_group", lambda g: "flowgate")
    monkeypatch.setattr(git_service.db_git, "get_state", lambda g: git_states.get(g))
    monkeypatch.setattr(git_service, "src_root", lambda pname, b: wt_map.get(b))
    monkeypatch.setattr(ai_invoke_service, "has_active_run", lambda g: False)

    lease_checks = {"count": 0}

    def lease_after_claim(group_id):
        lease_checks["count"] += 1
        # resolve_target runs before the project lock and once again under it.
        # The third mutable check is the new post-open-attempt check.
        if lease_checks["count"] >= 3:
            return {"run_id": "lease-won-gap"}
        return None

    monkeypatch.setattr(group_ai_leases, "get_active", lease_after_claim)

    with pytest.raises(GitServiceError) as exc:
        git_service.merge_branches(
            "flowgate",
            source_branch="claim-race-source",
            target_branch="wt-claim-handoff",
            source_kind="branch",
            target_kind="worktree",
            target_group_id=gid,
            push=False,
        )

    assert exc.value.code == "branch_merge_target_group_ai_active"
    assert lease_checks["count"] >= 3
    assert not (wt_dir / "race.txt").exists()
    assert not git_service._merge_in_progress(wt_dir)
    assert branch_merge.get_branch_merge_group_claim(gid) is None


def test_merge_branches_all_4_combinations(repo, tmp_path, monkeypatch):
    """Test all 4 branch/worktree combinations and push false/true."""
    # Setup worktree 1 (Group 301, branch wt-1)
    wt1_dir = tmp_path / "wt1"
    _git(repo, "worktree", "add", str(wt1_dir), "-b", "wt-1")
    (wt1_dir / "file_wt1.txt").write_text("wt1 content\n", encoding="utf-8")
    _git(wt1_dir, "add", "file_wt1.txt")
    _git(wt1_dir, "commit", "-m", "wt1 commit")

    # Setup worktree 2 (Group 302, branch wt-2)
    wt2_dir = tmp_path / "wt2"
    _git(repo, "worktree", "add", str(wt2_dir), "-b", "wt-2")
    (wt2_dir / "file_wt2.txt").write_text("wt2 content\n", encoding="utf-8")
    _git(wt2_dir, "add", "file_wt2.txt")
    _git(wt2_dir, "commit", "-m", "wt2 commit")

    # Setup ordinary branches: checkout feat-b and commit
    _git(repo, "checkout", "-b", "feat-b")
    (repo / "file_b.txt").write_text("feat-b content\n", encoding="utf-8")
    _git(repo, "add", "file_b.txt")
    _git(repo, "commit", "-m", "feat-b commit")
    _git(repo, "checkout", "main")
    _git(repo, "branch", "feat-a")

    groups_records = {
        "flowgate.default.301": {"group_id": "flowgate.default.301", "project_id": "flowgate", "deleted_at": None},
        "flowgate.default.302": {"group_id": "flowgate.default.302", "project_id": "flowgate", "deleted_at": None},
    }
    git_states = {
        "flowgate.default.301": {"group_id": "flowgate.default.301", "project_id": "flowgate", "worktree_registered": True, "branch": "wt-1"},
        "flowgate.default.302": {"group_id": "flowgate.default.302", "project_id": "flowgate", "worktree_registered": True, "branch": "wt-2"},
    }
    wt_map = {"wt-1": wt1_dir, "wt-2": wt2_dir}

    monkeypatch.setattr(db_groups, "get_by_id", lambda gid: groups_records.get(gid))
    monkeypatch.setattr(git_service, "_project_name", lambda pid: "FlowGate")
    monkeypatch.setattr(git_service, "_project_of_group", lambda gid: "flowgate")
    monkeypatch.setattr(git_service.db_git, "get_state", lambda gid: git_states.get(gid))
    monkeypatch.setattr(git_service, "src_root", lambda pname, b: wt_map.get(b))

    # Combination 1: branch -> branch (regression)
    res_bb = git_service.merge_branches(
        "flowgate",
        source_branch="feat-b", target_branch="feat-a",
        source_kind="branch", target_kind="branch",
        push=False,
    )
    assert res_bb["ok"] is True
    assert res_bb["workspace_cleaned"] is True
    assert _git(repo, "show", "feat-a:file_b.txt").stdout == "feat-b content\n"

    # Combination 2: worktree -> branch
    _git(repo, "branch", "feat-c")
    res_wb = git_service.merge_branches(
        "flowgate",
        source_branch="wt-1", target_branch="feat-c",
        source_kind="worktree", source_group_id="flowgate.default.301",
        target_kind="branch",
        push=False,
    )
    assert res_wb["ok"] is True
    assert res_wb["workspace_cleaned"] is True
    assert _git(repo, "show", "feat-c:file_wt1.txt").stdout == "wt1 content\n"

    # Combination 3: branch -> worktree (push=False)
    res_bw_local = git_service.merge_branches(
        "flowgate",
        source_branch="feat-b", target_branch="wt-1",
        source_kind="branch",
        target_kind="worktree", target_group_id="flowgate.default.301",
        push=False,
    )
    assert res_bw_local["ok"] is True
    assert res_bw_local["workspace_cleaned"] is False
    assert (wt1_dir / "file_b.txt").exists()
    assert (wt1_dir / "file_b.txt").read_text(encoding="utf-8") == "feat-b content\n"
    assert wt1_dir.exists()

    # Combination 3b: branch -> worktree (push=True)
    _git(repo, "checkout", "-b", "feat-push")
    (repo / "file_pushed.txt").write_text("pushed content\n", encoding="utf-8")
    _git(repo, "add", "file_pushed.txt")
    _git(repo, "commit", "-m", "push commit")
    _git(repo, "checkout", "main")

    res_bw_push = git_service.merge_branches(
        "flowgate",
        source_branch="feat-push", target_branch="wt-1",
        source_kind="branch",
        target_kind="worktree", target_group_id="flowgate.default.301",
        push=True,
    )
    assert res_bw_push["ok"] is True
    assert res_bw_push["pushed"] is True
    assert res_bw_push["workspace_cleaned"] is False
    assert (wt1_dir / "file_pushed.txt").exists()
    assert wt1_dir.exists()

    # Combination 4: worktree A -> worktree B
    res_ww = git_service.merge_branches(
        "flowgate",
        source_branch="wt-1", target_branch="wt-2",
        source_kind="worktree", source_group_id="flowgate.default.301",
        target_kind="worktree", target_group_id="flowgate.default.302",
        push=False,
    )
    assert res_ww["ok"] is True
    assert res_ww["workspace_cleaned"] is False
    assert (wt2_dir / "file_wt1.txt").exists()
    assert wt1_dir.exists()
    assert wt2_dir.exists()


def test_worktree_target_conflict_and_persistent_claim_guards(repo, tmp_path, monkeypatch):
    """Test conflict merge on worktree target, persistent claim, and all 6 claim guards."""
    wt_target_dir = tmp_path / "wt_conflict_target"
    _git(repo, "worktree", "add", str(wt_target_dir), "-b", "wt-target-conflict")
    (wt_target_dir / "conflict.txt").write_text("target version\n", encoding="utf-8")
    _git(wt_target_dir, "add", "conflict.txt")
    _git(wt_target_dir, "commit", "-m", "target commit")

    _git(repo, "checkout", "-b", "src-conflict-branch")
    (repo / "conflict.txt").write_text("source version\n", encoding="utf-8")
    _git(repo, "add", "conflict.txt")
    _git(repo, "commit", "-m", "source commit")
    _git(repo, "checkout", "main")

    gid = "flowgate.default.401"
    groups_records = {gid: {"group_id": gid, "project_id": "flowgate", "deleted_at": None}}
    git_states = {gid: {"group_id": gid, "project_id": "flowgate", "worktree_registered": True, "branch": "wt-target-conflict", "status": "active"}}
    wt_map = {"wt-target-conflict": wt_target_dir}

    monkeypatch.setattr(db_groups, "get_by_id", lambda g: groups_records.get(g))
    monkeypatch.setattr(git_service, "_project_name", lambda pid: "FlowGate")
    monkeypatch.setattr(git_service, "_project_of_group", lambda g: "flowgate")
    monkeypatch.setattr(git_service.db_git, "get_state", lambda g: git_states.get(g))
    monkeypatch.setattr(git_service.db_git, "list_states_of_project", lambda pid: [git_states[gid]])
    monkeypatch.setattr(git_service, "src_root", lambda pname, b: wt_map.get(b))
    monkeypatch.setattr(git_service, "_require_enabled_config", lambda pid: {"project_id": "flowgate", "enabled": True})

    # Trigger conflict merge into worktree target
    res = git_service.merge_branches(
        "flowgate",
        source_branch="src-conflict-branch", target_branch="wt-target-conflict",
        source_kind="branch",
        target_kind="worktree", target_group_id=gid,
        push=False,
    )
    assert res["ok"] is True
    assert res["status"] == "conflict"
    merge_id = res["merge_id"]
    assert res["workspace_cleaned"] is False
    assert "conflict.txt" in res["conflict_files"]

    # Target worktree has conflict markers in place
    assert (wt_target_dir / "conflict.txt").exists()
    content = (wt_target_dir / "conflict.txt").read_text(encoding="utf-8")
    assert "<<<<<<<" in content and ">>>>>>>" in content
    assert git_service._merge_in_progress(wt_target_dir)

    # Claim is persistently active on target group
    claim = git_service.get_branch_merge_group_claim(gid)
    assert claim is not None
    assert claim["merge_id"] == merge_id
    assert claim["group_id"] == gid
    assert claim["target_branch"] == "wt-target-conflict"
    assert claim["source_branch"] == "src-conflict-branch"

    # --- Verify Guard 1: AI admission ---
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc1:
        admission.start_run(
            project_id="flowgate", group_id=gid, module="default",
            doc_ref=f"{gid}.0001-R", action_scope="chat_interactive",
            mode="interactive", continuation_target_seq=None,
            continuation_review_mode=False, continuation_instruction_mode=None,
            continuation_locale="ko", issued_to="ai-worker",
            api_base_url="http://127.0.0.1", mention_builder=lambda a, b: None,
        )
    assert exc1.value.status_code == 409
    assert exc1.value.detail.get("code") == "branch_merge_claim_active"

    # --- Verify Guard 2: update-from-base ---
    with pytest.raises(GitServiceError) as exc2:
        finalize_service.update_from_base(gid)
    assert exc2.value.code == "branch_merge_claim_active"

    # --- Verify Guard 3: group finalize ---
    with pytest.raises(GitServiceError) as exc3:
        finalize_service.finalize(gid, action="wait")
    assert exc3.value.code == "branch_merge_claim_active"

    # --- Verify Guard 4: worktree Git mutation (create_tr_commit, _cancel_prelock_gate) ---
    tr_res = commit_service.create_tr_commit(gid, "TR Commit Title")
    assert tr_res["committed"] is False
    assert tr_res["skipped_reason"] == "branch_merge_claim_active"

    gate_res = commit_service._cancel_prelock_gate(gid)
    assert gate_res["blocked_reason"] == "git_busy"
    assert gate_res["block_sub"] == "branch_merge_claim_active"

    # --- Verify Guard 5: teardown / cleanup ---
    disp_res = cleanup_service.cleanup_disposed_group("flowgate", gid)
    assert disp_res["ok"] is False
    assert disp_res["reason"] == "branch_merge_claim_active"

    git_states[gid]["status"] = "merged"
    term_res = cleanup_service.cleanup_terminal_slots("flowgate")
    assert any(p.get("group_id") == gid and p.get("reason") == "branch_merge_claim_active" for p in term_res.get("terminal_cleanup", {}).get("pending", []))

    wt_clean_res = worktree_service._cleanup_group_slot("flowgate", gid)
    assert wt_clean_res is False

    # --- Verify Guard 6: source mutation boundary (_resolve_root_for_mutation) ---
    from modules.flow_gate.services.remote_tool_service import _OpError
    with pytest.raises(_OpError) as exc6:
        remote_tool_service._resolve_root_for_mutation({"project": "flowgate", "group_id": gid}, "write")
    assert exc6.value.status == 409
    assert exc6.value.details.get("cause") == "branch_merge_claim_active"

    # --- Verify Guard 7: worktree provisioning & repair blocked during active claim ---
    cfg = git_service.db_git.get_config("flowgate")
    assert worktree_service.ensure_worktree("flowgate", "default", gid) == "failed"
    assert worktree_service._ensure_worktree_locked(
        cfg, "flowgate", "FlowGate", gid, "wt-target-conflict"
    ) == "failed"

    # If claimed target worktree directory disappears, ensure must NOT run git worktree add
    wt_map_saved = wt_map["wt-target-conflict"]
    wt_map["wt-target-conflict"] = tmp_path / "vanished_wt"
    assert worktree_service.ensure_worktree("flowgate", "default", gid) == "failed"
    assert worktree_service._ensure_worktree_locked(
        cfg, "flowgate", "FlowGate", gid, "wt-target-conflict"
    ) == "failed"
    assert not (tmp_path / "vanished_wt").exists()
    wt_map["wt-target-conflict"] = wt_map_saved

    # --- Test Abort: terminal state releases claim and restores worktree ---
    abort_res = branch_merge.abort("flowgate", merge_id)
    assert abort_res["ok"] is True
    assert abort_res["result"]["status"] == "aborted"
    assert abort_res["result"]["workspace_cleaned"] is False

    # Claim is released!
    assert git_service.get_branch_merge_group_claim(gid) is None

    # Worktree was reset via git merge --abort and is clean, directory preserved
    assert not git_service._merge_in_progress(wt_target_dir)
    assert wt_target_dir.exists()
    assert (wt_target_dir / "conflict.txt").read_text(encoding="utf-8") == "target version\n"


def test_claim_release_on_approve(repo, tmp_path, monkeypatch):
    """Test that approval / completion releases the claim while preserving the worktree."""
    wt_dir = tmp_path / "wt_approve"
    _git(repo, "worktree", "add", str(wt_dir), "-b", "wt-approve-branch")
    (wt_dir / "app.txt").write_text("v1\n", encoding="utf-8")
    _git(wt_dir, "add", "app.txt")
    _git(wt_dir, "commit", "-m", "init commit")

    _git(repo, "checkout", "-b", "feat-app")
    (repo / "app.txt").write_text("v2\n", encoding="utf-8")
    _git(repo, "add", "app.txt")
    _git(repo, "commit", "-m", "feat commit")
    _git(repo, "checkout", "main")

    gid = "flowgate.default.501"
    groups_records = {gid: {"group_id": gid, "project_id": "flowgate", "deleted_at": None}}
    git_states = {gid: {"group_id": gid, "project_id": "flowgate", "worktree_registered": True, "branch": "wt-approve-branch"}}
    wt_map = {"wt-approve-branch": wt_dir}

    monkeypatch.setattr(db_groups, "get_by_id", lambda g: groups_records.get(g))
    monkeypatch.setattr(git_service, "_project_name", lambda pid: "FlowGate")
    monkeypatch.setattr(git_service, "_project_of_group", lambda g: "flowgate")
    monkeypatch.setattr(git_service.db_git, "get_state", lambda g: git_states.get(g))
    monkeypatch.setattr(git_service, "src_root", lambda pname, b: wt_map.get(b))

    res = git_service.merge_branches(
        "flowgate",
        source_branch="feat-app", target_branch="wt-approve-branch",
        source_kind="branch",
        target_kind="worktree", target_group_id=gid,
        push=False,
    )
    assert res["status"] == "conflict"
    merge_id = res["merge_id"]
    assert git_service.get_branch_merge_group_claim(gid) is not None

    # Complete review / approve
    session = git_service.db_git.get_session(merge_id)
    ctx = git_service.db_git.session_context(session)
    comp_res = branch_merge.complete_reviewed(merge_id, ctx, pushed=False)
    assert comp_res["ok"] is True

    # Claim released!
    assert git_service.get_branch_merge_group_claim(gid) is None
    # Actual worktree preserved!
    assert wt_dir.exists()


def test_restart_and_recovery_worktree_target(repo, tmp_path, monkeypatch):
    """Test restart/reload re-resolving actual worktree and failing closed on invalid states."""
    wt_dir = tmp_path / "wt_recovery"
    _git(repo, "worktree", "add", str(wt_dir), "-b", "wt-recover-branch")

    gid = "flowgate.default.601"
    groups_records = {gid: {"group_id": gid, "project_id": "flowgate", "deleted_at": None}}
    git_states = {gid: {"group_id": gid, "project_id": "flowgate", "worktree_registered": True, "branch": "wt-recover-branch"}}
    wt_map = {"wt-recover-branch": wt_dir}

    monkeypatch.setattr(db_groups, "get_by_id", lambda g: groups_records.get(g))
    monkeypatch.setattr(git_service, "_project_name", lambda pid: "FlowGate")
    monkeypatch.setattr(git_service, "_project_of_group", lambda g: "flowgate")
    monkeypatch.setattr(git_service.db_git, "get_state", lambda g: git_states.get(g))
    monkeypatch.setattr(git_service, "src_root", lambda pname, b: wt_map.get(b))

    session = {
        "merge_id": 7777,
        "owner_type": "branch_merge",
        "project_id": "flowgate",
        "group_id": None,
        "context": {
            "merge_target": {
                "branch": "wt-recover-branch",
                "base_branch": "main",
                "is_project_base": False,
                "target_kind": "worktree",
                "target_group_id": gid,
                "managed_workspace": False,
                "owner": "test-owner",
            }
        }
    }

    # 1. Normal resolution recovers the same actual worktree
    target_ctx = merge_target.resolve_session_target(session)
    assert target_ctx.target_kind == "worktree"
    assert target_ctx.target_group_id == gid
    assert target_ctx.managed_workspace is False
    assert target_ctx.root == wt_dir.resolve()
    assert merge_target.workspace_ownership(target_ctx) == merge_target.OWN_OWNED

    # 2. Missing/deleted group fails closed (root=None, OWN_MISMATCH)
    groups_records[gid]["deleted_at"] = "2026-09-28T00:00:00Z"
    target_deleted = merge_target.resolve_session_target(session)
    assert target_deleted.root is None
    assert merge_target.workspace_ownership(target_deleted) == merge_target.OWN_MISMATCH
    groups_records[gid]["deleted_at"] = None

    # 3. Ledger branch mismatch fails closed
    git_states[gid]["branch"] = "wrong-branch"
    target_mismatch = merge_target.resolve_session_target(session)
    assert target_mismatch.root is None
    assert merge_target.workspace_ownership(target_mismatch) == merge_target.OWN_MISMATCH
    git_states[gid]["branch"] = "wt-recover-branch"

    # 4. Missing worktree dir fails closed
    wt_map["wt-recover-branch"] = tmp_path / "vanished_dir"
    target_missing = merge_target.resolve_session_target(session)
    assert target_missing.root is None
    assert merge_target.workspace_ownership(target_missing) == merge_target.OWN_MISMATCH
    wt_map["wt-recover-branch"] = wt_dir


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


def test_claim_query_failure_fails_closed(repo, tmp_path, monkeypatch):
    """Test that transient claim-query failure causes all guards to fail closed."""
    gid = "flowgate.default.701"
    branch = "wt-query-fail"
    wt_dir = tmp_path / "wt_query_fail"
    _git(repo, "worktree", "add", str(wt_dir), "-b", branch)

    groups_records = {gid: {"group_id": gid, "project_id": "flowgate", "deleted_at": None}}
    git_states = {gid: {"group_id": gid, "project_id": "flowgate", "worktree_registered": True, "branch": branch, "status": "none"}}
    wt_map = {branch: wt_dir}

    monkeypatch.setattr(db_groups, "get_by_id", lambda g: groups_records.get(g))
    monkeypatch.setattr(git_service, "_project_name", lambda pid: "FlowGate")
    monkeypatch.setattr(git_service, "_project_of_group", lambda g: "flowgate")
    monkeypatch.setattr(git_service.db_git, "get_state", lambda g: git_states.get(g))
    monkeypatch.setattr(git_service.db_git, "list_states_of_project", lambda pid: list(git_states.values()))
    monkeypatch.setattr(git_service, "src_root", lambda pname, b: wt_map.get(b))

    def _raising_list_open_sessions():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(git_service.db_git, "list_open_sessions", _raising_list_open_sessions)

    # 1. get_branch_merge_group_claim raises GitServiceError (fails closed)
    with pytest.raises(GitServiceError) as exc_claim:
        git_service.get_branch_merge_group_claim(gid)
    assert exc_claim.value.status == 500
    assert exc_claim.value.code == "branch_merge_claim_query_failed"

    # 2. Guard 1: AI admission fails closed
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc1:
        admission.start_run(
            project_id="flowgate", group_id=gid, module="default",
            doc_ref=f"{gid}.0001-R", action_scope="chat_interactive",
            mode="interactive", continuation_target_seq=None,
            continuation_review_mode=False, continuation_instruction_mode=None,
            continuation_locale="ko", issued_to="ai-worker",
            api_base_url="http://127.0.0.1", mention_builder=lambda a, b: None,
        )
    assert exc1.value.status_code == 500
    assert exc1.value.detail.get("code") == "branch_merge_claim_query_failed"

    # 3. Guard 2: update-from-base fails closed
    with pytest.raises(GitServiceError) as exc2:
        finalize_service.update_from_base(gid)
    assert exc2.value.code == "branch_merge_claim_query_failed"

    # 4. Guard 3: group finalize fails closed
    with pytest.raises(GitServiceError) as exc3:
        finalize_service.finalize(gid, action="wait")
    assert exc3.value.code == "branch_merge_claim_query_failed"

    # 5. Guard 4: TR commit fails closed
    tr_res = commit_service.create_tr_commit(gid, "TR Commit Title")
    assert tr_res["committed"] is False
    assert tr_res["skipped_reason"] == "branch_merge_claim_query_failed"

    # 6. Guard 4b: cancel prelock gate fails closed
    gate_res = commit_service._cancel_prelock_gate(gid)
    assert gate_res["blocked_reason"] == "git_busy"
    assert gate_res["block_sub"] == "branch_merge_claim_query_failed"

    # 7. Guard 5: cleanup disposed group fails closed
    disp_res = cleanup_service.cleanup_disposed_group("flowgate", gid)
    assert disp_res["ok"] is False
    assert disp_res["reason"] == "branch_merge_claim_query_failed"

    # 8. Guard 5b: cleanup terminal slots fails closed (pending)
    git_states[gid]["status"] = "merged"
    term_res = cleanup_service.cleanup_terminal_slots("flowgate")
    assert any(
        p.get("group_id") == gid and p.get("reason") == "branch_merge_claim_query_failed"
        for p in term_res.get("terminal_cleanup", {}).get("pending", [])
    )

    # 9. Guard 5c: slot cleanup returns False
    assert worktree_service._cleanup_group_slot("flowgate", gid) is False

    # 10. Guard 6: source mutation boundary fails closed
    from modules.flow_gate.services.remote_tool_service import _OpError
    with pytest.raises(_OpError) as exc6:
        remote_tool_service._resolve_root_for_mutation({"project": "flowgate", "group_id": gid}, "write")
    assert exc6.value.status == 500
    assert exc6.value.details.get("cause") == "branch_merge_claim_query_failed"

    # 11. Guard 7: worktree provisioning fails closed
    cfg = git_service.db_git.get_config("flowgate")
    assert worktree_service.ensure_worktree("flowgate", "default", gid) == "failed"
    assert worktree_service._ensure_worktree_locked(
        cfg, "flowgate", "FlowGate", gid, branch
    ) == "failed"


def test_worktree_provisioning_blocked_during_claim_and_disappeared_target(repo, tmp_path, monkeypatch):
    """Test that active claim blocks worktree provisioning / repair even if directory disappeared."""
    gid = "flowgate.default.801"
    branch = "wt-prov-blocked"
    wt_dir = tmp_path / "wt_prov_blocked"
    _git(repo, "worktree", "add", str(wt_dir), "-b", branch)

    groups_records = {gid: {"group_id": gid, "project_id": "flowgate", "deleted_at": None}}
    git_states = {gid: {"group_id": gid, "project_id": "flowgate", "worktree_registered": True, "branch": branch, "status": "none"}}
    wt_map = {branch: wt_dir}

    monkeypatch.setattr(db_groups, "get_by_id", lambda g: groups_records.get(g))
    monkeypatch.setattr(git_service, "_project_name", lambda pid: "FlowGate")
    monkeypatch.setattr(git_service, "_project_of_group", lambda g: "flowgate")
    monkeypatch.setattr(git_service.db_git, "get_state", lambda g: git_states.get(g))
    monkeypatch.setattr(git_service, "src_root", lambda pname, b: wt_map.get(b))

    active_claim = {
        "merge_id": 9991,
        "project_id": "flowgate",
        "group_id": gid,
        "target_branch": branch,
        "source_branch": "feat-something",
        "attempt_state": "conflict",
    }
    monkeypatch.setattr(branch_merge, "get_branch_merge_group_claim", lambda g: active_claim if g == gid else None)

    provision_failures = []
    monkeypatch.setattr(
        git_service, "_fail_worktree",
        lambda *args, **kwargs: provision_failures.append((args, kwargs)),
    )
    attempt_records = []
    monkeypatch.setattr(
        worktree_service, "_record_attempt",
        lambda *args, **kwargs: attempt_records.append((args, kwargs)),
    )

    cfg = git_service.db_git.get_config("flowgate")

    # 1. With worktree directory present, provisioning / ensure returns 'failed'
    res_ensure = worktree_service.ensure_worktree("flowgate", "default", gid)
    assert res_ensure == "failed"
    res_locked = worktree_service._ensure_worktree_locked(cfg, "flowgate", "FlowGate", gid, branch)
    assert res_locked == "failed"

    # 2. Target worktree directory disappears during open attempt
    wt_missing = tmp_path / "wt_missing_dir"
    wt_map[branch] = wt_missing

    # ensure_worktree must block provisioning, must NOT run git worktree add
    res_ensure_missing = worktree_service.ensure_worktree("flowgate", "default", gid)
    assert res_ensure_missing == "failed"
    assert not wt_missing.exists()

    res_locked_missing = worktree_service._ensure_worktree_locked(cfg, "flowgate", "FlowGate", gid, branch)
    assert res_locked_missing == "failed"
    assert not wt_missing.exists()

    # A live claim is normal ownership protection.  It may keep the legacy
    # 'failed' return value for compatibility, but must not persist/emit a
    # provisioning failure.
    assert provision_failures == []
    assert any(
        args[1] == "blocked" and args[2] == "branch_merge_claim_active"
        for args, _kwargs in attempt_records
    )
