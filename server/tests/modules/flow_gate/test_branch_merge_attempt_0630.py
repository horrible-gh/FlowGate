"""flowgate.default.0630 T0005 — ordinary Branch Merge conflict as a persistent attempt.

Real git end-to-end on a temporary SQLite with the real migrations (the same harness as
test_git_merge_target_0594.py). Every test builds its OWN project (own origin, own base
checkout), so nothing leaks between cases. The numbers are T0005 §15's required tests.

   1/2  clean merge push=false / push=true — attempt row created and completed
   3/4/5 conflict → persistent attempt, managed workspace kept, no `merge --abort`
   6    multi-file partial resolve
   7/8  T#3: no AI auto-start on conflict; explicit /ai-resolve route starts resolver
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
import copy
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

_SERVER_DIR = Path(__file__).resolve().parents[3]
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
def storage_dir(tmp_path_factory):
    # Honor pytest's configured scratch basetemp for this isolated storage fixture too.
    tmp = str(tmp_path_factory.mktemp("fg-bm0630-storage"))
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



# ── 6 partial resolve ────────────────────────────────────────────────────────



# ── 7/8 T#3: no auto-start on conflict; explicit /ai-resolve starts resolver ──────────

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











# ── 9/10 AI resolution → review, nothing moved ───────────────────────────────



# ── 11/12 approve ────────────────────────────────────────────────────────────







# ── 13/14 stale target / stale remote ────────────────────────────────────────





# ── 15 abort ─────────────────────────────────────────────────────────────────



# ── 16 recovery ──────────────────────────────────────────────────────────────





class _Crash(BaseException):
    """A process death: not caught by any `except Exception` on the way out."""




# ── 17 large conflict keeps the tool-driven read contract ────────────────────



# ── 18 EOL-only ──────────────────────────────────────────────────────────────



# ── 19 duplicate approve ─────────────────────────────────────────────────────



# ── 20/21 delete guards ──────────────────────────────────────────────────────





# ── owner isolation / token binding ──────────────────────────────────────────





# ── 24 state transitions the Git UIs render ──────────────────────────────────





# ── flowgate.default.0683 T0004 — review handoff / target blocker / plan-time recovery ──
#
# The 0683 case (flowgate.v02.0033.0007-AC): branch merge #206 (main → v0.2) sat in
# resolved_pending_review and every v0.2 approval was refused with a bare
# "another finalize attempt owns this target branch's workspace".

_OTHER_GROUP = "bmx.default.0001"


def _plan_finalize(p: Proj, target: str):
    """A new group finalize planned onto ``target`` (what an approval precheck runs)."""
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services.git import merge_target
    return merge_target.plan_finalize_target(_OTHER_GROUP, p.pid, db_git.get_config(p.pid), target)


def _busy(p: Proj, target: str = "develop"):
    from modules.flow_gate.services.git.credentials import GitServiceError
    with pytest.raises(GitServiceError) as caught:
        _plan_finalize(p, target)
    assert caught.value.code == "merge_target_busy"
    return caught.value


def _review_pending(p: Proj):
    """conflict → AI resolver started → AI resolves every file → resolved_pending_review."""
    from modules.flow_gate.services.git import branch_merge
    out, before = p.conflict()
    merge_id = out["merge_id"]
    branch_merge.start_resolver(p.pid, merge_id, start_run=lambda prov, msgs: "run-0683")
    assert _ctx(merge_id)["ai"]["status"] == "running"
    assert _resolve_all(p, merge_id, run_id="run-0683")["status"] == "resolved_pending_review"
    return merge_id, before
















def _crashed_branch_merge(p: Proj, monkeypatch) -> dict:
    """A branch merge whose process died after the attempt + workspace were written and
    before ``git merge`` ran: an open attempt with no conflict, its job still 'running'."""
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target
    p.branch("develop")
    p.branch("feature")
    p.commit_on("feature", {"feature.txt": "feature\n"})
    real_run_git = svc._run_git
    workspace_tree = p.workspace("develop") / "tree"

    def crashing_run_git(args, cwd=None, **kwargs):
        if "--no-ff" in args and cwd is not None and Path(cwd) == workspace_tree:
            raise _Crash()
        return real_run_git(args, cwd=cwd, **kwargs)

    real_release = merge_target.release_workspace
    monkeypatch.setattr(svc, "_run_git", crashing_run_git)
    # a dead process runs no ``finally``: the workspace and its marker stay behind
    monkeypatch.setattr(merge_target, "release_workspace", lambda ctx: False)
    with pytest.raises(_Crash):
        svc.merge_branches(p.pid, "feature", "develop", push=False)
    monkeypatch.setattr(svc, "_run_git", real_run_git)
    monkeypatch.setattr(merge_target, "release_workspace", real_release)
    session = db_git.list_branch_merge_sessions(p.pid)[0]
    assert _ctx(session["merge_id"])["attempt_state"] == "in_progress"
    assert (p.workspace("develop") / "owner.json").exists()
    return session


def _end_attempt_job(session: dict) -> None:
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import merge_target
    job_id = merge_target.target_record(session)["lock_holder"][len(merge_target.JOB_HOLDER_PREFIX):]
    with get_store().transaction():
        get_store()._execute(
            "UPDATE operation_job SET status = 'failed', lease_owner = NULL, lease_token = NULL, "
            "lease_until = NULL WHERE job_id = ?", [job_id])










# 0530 T0004: exercise the human HTTP approval boundary on real isolated git repos.
