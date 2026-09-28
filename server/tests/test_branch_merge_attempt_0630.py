"""flowgate.default.0630 T0005 — ordinary Branch Merge conflict as a persistent attempt.

Real git end-to-end on a temporary SQLite with the real migrations (the same harness as
test_git_merge_target_0594.py). Every test builds its OWN project (own origin, own base
checkout), so nothing leaks between cases. The numbers are T0005 §15's required tests.

   1/2  clean merge push=false / push=true — attempt row created and completed
   3/4/5 conflict → persistent attempt, managed workspace kept, no `merge --abort`
   6    multi-file partial resolve
   7/8  AI auto-start from the Branch Merge route; start failure → manual fallback
   9    AI resolution → resolved_pending_review (never auto-approved)
  10    target ref / remote untouched before approval
  11/12 approve push=false (local only) / push=true (remote)
  13    stale target blocked
  14    stale remote → the existing rejection/re-review path, remote never overwritten
  15    abort restores and cleans up
  16    restart recovery (match → kept; MERGE_HEAD gone → interrupted, never success)
  17    large conflict keeps the tool-driven (`chunk_text: omitted`) contract
  18    EOL-only conflict goes to review without an AI run
  19    duplicate approve is idempotent
  20/21 source / target branch delete guard
  24    server attempt state transitions the Git UIs render
  plus: migration 123 shape, owner isolation between group and project routes,
        worker-token binding of the project-scoped resolve endpoint.
"""
from __future__ import annotations

import base64
import itertools
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "*")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite3")
os.environ.setdefault("FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode())

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not _GIT, reason="git binary unavailable")


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
def storage_dir():
    tmp = tempfile.mkdtemp(prefix="fg-bm0630-storage-")
    previous = os.environ.get("FLOWGATE_STORAGE_DIR")
    os.environ["FLOWGATE_STORAGE_DIR"] = tmp
    yield Path(tmp)
    if previous is None:
        os.environ.pop("FLOWGATE_STORAGE_DIR", None)
    else:
        os.environ["FLOWGATE_STORAGE_DIR"] = previous
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="module")
def mock_db(storage_dir):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    db = _MockDB(db_path)
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
    os.unlink(db_path)


def _git(args, cwd=None, check=True):
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t",
    })
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env)
    if check:
        assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout


_COUNTER = itertools.count(1)


class Proj:
    """One isolated project: bare origin, seed clone, enabled git config, base checkout."""

    def __init__(self, tmp: Path):
        from modules.flow_gate.db import projects
        from modules.flow_gate.services import git_service as svc

        n = next(_COUNTER)
        self.tmp = tmp
        self.pid = f"bm{n:02d}"
        self.name = f"BM{n:02d}"
        projects.create({"project_id": self.pid, "project_name": self.name})
        self.bare = tmp / f"{self.pid}.git"
        self.seed = tmp / f"{self.pid}-seed"
        _git(["init", "--bare", "-b", "main", str(self.bare)])
        _git(["init", "-b", "main", str(self.seed)])
        _git(["config", "core.autocrlf", "false"], cwd=self.seed)
        # No EOL conversion anywhere: the EOL-only case (18) must see the bytes it wrote.
        (self.seed / ".gitattributes").write_bytes(b"* -text\n")
        (self.seed / "README.md").write_bytes(b"hello\n")
        (self.seed / "same.txt").write_bytes(b"base\n")
        (self.seed / "other.txt").write_bytes(b"base\n")
        _git(["add", "-A"], cwd=self.seed)
        _git(["commit", "-m", "init"], cwd=self.seed)
        _git(["remote", "add", "origin", str(self.bare)], cwd=self.seed)
        _git(["push", "origin", "main"], cwd=self.seed)
        svc.save_config(self.pid, {
            "repo_url": self.bare.as_uri(), "provider": "generic",
            "base_branch": "main", "default_finalize_action": "merge", "enabled": True,
        })
        if not (self.base / ".git").exists():
            outcome = svc.provision_base(self.pid, "test")
            assert (self.base / ".git").exists(), f"base checkout was not provisioned: {outcome}"
        _git(["config", "core.autocrlf", "false"], cwd=self.base)

    @property
    def base(self) -> Path:
        from modules.flow_gate.storage.paths import src_root
        return src_root(self.name, "main")

    def branch(self, name: str, source: str = "main") -> None:
        from modules.flow_gate.services import git_service as svc
        svc.create_branch(self.pid, name, source)

    def commit_on(self, branch: str, files: dict, message: str = "change") -> str:
        """Commit on a LOCAL branch without touching the base checkout. The base branch
        itself is checked out AT the base checkout, so its commits are made there (the
        way another finalize or a person would move it)."""
        if branch == "main":
            for path, content in files.items():
                data = content if isinstance(content, bytes) else content.encode("utf-8")
                (self.base / path).write_bytes(data)
            _git(["add", "-A"], cwd=self.base)
            _git(["commit", "-m", message], cwd=self.base)
            return _git(["rev-parse", "HEAD"], cwd=self.base).strip()
        wt = self.tmp / f"wt-{uuid.uuid4().hex[:8]}"
        _git(["worktree", "add", str(wt), branch], cwd=self.base)
        _git(["config", "core.autocrlf", "false"], cwd=wt)
        for path, content in files.items():
            target = wt / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_bytes(content.encode("utf-8"))
        _git(["add", "-A"], cwd=wt)
        _git(["commit", "-m", message], cwd=wt)
        sha = _git(["rev-parse", "HEAD"], cwd=wt).strip()
        _git(["worktree", "remove", "--force", str(wt)], cwd=self.base)
        return sha

    def publish(self, branch: str) -> None:
        _git(["push", "origin", f"{branch}:{branch}"], cwd=self.base)

    def sha(self, ref: str) -> str:
        return _git(["rev-parse", ref], cwd=self.base).strip()

    def remote_sha(self, branch: str) -> str:
        out = _git(["ls-remote", str(self.bare), f"refs/heads/{branch}"], cwd=self.base).split()
        return out[0] if out else ""

    def workspace(self, branch: str) -> Path:
        from modules.flow_gate.services.git import merge_target
        return merge_target.workspace_dir(self.pid, branch)

    def conflict(self, *, target="develop", push=False, files=("same.txt",), publish=False):
        """source and target both change ``files`` → a real content conflict."""
        from modules.flow_gate.services import git_service as svc
        if target != "main":
            self.branch(target)
        self.branch("feature")
        self.commit_on(target, {f: f"{target} version\n" for f in files}, "target side")
        self.commit_on("feature", {f: "feature version\n" for f in files}, "source side")
        if publish:
            self.publish(target)
        before = self.sha(f"refs/heads/{target}")
        out = svc.merge_branches(self.pid, "feature", target, push=push)
        return out, before


@pytest.fixture
def proj(tmp_path, monkeypatch, mock_db):
    from modules.flow_gate.services import git_service as svc
    monkeypatch.setattr(svc, "_sweep_daemon_started", True)   # never spawn the thread
    p = Proj(tmp_path)
    yield p
    svc.delete_config(p.pid)


def _session(merge_id: int) -> dict:
    from modules.flow_gate.db import git_integration as db_git
    return db_git.get_session(merge_id)


def _ctx(merge_id: int) -> dict:
    from modules.flow_gate.db import git_integration as db_git
    return db_git.session_context(_session(merge_id))


def _view(p: Proj, merge_id: int) -> dict:
    from modules.flow_gate.services.git import branch_merge
    return branch_merge.get_attempt(p.pid, merge_id)["result"]


def _resolve_all(p: Proj, merge_id: int, *, run_id=None, content="merged\n"):
    from modules.flow_gate.services import git_service as svc
    files = [row["path"] for row in svc.db_git.session_files(merge_id)]
    return svc.resolve_conflicts(
        None, merge_id, [{"path": f, "content": f"{f} {content}"} for f in files], True,
        resolver_run_id=run_id, project_id=p.pid,
    )["result"]


def _approve(p: Proj, merge_id: int, attempt_id=None, fingerprint=None):
    from modules.flow_gate.services import git_service as svc
    fp = fingerprint or _ctx(merge_id)["review_fingerprint"]
    return svc.approve_merge_review(
        None, merge_id, attempt_id=attempt_id or str(uuid.uuid4()),
        review_fingerprint=fp, authority="human", project_id=p.pid,
    )["result"]


# ── Migration 123 ────────────────────────────────────────────────────────────

def test_migration_123_relaxes_group_and_adds_owner_columns(mock_db):
    cols = {row["name"]: row for row in mock_db.fetch_all("PRAGMA table_info(git_merge_session)")}
    assert {"owner_type", "project_id", "kind", "context", "touched_at", "finalize_action"} <= set(cols)
    assert cols["group_id"]["notnull"] == 0
    fks = mock_db.fetch_all("PRAGMA foreign_key_list(git_merge_session_file)")
    assert any(fk["table"] == "git_merge_session" for fk in fks)   # child FK not rewritten
    indexes = {row["name"] for row in mock_db.fetch_all("PRAGMA index_list(git_merge_session)")}
    assert "uq_git_merge_session_open" in indexes
    assert "idx_git_merge_session_project_status" in indexes
    for dialect in ("sqlite", "postgres", "mysql"):
        assert (_SERVER_DIR / "sql" / "migrations" / dialect / "123_git_merge_session_owner.sql").is_file()


# ── 1/2 clean merges ─────────────────────────────────────────────────────────

def test_01_clean_merge_push_false_completes_attempt_local_only(proj):
    from modules.flow_gate.services import git_service as svc
    p = proj
    p.branch("develop")
    p.branch("feature")
    p.commit_on("feature", {"feature.txt": "feature\n"})
    out = svc.merge_branches(p.pid, "feature", "develop", push=False)
    assert out["status"] == "merged" and out["pushed"] is False and out["workspace_cleaned"] is True
    assert p.sha("refs/heads/develop") == out["target_head"]
    assert p.remote_sha("develop") == ""                       # origin never learned it
    session = _session(out["merge_id"])
    assert session["kind"] == "branch_merge" and session["owner_type"] == "branch_merge"
    assert session["group_id"] is None and session["project_id"] == p.pid
    assert session["status"] == "done"
    assert _ctx(out["merge_id"])["attempt_state"] == "completed"
    assert not (p.workspace("develop") / "tree").exists()
    assert _view(p, out["merge_id"])["state"] == "completed"


def test_02_clean_merge_push_true_publishes_and_completes(proj):
    from modules.flow_gate.services import git_service as svc
    p = proj
    p.branch("develop")
    p.branch("feature")
    p.commit_on("feature", {"feature.txt": "feature\n"})
    out = svc.merge_branches(p.pid, "feature", "develop", push=True)
    assert out["status"] == "merged" and out["pushed"] is True
    assert p.remote_sha("develop") == out["target_head"] == p.sha("refs/heads/develop")
    assert _session(out["merge_id"])["status"] == "done"
    events = [e["type"] for e in _ctx(out["merge_id"])["attempt_events"]]
    assert events[0] == "git_branch_merge_started" and events[-1] == "git_branch_merge_completed"


def test_02b_clean_merge_into_base_push_false_applies_base_checkout(proj):
    from modules.flow_gate.services import git_service as svc
    p = proj
    p.branch("feature")
    p.commit_on("feature", {"feature.txt": "feature\n"})
    main_before = p.sha("refs/heads/main")
    out = svc.merge_branches(p.pid, "feature", "main", push=False)
    assert out["status"] == "merged" and out["target_before"] == main_before
    assert p.sha("refs/heads/main") == out["target_head"] != main_before
    assert (p.base / "feature.txt").read_text(encoding="utf-8") == "feature\n"
    assert _git(["status", "--porcelain"], cwd=p.base).strip() == ""
    assert p.remote_sha("main") == main_before
    assert not (p.workspace("main") / "tree").exists()


# ── 3/4/5 conflict is kept ───────────────────────────────────────────────────

def test_03_04_05_conflict_creates_persistent_attempt_and_keeps_workspace(proj):
    from modules.flow_gate.services import git_service as svc
    p = proj
    out, before = p.conflict(push=False)
    assert out["status"] == "conflict" and out["ok"] is True
    merge_id = out["merge_id"]
    assert out["conflict_files"] == ["same.txt"] and out["push"] is False
    session = _session(merge_id)
    assert session["status"] == "open" and session["group_id"] is None
    assert session["kind"] == "branch_merge" and session["project_id"] == p.pid
    ctx = _ctx(merge_id)
    assert ctx["attempt_state"] == "conflict"
    assert ctx["auto_authority"] is False and ctx["ai"]["auto_authority"] is False
    assert ctx["branch_merge"]["source_branch"] == "feature" and ctx["branch_merge"]["push"] is False
    assert ctx["resolver_baseline"]["base_head"] == before
    # 4: the managed workspace is still there, owned by this attempt
    tree = p.workspace("develop") / "tree"
    assert tree.is_dir()
    from modules.flow_gate.services.git import merge_target
    assert merge_target.read_owner_marker(p.workspace("develop"))["merge_id"] == merge_id
    # 5: no merge --abort — MERGE_HEAD and the markers are alive
    assert _git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=tree).strip()
    assert "<<<<<<<" in (tree / "same.txt").read_text(encoding="utf-8")
    # nothing moved
    assert p.sha("refs/heads/develop") == before
    assert svc.list_conflicts(None, merge_id, project_id=p.pid)["files"][0]["conflict_count"] == 1
    assert _view(p, merge_id)["state"] == "conflict"


# ── 6 partial resolve ────────────────────────────────────────────────────────

def test_06_multi_file_partial_resolve_keeps_the_rest_open(proj):
    from modules.flow_gate.services import git_service as svc
    p = proj
    out, _before = p.conflict(files=("same.txt", "other.txt"))
    merge_id = out["merge_id"]
    assert sorted(out["conflict_files"]) == ["other.txt", "same.txt"]
    first = svc.resolve_conflicts(
        None, merge_id, [{"path": "same.txt", "content": "merged same\n"}], False,
        project_id=p.pid,
    )["result"]
    assert first["status"] == "conflict" and first["remaining_conflicts"] == ["other.txt"]
    view = _view(p, merge_id)
    assert view["state"] == "conflict_remaining"
    assert view["resolved_count"] == 1 and view["unresolved"] == ["other.txt"]
    assert view["resolver_type"] == "human"


# ── 7/8 AI auto-start through the real route ─────────────────────────────────

def _client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.v1 import git_routes
    from modules.flow_gate.auth.middleware import get_current_user

    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "admin", "is_admin": True}
    return TestClient(app, raise_server_exceptions=True)


def _prepare_route_conflict(p: Proj):
    p.branch("develop")
    p.branch("feature")
    p.commit_on("develop", {"same.txt": "develop version\n"})
    p.commit_on("feature", {"same.txt": "feature version\n"})


def test_07_route_conflict_auto_starts_the_resolver_without_a_click(proj, monkeypatch):
    from modules.flow_gate.api.v1 import git_routes
    p = proj
    _prepare_route_conflict(p)
    calls = []

    def fake_start(**kwargs):
        calls.append(kwargs)
        return "run-auto-1"

    monkeypatch.setattr(git_routes, "_start_branch_merge_resolve_run", fake_start)
    resp = _client(monkeypatch).post(
        f"/api/v1/projects/{p.pid}/git/branches/merge",
        json={"source_branch": "feature", "target_branch": "develop", "push": False,
              "provider_id": "prov-x"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "conflict" and body["merge_id"]
    assert body["ai"]["status"] == "running" and body["ai"]["run_id"] == "run-auto-1"
    assert body["ai"]["auto_start"] is True and body["ai"]["auto_authority"] is False
    assert len(calls) == 1
    assert calls[0]["project_id"] == p.pid and calls[0]["merge_id"] == body["merge_id"]
    assert calls[0]["provider_id"] == "prov-x"                  # §12 tier 1
    # the start ran after the Git lock was released
    from modules.flow_gate.db import git_integration as db_git
    assert p.pid not in {row["project_id"] for row in db_git.list_locks()}
    events = [e["type"] for e in _ctx(body["merge_id"])["attempt_events"]]
    assert "git_branch_merge_conflict" in events and "git_branch_merge_ai_started" in events


def test_08_ai_start_failure_keeps_attempt_and_manual_fallback_reaches_review(proj, monkeypatch):
    from fastapi import HTTPException
    from modules.flow_gate.api.v1 import git_routes
    p = proj
    _prepare_route_conflict(p)

    def failing_start(**kwargs):
        raise HTTPException(status_code=409, detail={"code": "no_enabled_provider",
                                                      "message": "no provider"})

    monkeypatch.setattr(git_routes, "_start_branch_merge_resolve_run", failing_start)
    client = _client(monkeypatch)
    resp = client.post(
        f"/api/v1/projects/{p.pid}/git/branches/merge",
        json={"source_branch": "feature", "target_branch": "develop", "push": False},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    merge_id = body["merge_id"]
    assert body["ai"]["status"] == "start_failed"
    assert body["ai"]["error"]["code"] == "no_enabled_provider"
    assert _session(merge_id)["status"] == "open"
    assert _view(p, merge_id)["state"] == "conflict_remaining"
    # manual fallback through the project-scoped human route
    conflicts = client.get(f"/api/v1/projects/{p.pid}/git/merge/{merge_id}/conflicts").json()
    assert [f["path"] for f in conflicts["files"]] == ["same.txt"]
    resolved = client.post(
        f"/api/v1/projects/{p.pid}/git/merge/{merge_id}/resolve",
        json={"files": [{"path": "same.txt", "content": "merged by hand\n"}], "complete": True},
    ).json()
    assert resolved["result"]["status"] == "resolved_pending_review"
    review = client.get(f"/api/v1/projects/{p.pid}/git/merge/{merge_id}/review").json()["result"]
    assert review["resolver_type"] == "human" and review["owner_type"] == "branch_merge"
    assert review["source_branch"] == "feature" and review["target_branch"] == "develop"
    # the re-invoke route records a new start attempt (still failing here)
    again = client.post(f"/api/v1/projects/{p.pid}/git/merge/{merge_id}/ai-resolve", json={})
    assert again.status_code == 409      # already in review: use the review screen


# ── 9/10 AI resolution → review, nothing moved ───────────────────────────────

def test_09_10_ai_resolution_stops_at_review_and_moves_nothing(proj):
    p = proj
    out, before = p.conflict(push=True, publish=True)
    merge_id = out["merge_id"]
    remote_before = p.remote_sha("develop")
    result = _resolve_all(p, merge_id, run_id="run-ai-9")
    assert result["status"] == "resolved_pending_review" and result["merge_commit"] is None
    ctx = _ctx(merge_id)
    assert ctx["review_state"] == "resolved_pending_review"
    assert ctx["resolver_run_id"] == "run-ai-9" and ctx["review_fingerprint"]
    assert ctx["conflict_origins"]
    view = _view(p, merge_id)
    assert view["state"] == "resolved_pending_review" and view["resolver_type"] == "ai"
    # 10: target ref and remote untouched before approval
    assert p.sha("refs/heads/develop") == before
    assert p.remote_sha("develop") == remote_before
    events = [e["type"] for e in ctx["attempt_events"]]
    assert "git_branch_merge_resolved" in events and "git_branch_merge_review_pending" in events


# ── 11/12 approve ────────────────────────────────────────────────────────────

def test_11_approve_push_false_is_local_only(proj):
    p = proj
    out, before = p.conflict(push=False, publish=True)
    merge_id = out["merge_id"]
    remote_before = p.remote_sha("develop")
    _resolve_all(p, merge_id)
    result = _approve(p, merge_id)
    assert result["status"] == "merged" and result["pushed"] is False
    head = p.sha("refs/heads/develop")
    assert head != before and _git(["rev-parse", f"{head}^1"], cwd=p.base).strip() == before
    assert _git(["show", f"{head}:same.txt"], cwd=p.base) == "same.txt merged\n"
    assert p.remote_sha("develop") == remote_before
    assert _session(merge_id)["status"] == "done"
    assert not (p.workspace("develop") / "tree").exists()
    view = _view(p, merge_id)
    assert view["state"] == "completed" and view["pushed"] is False and view["local_applied"] is True


def test_12_approve_push_true_publishes(proj):
    p = proj
    out, _before = p.conflict(push=True, publish=True)
    merge_id = out["merge_id"]
    _resolve_all(p, merge_id)
    result = _approve(p, merge_id)
    assert result["status"] == "merged" and result["pushed"] is True
    assert p.remote_sha("develop") == p.sha("refs/heads/develop")
    assert _view(p, merge_id)["remote_applied"] is True


def test_12b_base_target_conflict_lives_in_a_detached_workspace_and_applies_on_approval(proj):
    p = proj
    out, before = p.conflict(target="main", push=False)
    merge_id = out["merge_id"]
    # the shared base checkout is untouched while the conflict waits
    assert _git(["status", "--porcelain"], cwd=p.base).strip() == ""
    assert p.sha("refs/heads/main") == before
    assert (p.workspace("main") / "tree").is_dir()
    _resolve_all(p, merge_id)
    result = _approve(p, merge_id)
    assert result["status"] == "merged"
    assert p.sha("refs/heads/main") != before
    assert (p.base / "same.txt").read_text(encoding="utf-8") == "same.txt merged\n"
    assert _git(["status", "--porcelain"], cwd=p.base).strip() == ""


# ── 13/14 stale target / stale remote ────────────────────────────────────────

def test_13_stale_target_is_blocked_and_nothing_moves(proj):
    p = proj
    out, before = p.conflict(target="main", push=False)
    merge_id = out["merge_id"]
    _resolve_all(p, merge_id)
    moved = p.commit_on("main", {"late.txt": "someone else\n"}, "concurrent change")
    from modules.flow_gate.services.git.credentials import GitServiceError as Err
    with pytest.raises(Err) as caught:
        _approve(p, merge_id)
    assert caught.value.code == "stale_target"
    assert p.sha("refs/heads/main") == moved
    assert _ctx(merge_id)["review_state"] == "resolved_pending_review"   # never left pending


def test_14_stale_remote_is_not_overwritten(proj):
    p = proj
    out, _before = p.conflict(push=True, publish=True)
    merge_id = out["merge_id"]
    _resolve_all(p, merge_id)
    # someone pushes to origin/develop while the review waits
    _git(["fetch", "origin"], cwd=p.seed)
    _git(["checkout", "-B", "develop", "origin/develop"], cwd=p.seed)
    (p.seed / "remote-only.txt").write_text("remote\n", encoding="utf-8")
    _git(["add", "-A"], cwd=p.seed)
    _git(["commit", "-m", "remote change"], cwd=p.seed)
    _git(["push", "origin", "develop"], cwd=p.seed)
    remote_head = p.remote_sha("develop")
    result = _approve(p, merge_id)
    assert result["status"] in ("re_review", "reconciling"), result
    ctx = _ctx(merge_id)
    assert ctx["review_state"] in ("re_review", "reconciling")
    # the remote still carries the other person's commit (or a descendant of it)
    now = p.remote_sha("develop")
    assert now == remote_head or _git(["merge-base", "--is-ancestor", remote_head, now],
                                      cwd=p.bare, check=False) == ""


# ── 15 abort ─────────────────────────────────────────────────────────────────

def test_15_abort_restores_and_cleans(proj):
    from modules.flow_gate.services import git_service as svc
    p = proj
    out, before = p.conflict()
    merge_id = out["merge_id"]
    result = svc.abort_merge(None, merge_id, project_id=p.pid)["result"]
    assert result["status"] == "aborted" and result["workspace_cleaned"] is True
    assert _session(merge_id)["status"] == "aborted"
    assert _ctx(merge_id)["attempt_state"] == "aborted"
    assert p.sha("refs/heads/develop") == before
    assert not (p.workspace("develop") / "tree").exists()
    assert _view(p, merge_id)["state"] == "aborted"
    # the same target can be merged again right away
    again = svc.merge_branches(p.pid, "feature", "develop", push=False)
    assert again["status"] == "conflict" and again["merge_id"] != merge_id


# ── 16 recovery ──────────────────────────────────────────────────────────────

def test_16_restart_recovery_keeps_a_matching_attempt_and_flags_a_broken_one(proj):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import branch_merge
    p = proj
    out, _before = p.conflict()
    merge_id = out["merge_id"]
    assert branch_merge.verify_open_attempt(_session(merge_id), at_boot=True) == "ok"
    # the periodic sweep never TTL-aborts a branch merge
    svc.merge_session_sweep(sessions=[_session(merge_id)])
    assert _session(merge_id)["status"] == "open"
    assert _view(p, merge_id)["state"] == "conflict"
    # the workspace loses its MERGE_HEAD behind FlowGate's back
    _git(["merge", "--abort"], cwd=p.workspace("develop") / "tree")
    assert branch_merge.verify_open_attempt(_session(merge_id), at_boot=True) == "interrupted"
    assert _session(merge_id)["status"] == "open"            # never read as success
    assert _view(p, merge_id)["state"] == "interrupted"
    # abort/retry path stays available
    assert svc.abort_merge(None, merge_id, project_id=p.pid)["result"]["status"] == "aborted"


def test_16b_startup_recovery_scan_handles_branch_merges(proj, monkeypatch):
    from modules.flow_gate.services import git_service as svc
    p = proj
    out, _before = p.conflict()
    merge_id = out["merge_id"]
    monkeypatch.setattr(svc, "_start_sweep_daemon", lambda: None)
    svc.startup_recovery()
    assert _session(merge_id)["status"] == "open"
    assert _view(p, merge_id)["state"] == "conflict"


class _Crash(BaseException):
    """A process death: not caught by any `except Exception` on the way out."""


def test_16c_crash_after_a_landed_base_merge_is_applied_on_recovery(proj, monkeypatch):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import branch_merge, merge_target
    p = proj
    p.branch("feature")
    p.commit_on("feature", {"feature.txt": "feature\n"})
    main_before = p.sha("refs/heads/main")
    real_run_git = svc._run_git

    def crashing_run_git(args, cwd=None, **kwargs):
        if args[:2] == ["merge", "--ff-only"] and not str(args[2]).startswith("origin/") \
                and Path(cwd) == p.base:
            raise _Crash()
        return real_run_git(args, cwd=cwd, **kwargs)

    monkeypatch.setattr(svc, "_run_git", crashing_run_git)
    monkeypatch.setattr(merge_target, "release_workspace", lambda ctx: False)
    with pytest.raises(_Crash):
        svc.merge_branches(p.pid, "feature", "main", push=False)
    monkeypatch.undo()
    from modules.flow_gate.db import git_integration as db_git
    session = db_git.list_branch_merge_sessions(p.pid)[0]
    assert _ctx(session["merge_id"])["attempt_state"] == "in_progress"
    assert p.sha("refs/heads/main") == main_before               # the crash left main behind
    branch_merge.verify_open_attempt(session, at_boot=True)
    merged = _session(session["merge_id"])
    assert merged["status"] == "done"
    result = _ctx(session["merge_id"])["attempt_result"]
    assert result["recovered"] is True and result["local_applied"] is True
    assert p.sha("refs/heads/main") != main_before
    assert (p.base / "feature.txt").read_text(encoding="utf-8") == "feature\n"
    assert not (p.workspace("main") / "tree").exists()


# ── 17 large conflict keeps the tool-driven read contract ────────────────────

def test_17_large_conflict_mention_omits_chunk_text_and_is_project_scoped(proj):
    from modules.flow_gate.api import token_routes
    p = proj
    big_target = "".join(f"target line {i} {'x' * 60}\n" for i in range(200))
    big_source = "".join(f"source line {i} {'y' * 60}\n" for i in range(200))
    p.branch("develop")
    p.branch("feature")
    p.commit_on("develop", {"same.txt": big_target})
    p.commit_on("feature", {"same.txt": big_source})
    from modules.flow_gate.services import git_service as svc
    out = svc.merge_branches(p.pid, "feature", "develop", push=False)
    mention = token_routes._build_conflict_mention(
        group_id=None, project_id=p.pid, merge_id=out["merge_id"], scratch_dir="",
        raw_token="tok", api_base_url="http://h/api/v1",
    )
    assert '"chunk_text": "omitted"' in mention
    assert "raw_content" not in mention and "x" * 60 not in mention
    assert f"http://h/api/v1/projects/{p.pid}/git/merge/{out['merge_id']}/resolve-token" in mention
    assert "owner: branch_merge" in mention and "group: None" not in mention
    assert len(mention) < 20_000


# ── 18 EOL-only ──────────────────────────────────────────────────────────────

def test_18_eol_only_conflict_goes_to_review_without_an_ai_run(proj):
    from modules.flow_gate.services.git import branch_merge
    p = proj
    p.branch("develop")
    p.branch("feature")
    p.commit_on("develop", {"same.txt": b"base\r\n"}, "crlf")
    p.commit_on("feature", {"same.txt": b"base\nadded\n"}, "append")
    from modules.flow_gate.services import git_service as svc
    out = svc.merge_branches(p.pid, "feature", "develop", push=False)
    if out["status"] == "merged":
        pytest.skip("this git merged the EOL change cleanly; no EOL-only conflict to test")
    merge_id = out["merge_id"]
    assert out["eol_only_paths"] == ["same.txt"] and out["remaining_conflicts"] == []
    started = []
    view = branch_merge.settle_new_conflict(
        p.pid, merge_id, start_run=lambda *_a: started.append(1) or "run-x",
    )
    assert started == []                                        # nothing for an AI to do
    assert view["state"] == "resolved_pending_review"
    assert p.sha("refs/heads/develop") == _ctx(merge_id)["base_head"]   # not committed


# ── 19 duplicate approve ─────────────────────────────────────────────────────

def test_19_duplicate_approve_is_idempotent(proj):
    p = proj
    out, _before = p.conflict(push=False)
    merge_id = out["merge_id"]
    _resolve_all(p, merge_id)
    attempt = str(uuid.uuid4())
    fingerprint = _ctx(merge_id)["review_fingerprint"]
    first = _approve(p, merge_id, attempt_id=attempt, fingerprint=fingerprint)
    head = p.sha("refs/heads/develop")
    second = _approve(p, merge_id, attempt_id=attempt, fingerprint=fingerprint)
    assert first["status"] == "merged" and second["status"] == "already_applied"
    assert p.sha("refs/heads/develop") == head


# ── 20/21 delete guards ──────────────────────────────────────────────────────

def test_20_21_open_attempt_pins_source_and_target(proj):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git.credentials import GitServiceError
    p = proj
    out, _before = p.conflict()
    assert svc.check_branch_delete(p.pid, "feature") == "branch_in_use"
    assert svc.check_branch_delete(p.pid, "develop") == "branch_in_use"
    with pytest.raises(GitServiceError) as caught:
        svc.delete_branch(p.pid, "feature")
    assert caught.value.code == "branch_in_use"
    assert caught.value.details["merge_id"] == out["merge_id"]
    assert caught.value.details["role"] == "source"
    svc.abort_merge(None, out["merge_id"], project_id=p.pid)
    assert svc.check_branch_delete(p.pid, "develop") != "branch_in_use"


def test_same_target_cannot_start_a_second_attempt(proj):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git.credentials import GitServiceError
    p = proj
    out, _before = p.conflict()
    p.branch("feature2")
    p.commit_on("feature2", {"f2.txt": "x\n"})
    with pytest.raises(GitServiceError) as caught:
        svc.merge_branches(p.pid, "feature2", "develop", push=False)
    assert caught.value.code == "merge_target_busy"
    assert caught.value.details["merge_id"] == out["merge_id"]


# ── owner isolation / token binding ──────────────────────────────────────────

def test_group_and_project_addressing_never_cross(proj):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git.credentials import GitServiceError
    p = proj
    out, _before = p.conflict()
    merge_id = out["merge_id"]
    for call in (
        lambda: svc.list_conflicts(f"{p.pid}.default.0001", merge_id),
        lambda: svc.list_conflicts(None, merge_id, project_id="someone-else"),
        lambda: svc.get_merge_review(f"{p.pid}.none.0000", merge_id),
        lambda: svc.abort_merge(f"{p.pid}.default.0001", merge_id),
    ):
        with pytest.raises(GitServiceError) as caught:
            call()
        assert caught.value.status == 404
    assert svc.merge_route_prefix(merge_id) == f"/projects/{p.pid}/git/merge/{merge_id}"
    assert svc.merge_session_owner_args(merge_id, "ignored") == (None, p.pid)


def test_project_resolve_token_route_binds_project_merge_and_no_group(proj, monkeypatch):
    from modules.flow_gate.api.v1 import git_routes
    p = proj
    out, _before = p.conflict()
    merge_id = out["merge_id"]
    consumed = []
    monkeypatch.setattr(git_routes.token_service, "consume", lambda tid, proj_id: consumed.append(tid))
    client = _client(monkeypatch)
    url = f"/api/v1/projects/{p.pid}/git/merge/{merge_id}/resolve-token"
    payload = {"files": [{"path": "same.txt", "content": "ai merged\n"}], "complete": True}
    for auth in (
        {"_is_user_jwt": True},
        {"action_scope": "resolve_conflict", "group_id": f"{p.pid}.default.0001",
         "project": p.pid, "merge_id": merge_id, "token_id": "t"},
        {"action_scope": "resolve_conflict", "group_id": None, "project": "other",
         "merge_id": merge_id, "token_id": "t"},
        {"action_scope": "resolve_conflict", "group_id": None, "project": p.pid,
         "merge_id": merge_id + 1000, "token_id": "t"},
    ):
        monkeypatch.setattr(git_routes, "verify_bearer", lambda request, _a=auth: _a)
        assert client.post(url, json=payload).status_code == 403
    monkeypatch.setattr(git_routes, "verify_bearer", lambda request: {
        "action_scope": "resolve_conflict", "group_id": None, "project": p.pid,
        "merge_id": merge_id, "token_id": "tok-ok", "ai_run_id": "run-tok",
    })
    resp = client.post(url, json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"]["status"] == "resolved_pending_review"
    assert consumed == ["tok-ok"]
    assert _view(p, merge_id)["resolver_type"] == "ai"


# ── 24 state transitions the Git UIs render ──────────────────────────────────

def test_24_server_state_transitions(proj, monkeypatch):
    from modules.flow_gate.services.git import branch_merge
    p = proj
    out, _before = p.conflict(files=("same.txt", "other.txt"))
    merge_id = out["merge_id"]
    states = [_view(p, merge_id)["state"]]                          # conflict
    monkeypatch.setattr(branch_merge, "_ai_run_status", lambda run_id: "running")
    branch_merge.start_resolver(p.pid, merge_id, start_run=lambda prov, msgs: "run-24")
    states.append(_view(p, merge_id)["state"])                      # ai_resolving
    monkeypatch.setattr(branch_merge, "_ai_run_status", lambda run_id: "finished")
    from modules.flow_gate.services import git_service as svc
    svc.resolve_conflicts(None, merge_id, [{"path": "same.txt", "content": "m\n"}], False,
                          resolver_run_id="run-24", project_id=p.pid)
    states.append(_view(p, merge_id)["state"])                      # conflict_remaining
    svc.resolve_conflicts(None, merge_id, [{"path": "other.txt", "content": "m\n"}], True,
                          project_id=p.pid)
    view = _view(p, merge_id)
    states.append(view["state"])                                    # resolved_pending_review
    assert view["resolver_type"] == "mixed"                         # AI + a person
    _approve(p, merge_id)
    states.append(_view(p, merge_id)["state"])                      # completed
    assert states == ["conflict", "ai_resolving", "conflict_remaining",
                      "resolved_pending_review", "completed"]


def test_24b_reject_returns_to_resolver_with_a_new_run(proj):
    from modules.flow_gate.services import git_service as svc
    p = proj
    out, _before = p.conflict()
    merge_id = out["merge_id"]
    _resolve_all(p, merge_id, run_id="run-a")
    result = svc.reject_merge_review(
        None, merge_id, reason="keep both lines", provider_id="prov", provider_pinned=True,
        start_run=lambda first_message: "run-b", project_id=p.pid,
    )["result"]
    assert result["status"] == "returned_to_resolver" and result["resolver_run_id"] == "run-b"
    ctx = _ctx(merge_id)
    assert ctx["review_state"] is None and ctx["ai"]["run_id"] == "run-b"
    tree = p.workspace("develop") / "tree"
    assert "<<<<<<<" in (tree / "same.txt").read_text(encoding="utf-8")   # conflict restored
    assert "git_branch_merge_review_rejected" in [e["type"] for e in ctx["attempt_events"]]
