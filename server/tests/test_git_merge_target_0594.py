"""flowgate.default.0594 T0012 — selected finalize target + managed target workspace.

Real git end-to-end (bare origin + service clone, like test_git_integration_0115.py)
on a temporary SQLite with the real migrations. Every test builds its OWN project
(own origin, own base checkout, own groups), so no state leaks between cases.

  A  base finalize without a target: legacy behavior + completed attempt record
  B  non-base clean finalize: managed workspace, base checkout never switched
  C  non-base conflict → resolve → review → approve stays on the target
  D  reject / re-review keep the target root
  E  startup recovery: E1 conflict re-found, E2 interrupted attempt closed,
     E3 a live in-progress attempt is left alone, E4 a crash after the merge
     commit / push / ledger write is reconciled from the recorded merge inputs,
     E5 a review that completed but was never closed is finished
  F  single-owner workspace (1st check + post-lock 2nd check)
  G  owner-marker mismatch fails closed; abort / TTL / startup / interrupted
     recovery never touch the merge state, the row or the workspace
  H  retarget contract H1..H5
  I  legacy session fallback
  J  non-base unmerge fail-closed (service + HTTP route)
  K  base unmerge unchanged
  L  internal slot / remote-only / missing targets refused on direct requests
  M  base-only paths keep the project base meaning
"""
from __future__ import annotations

import base64
import itertools
import json
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
needs_git = pytest.mark.skipif(not _GIT, reason="git binary unavailable")


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
def storage_dir():
    tmp = tempfile.mkdtemp(prefix="fg-mt0594-storage-")
    previous = os.environ.get("FLOWGATE_STORAGE_DIR")
    os.environ["FLOWGATE_STORAGE_DIR"] = tmp
    yield Path(tmp)
    if previous is None:
        os.environ.pop("FLOWGATE_STORAGE_DIR", None)
    else:
        os.environ["FLOWGATE_STORAGE_DIR"] = previous
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="module", autouse=True)
def patch_store(storage_dir):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    mock_db = _MockDB(db_path)
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    mock_db._conn.commit()
    from modules.flow_gate.db import connection as conn_mod

    original_store = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original_store
    mock_db.close()
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
    """One isolated project: bare origin, seed clone, enabled git config."""

    def __init__(self, tmp: Path):
        from modules.flow_gate.db import projects
        from modules.flow_gate.services import git_service as svc

        n = next(_COUNTER)
        self.pid = f"mt{n:02d}"
        self.name = f"MT{n:02d}"
        projects.create({"project_id": self.pid, "project_name": self.name})
        self.bare = tmp / f"{self.pid}.git"
        self.seed = tmp / f"{self.pid}-seed"
        _git(["init", "--bare", "-b", "main", str(self.bare)])
        _git(["init", "-b", "main", str(self.seed)])
        (self.seed / "README.md").write_text("hello\n", encoding="utf-8")
        (self.seed / "shared.txt").write_text("line1\n", encoding="utf-8")
        _git(["add", "-A"], cwd=self.seed)
        _git(["commit", "-m", "init"], cwd=self.seed)
        _git(["remote", "add", "origin", str(self.bare)], cwd=self.seed)
        _git(["push", "origin", "main"], cwd=self.seed)
        _git(["push", "origin", "main:develop"], cwd=self.seed)
        _git(["push", "origin", "main:release"], cwd=self.seed)
        svc.save_config(self.pid, {
            "repo_url": self.bare.as_uri(), "provider": "generic",
            "base_branch": "main", "default_finalize_action": "merge", "enabled": True,
        })

    # ── helpers ─────────────────────────────────────────────────────────────
    def group(self, seq: int) -> str:
        from modules.flow_gate.services import git_service as svc
        gid = f"{self.pid}.default.{seq:04d}"
        assert svc.ensure_worktree(self.pid, "default", gid) == "ok"
        return gid

    def ready(self, gid: str) -> None:
        from modules.flow_gate.db import git_integration as db_git
        _seed_wf_done_root(gid, self.pid)
        db_git.set_status(gid, "awaiting_choice")

    def wt(self, gid: str) -> Path:
        from modules.flow_gate.storage.paths import src_root
        return src_root(self.name, gid.replace(".", "_"))

    @property
    def base(self) -> Path:
        from modules.flow_gate.storage.paths import src_root
        return src_root(self.name, "main")

    def local_branch(self, name: str) -> None:
        from modules.flow_gate.services import git_service as svc
        svc.create_branch(self.pid, name, "main")

    def origin_commit(self, branch: str, path: str, content: str) -> None:
        _git(["fetch", "origin"], cwd=self.seed)
        _git(["checkout", "-B", branch, f"origin/{branch}"], cwd=self.seed)
        (self.seed / path).write_text(content, encoding="utf-8")
        _git(["add", "-A"], cwd=self.seed)
        _git(["commit", "-m", f"{branch} change"], cwd=self.seed)
        _git(["push", "origin", branch], cwd=self.seed)
        _git(["checkout", "main"], cwd=self.seed)

    def origin_files(self, branch: str) -> list[str]:
        return _git(["ls-tree", "-r", "--name-only", branch], cwd=self.bare).split()

    def origin_show(self, branch: str, path: str) -> str:
        return _git(["show", f"{branch}:{path}"], cwd=self.bare)

    def base_head_branch(self) -> str:
        return _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=self.base).strip()

    def base_sha(self) -> str:
        return _git(["rev-parse", "HEAD"], cwd=self.base).strip()

    def base_clean(self) -> bool:
        return _git(["status", "--porcelain"], cwd=self.base).strip() == ""

    def workspace(self, branch: str) -> Path:
        from modules.flow_gate.services.git import merge_target
        return merge_target.workspace_dir(self.pid, branch)


def _seed_wf_done_root(group_id: str, project_id: str) -> None:
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups

    if db_groups.get_by_id(group_id) is None:
        db_groups.create({
            "group_id": group_id, "project_id": project_id,
            "module": "default", "title": "t0012",
        })
    doc_id = f"{group_id}.0001-R"
    if db_docs.get_by_id(doc_id) is None:
        db_docs.create({
            "doc_id": doc_id, "project_id": project_id, "module": "default",
            "group_id": group_id, "type_code": "R", "seq": 1, "title": "root",
            "file_path": f"documents/{group_id}/0001-R.md",
        })
    db_docs.update(doc_id, {"doc_review_status": "wf_done"})


@pytest.fixture
def proj(tmp_path, monkeypatch):
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


def _conflict_on(p: Proj, gid: str, branch: str) -> dict:
    """Group edits shared.txt while ``branch`` on origin edits the same line."""
    from modules.flow_gate.services import git_service as svc
    (p.wt(gid) / "shared.txt").write_text(f"{gid} version\n", encoding="utf-8")
    p.origin_commit(branch, "shared.txt", f"{branch} version\n")
    p.ready(gid)
    out = svc.finalize(gid, "merge", target_branch=None if branch == "main" else branch)
    assert out["result"]["status"] == "conflict", out
    return out["result"]


# ── Path rules ───────────────────────────────────────────────────────────────

def test_workspace_path_is_deterministic_safe_and_collision_free(storage_dir):
    from modules.flow_gate.services.git import merge_target as mt

    a = mt.workspace_dir("flowgate", "feature/a")
    assert a == mt.workspace_dir("flowgate", "feature/a")            # deterministic
    assert mt.workspace_key("feature/a") != mt.workspace_key("feature_a")   # no fold collision
    assert mt.workspace_key("a/b") != mt.workspace_key("a_b")
    root = (storage_dir / mt.WORKSPACES_DIR).resolve()
    for name in ("../../etc", "..", "한글/브랜치", "x" * 300, ".hidden", "a\\b"):
        key = mt.workspace_key(name)
        assert "/" not in key and "\\" not in key and not key.startswith(".")
        assert root in mt.workspace_dir("flowgate", name).resolve().parents
    # under {storage}/git_merge_targets — never inside {storage}/src (source explorer)
    assert mt.workspaces_root() == storage_dir / mt.WORKSPACES_DIR
    assert (storage_dir / "src").resolve() not in a.resolve().parents


# ── A ────────────────────────────────────────────────────────────────────────

@needs_git
def test_A_base_finalize_without_target_is_unchanged_and_records_attempt(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    (proj.wt(gid) / "work.txt").write_text("work\n", encoding="utf-8")
    proj.ready(gid)
    out = svc.finalize(gid, "merge")
    assert out["result"]["status"] == "merged"
    assert out["result"]["pushed"] is True
    assert out["result"]["merge_id"] is None                 # response contract unchanged
    assert out["result"]["target_branch"] == "main"
    assert "work.txt" in proj.origin_files("main")
    state = db_git.get_state(gid)
    assert state["status"] == "merged" and state["merge_id"] is not None
    session = _session(state["merge_id"])
    ctx = db_git.session_context(session)
    assert session["status"] == "done"
    assert ctx["attempt_state"] == "completed"
    assert ctx["merge_target"]["branch"] == "main"
    assert ctx["merge_target"]["is_project_base"] is True
    view = svc.get_finalize_state(gid)["state"]
    assert view["merge_id"] is None                            # still only for conflicts
    assert view["finalize_target"]["target_branch"] == "main"
    assert not proj.workspace("main").exists()


# ── B ────────────────────────────────────────────────────────────────────────

@needs_git
def test_B_non_base_clean_finalize_uses_managed_workspace(proj, monkeypatch):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt

    gid = proj.group(1)
    proj.local_branch("develop")
    before = proj.base_sha()
    (proj.wt(gid) / "feature.txt").write_text("feature\n", encoding="utf-8")
    proj.ready(gid)

    seen = {}
    real_prepare = mt.prepare_workspace

    def spy(ctx, **kwargs):
        real_prepare(ctx, **kwargs)
        seen["root"] = ctx.root
        seen["exists"] = ctx.root.is_dir()
        seen["base_branch_during"] = proj.base_head_branch()

    monkeypatch.setattr(mt, "prepare_workspace", spy)
    out = svc.finalize(gid, "merge", target_branch="develop")
    assert out["result"]["status"] == "merged"
    assert out["result"]["target_branch"] == "develop"
    assert seen["root"] == proj.workspace("develop") / "tree" and seen["exists"]
    assert seen["base_branch_during"] == "main"
    # the shared base checkout was never switched nor moved
    assert proj.base_head_branch() == "main"
    assert proj.base_sha() == before
    assert not (proj.base / "feature.txt").exists()
    assert "feature.txt" in _git(["ls-tree", "-r", "--name-only", "develop"], cwd=proj.base).split()
    assert "feature.txt" not in _git(["ls-tree", "-r", "--name-only", "main"], cwd=proj.base).split()
    assert "feature.txt" in proj.origin_files("develop")
    assert "feature.txt" not in proj.origin_files("main")
    state = db_git.get_state(gid)
    ctx = _ctx(state["merge_id"])
    assert ctx["attempt_state"] == "completed"
    assert ctx["merge_target"]["branch"] == "develop"
    assert ctx["merge_target"]["is_project_base"] is False
    assert not proj.workspace("develop").exists()             # own workspace released
    # slot teardown: the work branch is contained in develop (not in base) and is
    # still removed, because the completed attempt proves where it landed
    assert _git(["show-ref", "--verify", f"refs/heads/{gid.replace('.', '_')}"],
                cwd=proj.base, check=False) == ""
    assert "_default_0001" not in _git(["branch", "--list"], cwd=proj.base)
    # restart: the real target is recovered from the ledger → attempt record
    svc.startup_recovery()
    done = mt.completed_target_of_state(db_git.get_state(gid))
    assert done.target_branch == "develop" and not done.is_project_base
    assert svc.get_finalize_state(gid)["state"]["finalize_target"]["target_branch"] == "develop"

    # T0014 connected edge: the result just pushed by group finalize is then
    # promoted through the real ordinary-branch merge service into main.
    promoted = svc.merge_branches(proj.pid, "develop", "main")
    assert promoted["pushed"] is True and promoted["workspace_cleaned"] is True
    assert "feature.txt" in proj.origin_files("main")
    assert not proj.workspace("main").exists()
    assert proj.base_head_branch() == "main"


# ── C ────────────────────────────────────────────────────────────────────────

@needs_git
def test_C_non_base_conflict_resolve_review_approve_stays_on_target(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    proj.local_branch("develop")
    base_before = proj.base_sha()
    base_shared = (proj.base / "shared.txt").read_text(encoding="utf-8")
    result = _conflict_on(proj, gid, "develop")
    merge_id = result["merge_id"]
    assert result["conflict_files"] == ["shared.txt"]
    assert result["target_branch"] == "develop"
    ws = proj.workspace("develop") / "tree"
    assert svc.resolve_conflict_src_root(gid, merge_id) == ws
    listed = svc.list_conflicts(gid, merge_id)
    assert listed["target_branch"] == "develop"
    assert "<<<<<<<" in listed["files"][0]["content"]
    assert "<<<<<<<" in (ws / "shared.txt").read_text(encoding="utf-8")
    # the base checkout is untouched and not held
    assert proj.base_head_branch() == "main" and proj.base_sha() == base_before
    assert (proj.base / "shared.txt").read_text(encoding="utf-8") == base_shared
    assert proj.base_clean() and svc.base_merge_in_progress(proj.pid) is None
    svc.guard_base_free(proj.pid)                             # does not raise
    assert svc.open_merge_session_of_project(proj.pid) is None
    view = svc.get_finalize_state(gid)["state"]
    assert view["status"] == "conflict" and view["merge_id"] == merge_id
    assert view["finalize_target"]["target_branch"] == "develop"

    out = svc.resolve_conflicts(gid, merge_id, [{
        "path": "shared.txt", "content": f"{gid} version\ndevelop version\n",
    }], True)
    assert out["result"]["status"] == "resolved_pending_review"
    review = svc.get_merge_review(gid, merge_id)["result"]
    assert (proj.base / "shared.txt").read_text(encoding="utf-8") == base_shared
    approved = svc.approve_merge_review(
        gid, merge_id, attempt_id=str(uuid.uuid4()),
        review_fingerprint=review["review_fingerprint"], authority="human",
    )
    assert approved["result"]["status"] == "merged"
    assert approved["result"]["pushed"] is True
    assert proj.origin_show("develop", "shared.txt") == f"{gid} version\ndevelop version\n"
    assert proj.origin_show("main", "shared.txt") == "line1\n"
    assert proj.base_head_branch() == "main" and proj.base_sha() == base_before
    session = _session(merge_id)
    assert session["status"] == "done"
    assert _ctx(merge_id)["attempt_state"] == "completed"
    state = db_git.get_state(gid)
    assert state["status"] == "merged" and state["merge_id"] == merge_id
    assert not proj.workspace("develop").exists()


# ── D ────────────────────────────────────────────────────────────────────────

@needs_git
def test_D_reject_and_re_review_keep_the_target_root(proj):
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    proj.local_branch("develop")
    base_before = proj.base_sha()
    merge_id = _conflict_on(proj, gid, "develop")["merge_id"]
    ws = proj.workspace("develop") / "tree"
    original = svc.list_conflicts(gid, merge_id)["files"][0]["content"]
    svc.resolve_conflicts(gid, merge_id, [{
        "path": "shared.txt", "content": f"{gid} version\ndevelop version\n",
    }], True)
    rejected = svc.reject_merge_review(
        gid, merge_id, reason="다시 확인해 주세요 — 충분히 긴 사유입니다.",
        provider_id="prov", provider_pinned=True, start_run=lambda message: "aiv_fake",
    )
    assert rejected["result"]["status"] == "returned_to_resolver"
    assert svc.resolve_conflict_src_root(gid, merge_id) == ws
    assert svc.list_conflicts(gid, merge_id)["files"][0]["content"] == original
    assert (ws / "shared.txt").read_text(encoding="utf-8") == original
    assert proj.base_sha() == base_before and proj.base_clean()
    assert _ctx(merge_id)["merge_target"]["branch"] == "develop"

    second = f"develop version\n{gid} version\n"
    out = svc.resolve_conflicts(gid, merge_id, [{"path": "shared.txt", "content": second}], True)
    review = svc.get_merge_review(gid, merge_id)["result"]
    assert review["review_state"] == "resolved_pending_review"
    approved = svc.approve_merge_review(
        gid, merge_id, attempt_id=str(uuid.uuid4()),
        review_fingerprint=out["result"]["review_fingerprint"], authority="human",
    )
    assert approved["result"]["status"] == "merged"
    assert proj.origin_show("develop", "shared.txt") == second
    assert proj.origin_show("main", "shared.txt") == "line1\n"


# ── E ────────────────────────────────────────────────────────────────────────

@needs_git
def test_E1_startup_recovery_refinds_the_deterministic_workspace(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    proj.local_branch("develop")
    merge_id = _conflict_on(proj, gid, "develop")["merge_id"]
    svc.startup_recovery()
    assert _session(merge_id)["status"] == "open"
    state = db_git.get_state(gid)
    assert state["status"] == "conflict" and state["merge_id"] == merge_id
    ws = proj.workspace("develop") / "tree"
    assert svc.resolve_conflict_src_root(gid, merge_id) == ws
    assert "<<<<<<<" in svc.list_conflicts(gid, merge_id)["files"][0]["content"]
    # periodic sweep inside the TTL also leaves it alone
    svc.merge_session_sweep()
    assert _session(merge_id)["status"] == "open"
    svc.abort_merge(gid, merge_id)
    assert _ctx(merge_id)["attempt_state"] == "aborted"
    assert db_git.get_state(gid)["status"] == "waiting"
    assert not proj.workspace("develop").exists()


def _open_attempt(p: Proj, gid: str, branch: str, holder: str):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services.git import merge_target as mt

    cfg = db_git.get_config(p.pid)
    target = mt.plan_finalize_target(gid, p.pid, cfg, None if branch == "main" else branch)
    attempt = mt.open_attempt(gid, target, "merge", holder, holder.split(":", 1)[1])
    mt.prepare_workspace(attempt)
    return attempt


@needs_git
def test_E2_startup_recovery_closes_an_interrupted_attempt(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    proj.local_branch("develop")
    proj.ready(gid)
    attempt = _open_attempt(proj, gid, "develop", "op:crashed")
    db_git.set_status(gid, "merging")
    assert db_git.try_acquire_lock(proj.pid, "op:crashed")     # stale pre-restart lock row
    assert proj.workspace("develop").exists()
    svc.startup_recovery()
    session = _session(attempt.merge_id)
    assert session["status"] == "aborted"
    assert _ctx(attempt.merge_id)["attempt_state"] == "interrupted"
    assert db_git.get_state(gid)["status"] == "waiting"
    assert db_git.get_lock(proj.pid) is None
    assert not proj.workspace("develop").exists()


@needs_git
def test_E3_live_in_progress_attempt_is_not_a_conflict_and_not_swept(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt

    gid = proj.group(1)
    gid2 = proj.group(2)          # provisioned before the "live runner" holds the lock
    proj.local_branch("develop")
    proj.ready(gid)
    proj.ready(gid2)
    attempt = _open_attempt(proj, gid, "develop", "op:live")
    base_attempt = _open_attempt(proj, gid2, "main", "op:live")
    assert db_git.try_acquire_lock(proj.pid, "op:live")
    db_git.set_status(gid, "merging")
    session = _session(attempt.merge_id)
    assert mt.attempt_phase(session) == mt.PHASE_IN_PROGRESS
    svc.merge_session_sweep()
    assert _session(attempt.merge_id)["status"] == "open"
    assert proj.workspace("develop").exists()
    assert svc.get_finalize_state(gid)["state"]["status"] == "merging"
    assert svc.get_finalize_state(gid)["state"]["merge_id"] is None

    # a BASE attempt in flight does not report the base checkout as conflicted either
    assert mt.attempt_phase(_session(base_attempt.merge_id)) == mt.PHASE_IN_PROGRESS
    assert _session(base_attempt.merge_id)["status"] == "open"
    assert svc.open_merge_session_of_project(proj.pid) is None
    svc.guard_base_free(proj.pid)

    # runner gone → the next sweep closes both as interrupted
    db_git.release_lock(proj.pid, "op:live")
    svc.merge_session_sweep()
    assert _ctx(attempt.merge_id)["attempt_state"] == "interrupted"
    assert _ctx(base_attempt.merge_id)["attempt_state"] == "interrupted"
    assert not proj.workspace("develop").exists()
    assert db_git.get_state(gid)["status"] == "waiting"


# ── F / G ────────────────────────────────────────────────────────────────────

@needs_git
def test_F_workspace_has_a_single_owner(proj, monkeypatch):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt

    gid_a = proj.group(1)
    gid_b = proj.group(2)
    proj.local_branch("develop")
    merge_a = _conflict_on(proj, gid_a, "develop")["merge_id"]
    ws = proj.workspace("develop")
    marker_before = (ws / "owner.json").read_text(encoding="utf-8")
    conflict_before = (ws / "tree" / "shared.txt").read_text(encoding="utf-8")

    (proj.wt(gid_b) / "b.txt").write_text("b\n", encoding="utf-8")
    proj.ready(gid_b)
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid_b, "merge", target_branch="develop")
    assert caught.value.code == "merge_target_busy"
    assert caught.value.details["merge_id"] == merge_a

    # 2nd check (post-lock) is the real guard: let the pre-lock check pass once.
    calls = {"n": 0}
    real = mt.raise_if_workspace_unavailable

    def racing(project_id, branch):
        calls["n"] += 1
        if calls["n"] == 1:
            return "free"
        return real(project_id, branch)

    monkeypatch.setattr(mt, "raise_if_workspace_unavailable", racing)
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid_b, "merge", target_branch="develop")
    assert caught.value.code == "merge_target_busy"
    assert calls["n"] == 2
    assert (ws / "owner.json").read_text(encoding="utf-8") == marker_before
    assert (ws / "tree" / "shared.txt").read_text(encoding="utf-8") == conflict_before
    assert _session(merge_a)["status"] == "open"
    from modules.flow_gate.db import git_integration as db_git
    assert db_git.get_open_session_by_group(gid_b) is None     # no attempt was written


@needs_git
def test_G_owner_marker_mismatch_fails_closed_and_is_never_cleaned(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid_a = proj.group(1)
    gid_b = proj.group(2)
    proj.local_branch("develop")
    merge_a = _conflict_on(proj, gid_a, "develop")["merge_id"]
    ws = proj.workspace("develop")
    marker = json.loads((ws / "owner.json").read_text(encoding="utf-8"))
    marker["owner"] = "someone-else"
    (ws / "owner.json").write_text(json.dumps(marker), encoding="utf-8")

    (proj.wt(gid_b) / "b.txt").write_text("b\n", encoding="utf-8")
    proj.ready(gid_b)
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid_b, "merge", target_branch="develop")
    assert caught.value.code == "merge_target_owner_mismatch"

    # abort of A is refused BEFORE git runs: the workspace is not A's by marker, so
    # A's row, the merge state and the workspace all stay (see the next test)
    with pytest.raises(svc.GitServiceError) as caught:
        svc.abort_merge(gid_a, merge_a)
    assert caught.value.code == "merge_target_owner_mismatch"
    assert _session(merge_a)["status"] == "open"
    assert (ws / "tree").is_dir() and (ws / "owner.json").exists()
    svc.merge_session_sweep()
    assert (ws / "tree").is_dir()
    # a marker with no matching open attempt still fails closed (no takeover)
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid_b, "merge", target_branch="develop")
    assert caught.value.code == "merge_target_owner_mismatch"
    assert db_git.get_open_session_by_group(gid_b) is None


def _merge_head(tree: Path) -> str:
    return _git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=tree, check=False).strip()


@needs_git
def test_G_mismatch_leaves_merge_state_row_and_workspace_on_every_path(proj, monkeypatch):
    """§9.1/§9.3: abort, TTL, orphan/startup recovery check the owner BEFORE git runs."""
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import cleanup

    gid = proj.group(1)
    proj.local_branch("develop")
    merge_id = _conflict_on(proj, gid, "develop")["merge_id"]
    ws = proj.workspace("develop")
    tree = ws / "tree"
    head_before = _merge_head(tree)
    content_before = (tree / "shared.txt").read_text(encoding="utf-8")
    status_before = _git(["status", "--porcelain"], cwd=tree)
    assert head_before and "<<<<<<<" in content_before
    marker = json.loads((ws / "owner.json").read_text(encoding="utf-8"))
    marker["owner"] = "someone-else"
    (ws / "owner.json").write_text(json.dumps(marker), encoding="utf-8")

    def untouched():
        assert _merge_head(tree) == head_before
        assert (tree / "shared.txt").read_text(encoding="utf-8") == content_before
        assert _git(["status", "--porcelain"], cwd=tree) == status_before
        assert _session(merge_id)["status"] == "open"
        assert _ctx(merge_id)["attempt_state"] == "conflict"
        assert db_git.get_state(gid)["status"] == "conflict"
        assert (ws / "owner.json").exists()

    with pytest.raises(svc.GitServiceError) as caught:
        svc.abort_merge(gid, merge_id)
    assert caught.value.code == "merge_target_owner_mismatch"
    untouched()
    monkeypatch.setattr(cleanup, "_ttl_expired", lambda _last: True)
    svc.merge_session_sweep()
    untouched()
    svc.startup_recovery()
    untouched()
    # the TTL reclaimer itself re-checks under its own lock
    cleanup._auto_abort_session(_session(merge_id), proj.pid, tree, "ttl_expired")
    untouched()
    # orphan close refuses too (e.g. MERGE_HEAD read as gone)
    svc._close_orphan(_session(merge_id), proj.pid)
    untouched()


@needs_git
def test_G_interrupted_attempt_never_aborts_another_owners_merge(proj):
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    proj.local_branch("develop")
    proj.ready(gid)
    attempt = _open_attempt(proj, gid, "develop", "op:crashed")
    tree = proj.workspace("develop") / "tree"
    # someone else's in-flight merge state in the tree, marker naming them
    wb = gid.replace(".", "_")
    (proj.wt(gid) / "x.txt").write_text("x\n", encoding="utf-8")
    _git(["add", "-A"], cwd=proj.wt(gid))
    _git(["commit", "-m", "x"], cwd=proj.wt(gid))
    _git(["merge", "--no-commit", "--no-ff", wb], cwd=tree)
    marker_path = proj.workspace("develop") / "owner.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["owner"] = "someone-else"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    head_before = _merge_head(tree)
    svc.startup_recovery()
    assert _session(attempt.merge_id)["status"] == "open"
    assert _merge_head(tree) == head_before
    assert (tree / "x.txt").exists()


class _Crash(BaseException):
    """Process death: nothing after the raise point runs (not even fail_attempt)."""


def _crash_finalize(proj, monkeypatch, gid: str, action: str, *, where: str):
    from modules.flow_gate.services import git_service as svc

    with pytest.MonkeyPatch.context() as crash:
        _arm_crash(crash, where)
        with pytest.raises(_Crash):
            svc.finalize(gid, action, target_branch="develop")


def _arm_crash(monkeypatch, where: str) -> None:
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt

    monkeypatch.setattr(mt, "fail_attempt", lambda *a, **k: None)
    armed = {"on": True}
    if where == "before_push":
        real_run = svc._run_git

        def run(args, *a, **k):
            if armed["on"] and args and args[0] == "push":
                armed["on"] = False
                raise _Crash()
            return real_run(args, *a, **k)
        monkeypatch.setattr(svc, "_run_git", run)
    elif where == "before_ledger":
        real_status = svc._set_status

        def status(group_id, value, **kw):
            if armed["on"] and value == "merged":
                armed["on"] = False
                raise _Crash()
            return real_status(group_id, value, **kw)
        monkeypatch.setattr(svc, "_set_status", status)
    elif where == "before_close":
        real_complete = mt.complete_attempt

        def complete(*a, **k):
            if armed["on"]:
                armed["on"] = False
                raise _Crash()
            return real_complete(*a, **k)
        monkeypatch.setattr(mt, "complete_attempt", complete)


@needs_git
@pytest.mark.parametrize("action,where,recover", [
    ("merge", "before_ledger", "startup"),
    ("merge", "before_close", "startup"),
    ("merge_only", "before_ledger", "sweep"),
])
def test_E4_crash_after_the_merge_landed_is_reconciled_to_completed(
    proj, monkeypatch, action, where, recover,
):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    proj.local_branch("develop")
    (proj.wt(gid) / "feature.txt").write_text("feature\n", encoding="utf-8")
    proj.ready(gid)
    _crash_finalize(proj, monkeypatch, gid, action, where=where)
    session = db_git.get_open_session_by_group(gid)
    assert session is not None                               # the crash left it open
    merge_id = int(session["merge_id"])
    landed = _git(["rev-parse", "develop"], cwd=proj.base).strip()
    assert "feature.txt" in _git(["ls-tree", "-r", "--name-only", "develop"], cwd=proj.base).split()
    if recover == "startup":
        svc.startup_recovery()
    else:
        svc.merge_session_sweep()                            # runner gone: lock released
    ctx = _ctx(merge_id)
    assert _session(merge_id)["status"] == "done"
    assert ctx["attempt_state"] == "completed"               # never "interrupted"
    assert ctx["attempt_result"]["pushed"] is (action == "merge")
    assert ctx["attempt_result"]["recovered"] is True
    state = db_git.get_state(gid)
    assert state["status"] == "merged" and state["merge_id"] == merge_id
    assert _git(["rev-parse", "develop"], cwd=proj.base).strip() == landed   # not undone
    assert ("feature.txt" in proj.origin_files("develop")) is (action == "merge")
    assert not proj.workspace("develop").exists()
    assert proj.base_head_branch() == "main"


@needs_git
def test_E4_crash_before_the_push_rolls_the_local_merge_back(proj, monkeypatch):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    proj.local_branch("develop")
    (proj.wt(gid) / "feature.txt").write_text("feature\n", encoding="utf-8")
    proj.ready(gid)
    _crash_finalize(proj, monkeypatch, gid, "merge", where="before_push")
    session = db_git.get_open_session_by_group(gid)
    merge_id = int(session["merge_id"])
    inputs = _ctx(merge_id)["merge_inputs"]
    assert _git(["rev-parse", "develop"], cwd=proj.base).strip() != inputs["pre_head"]
    assert db_git.get_state(gid)["status"] == "merging"
    svc.startup_recovery()
    assert _ctx(merge_id)["attempt_state"] == "interrupted"
    assert db_git.get_state(gid)["status"] == "waiting"
    # the unpushed merge commit is undone (never reported as a result)
    assert _git(["rev-parse", "develop"], cwd=proj.base).strip() == inputs["pre_head"]
    assert "feature.txt" not in proj.origin_files("develop")
    assert not proj.workspace("develop").exists()
    # and the group can finalize again normally
    out = svc.finalize(gid, "merge", target_branch="develop")
    assert out["result"]["status"] == "merged"
    assert "feature.txt" in proj.origin_files("develop")


@needs_git
def test_E5_completed_review_left_open_is_finished_on_restart(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    proj.local_branch("develop")
    merge_id = _conflict_on(proj, gid, "develop")["merge_id"]
    tree = proj.workspace("develop") / "tree"
    _git(["checkout", "--theirs", "shared.txt"], cwd=tree)
    _git(["add", "-A"], cwd=tree)
    _git(["-c", "user.name=T", "-c", "user.email=t@t", "commit", "--no-edit"], cwd=tree)
    commit = _git(["rev-parse", "HEAD"], cwd=tree).strip()
    ctx = _ctx(merge_id)
    ctx.update({"review_state": "completed", "apply_phase": "completed", "merge_commit": commit})
    db_git.set_session_context(merge_id, ctx)
    # the crash hit after the ledger write, before the row was closed
    db_git.set_status(gid, "merged", merge_id=merge_id, merge_commit=commit[:7])
    svc.startup_recovery()
    assert _session(merge_id)["status"] == "done"
    assert _ctx(merge_id)["attempt_state"] == "completed"
    state = db_git.get_state(gid)
    assert state["status"] == "merged" and state["merge_id"] == merge_id
    assert not proj.workspace("develop").exists()


# ── H ────────────────────────────────────────────────────────────────────────

@needs_git
def test_H1_H2_retarget_allowed_without_open_attempt_and_after_failure(proj, tmp_path):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt

    gid = proj.group(1)
    proj.local_branch("develop")
    proj.local_branch("release")
    (proj.wt(gid) / "h.txt").write_text("h\n", encoding="utf-8")
    proj.ready(gid)
    cfg = db_git.get_config(proj.pid)
    # H1: nothing open → any allowed target
    assert mt.plan_finalize_target(gid, proj.pid, cfg, "develop").target_branch == "develop"
    assert mt.plan_finalize_target(gid, proj.pid, cfg, "release").target_branch == "release"

    # H2: an attempt on develop fails (develop is checked out elsewhere) …
    blocker = tmp_path / "blocker"
    _git(["worktree", "add", str(blocker), "develop"], cwd=proj.base)
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid, "merge", target_branch="develop")
    assert caught.value.code == "merge_target_checkout_failed"
    failed = [
        s for s in db_git.list_open_sessions() if s["group_id"] == gid
    ]
    assert failed == []
    state = db_git.get_state(gid)
    assert state["status"] == "awaiting_choice"
    from modules.flow_gate.db.connection import get_store
    row = get_store()._fetch_one(
        "SELECT * FROM git_merge_session WHERE group_id = ? ORDER BY merge_id DESC", [gid],
    )
    assert row["status"] == "aborted"
    assert db_git.session_context(row)["attempt_state"] == "failed"
    assert db_git.session_context(row)["merge_target"]["branch"] == "develop"
    assert not proj.workspace("develop").exists()
    _git(["worktree", "remove", "--force", str(blocker)], cwd=proj.base)
    # … and the retry may pick another target
    out = svc.finalize(gid, "merge", target_branch="release")
    assert out["result"]["status"] == "merged"
    assert "h.txt" in proj.origin_files("release")


@needs_git
def test_H3_H4_H5_open_attempt_pins_its_target(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt

    gid = proj.group(1)
    proj.local_branch("develop")
    proj.local_branch("release")
    merge_id = _conflict_on(proj, gid, "develop")["merge_id"]
    started = _ctx(merge_id)["merge_target"]["started_at"]

    # H3: open conflict + a different explicit target → refused, target unchanged
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid, "merge", target_branch="release")
    assert caught.value.code == "merge_target_locked"
    assert caught.value.details["target_branch"] == "develop"
    assert caught.value.details["started_at"] == started
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid, "merge", target_branch="main")
    assert caught.value.code == "merge_target_locked"
    with pytest.raises(svc.GitServiceError) as caught:
        svc.precheck_approve_git_target(gid, "merge", "release")
    assert caught.value.code == "merge_target_locked"

    # H5: omitted target is not a retarget — it continues with the pinned one
    cfg = db_git.get_config(proj.pid)
    assert mt.plan_finalize_target(gid, proj.pid, cfg, None).target_branch == "develop"
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid, "merge")
    assert caught.value.code == "invalid_state"
    assert caught.value.details["target_branch"] == "develop"

    # H4: resolved → open review: still pinned
    resolved = f"{gid} version\ndevelop version\n"
    out = svc.resolve_conflicts(gid, merge_id, [{"path": "shared.txt", "content": resolved}], True)
    assert out["result"]["status"] == "resolved_pending_review"
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid, "merge", target_branch="release")
    assert caught.value.code == "merge_target_locked"
    assert _ctx(merge_id)["merge_target"]["branch"] == "develop"
    approved = svc.approve_merge_review(
        gid, merge_id, attempt_id=str(uuid.uuid4()),
        review_fingerprint=out["result"]["review_fingerprint"], authority="human",
    )
    assert approved["result"]["status"] == "merged"
    assert proj.origin_show("develop", "shared.txt") == resolved
    assert proj.origin_show("release", "shared.txt") == "line1\n"


# ── I ────────────────────────────────────────────────────────────────────────

@needs_git
def test_I_legacy_session_without_target_falls_back_to_base(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt

    gid = proj.group(1)
    merge_id = _conflict_on(proj, gid, "main")["merge_id"]
    ctx = _ctx(merge_id)
    for key in ("merge_target", "target_branch", "attempt_state"):
        ctx.pop(key, None)
    db_git.set_session_context(merge_id, ctx)          # = a row written before T0012

    resolved = mt.resolve_session_target(_session(merge_id))
    assert resolved.legacy and resolved.is_project_base and resolved.target_branch == "main"
    assert svc.resolve_conflict_src_root(gid, merge_id) == proj.base
    assert svc.open_merge_session_of_project(proj.pid)["merge_id"] == merge_id
    legacy = f"{gid} version\nmain version\n"
    out = svc.resolve_conflicts(gid, merge_id, [{"path": "shared.txt", "content": legacy}], True)
    review = svc.get_merge_review(gid, merge_id)["result"]
    approved = svc.approve_merge_review(
        gid, merge_id, attempt_id=str(uuid.uuid4()),
        review_fingerprint=review["review_fingerprint"], authority="human",
    )
    assert approved["result"]["status"] == "merged"
    assert proj.origin_show("main", "shared.txt") == legacy
    assert out["result"]["status"] == "resolved_pending_review"


# ── J / K ────────────────────────────────────────────────────────────────────

@needs_git
def test_J_non_base_unmerge_fails_closed_service_and_route(proj):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api.v1 import git_routes
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    proj.local_branch("develop")
    (proj.wt(gid) / "j.txt").write_text("j\n", encoding="utf-8")
    proj.ready(gid)
    out = svc.finalize(gid, "merge_only", target_branch="develop")
    assert out["result"]["status"] == "merged" and out["result"]["pushed"] is False
    sha = out["result"]["merge_commit"]
    base_before = proj.base_sha()
    develop_before = _git(["rev-parse", "develop"], cwd=proj.base).strip()
    with pytest.raises(svc.GitServiceError) as caught:
        svc.unmerge(gid, sha)
    assert caught.value.code == "unmerge_unsupported_target"
    assert caught.value.details["target_branch"] == "develop"

    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "admin", "is_admin": True}
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(f"/api/v1/groups/{gid}/git/unmerge", json={"merge_commit": sha})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "unmerge_unsupported_target"
    assert proj.base_sha() == base_before and proj.base_clean()
    assert _git(["rev-parse", "develop"], cwd=proj.base).strip() == develop_before
    # the base unmerge list never offers it
    status = svc.project_git_status(proj.pid)["status"]
    assert all(m.get("group_id") != gid for m in status["unpushed"]["merges"])


@needs_git
def test_K_base_unmerge_still_works(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    (proj.wt(gid) / "k.txt").write_text("k\n", encoding="utf-8")
    proj.ready(gid)
    before = proj.base_sha()
    out = svc.finalize(gid, "merge_only")
    assert out["result"]["status"] == "merged"
    assert db_git.get_state(gid)["merge_id"] is not None       # ledger points at attempt
    status = svc.project_git_status(proj.pid)["status"]
    merges = status["unpushed"]["merges"]
    assert merges and merges[0]["group_id"] == gid and merges[0]["can_unmerge"] is True
    result = svc.unmerge(gid, out["result"]["merge_commit"])
    assert result["result"]["unmerged"] is True
    assert proj.base_sha() == before
    assert db_git.get_state(gid)["status"] == "awaiting_choice"


# ── L ────────────────────────────────────────────────────────────────────────

@needs_git
def test_L_internal_slot_remote_only_and_missing_targets_are_refused(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt

    gid = proj.group(1)
    other = proj.group(2)
    other_branch = db_git.get_state(other)["branch"]
    own_branch = db_git.get_state(gid)["branch"]
    (proj.wt(gid) / "l.txt").write_text("l\n", encoding="utf-8")
    proj.ready(gid)

    for branch, owner in ((other_branch, other), (own_branch, gid)):
        with pytest.raises(svc.GitServiceError) as caught:
            svc.finalize(gid, "merge", target_branch=branch)
        assert caught.value.code == "merge_target_internal_slot"
        assert caught.value.details["connected_group_id"] == owner

    _git(["push", "origin", "main:remote-only"], cwd=proj.seed)
    _git(["fetch", "origin"], cwd=proj.base)
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid, "merge", target_branch="remote-only")
    assert caught.value.code == "merge_target_remote_only"
    with pytest.raises(svc.GitServiceError) as caught:
        svc.precheck_approve_git_target(gid, "merge", "remote-only")
    assert caught.value.code == "merge_target_remote_only"
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid, "merge", target_branch="nope")
    assert caught.value.code == "merge_target_not_found"
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid, "push", target_branch="develop")
    assert caught.value.code == "merge_target_requires_merge"
    assert db_git.get_open_session_by_group(gid) is None
    assert db_git.get_state(gid)["status"] == "awaiting_choice"

    # the slot decision is the ledger, not the name: an UNREGISTERED branch whose
    # name looks like a slot is an ordinary local branch
    lookalike = f"{proj.pid}_default_0999"
    proj.local_branch(lookalike)
    cfg = db_git.get_config(proj.pid)
    assert mt.plan_finalize_target(gid, proj.pid, cfg, lookalike).target_branch == lookalike


# ── M ────────────────────────────────────────────────────────────────────────

@needs_git
def test_M_base_only_paths_keep_the_project_base_meaning(proj, monkeypatch):
    from modules.flow_gate.services import git_service as svc

    # update_from_base's own `git merge` carries no FlowGate identity (unchanged
    # base-only code); give hosts without a global git identity one for this test.
    # (_run_git strips an ambient author on purpose → configure the project author.)
    for key, value in (("GIT_COMMITTER_NAME", "T"), ("GIT_COMMITTER_EMAIL", "t@t")):
        monkeypatch.setenv(key, value)
    svc.save_config(proj.pid, {
        "repo_url": proj.bare.as_uri(), "provider": "generic", "base_branch": "main",
        "default_finalize_action": "merge", "enabled": True,
        "author_name": "T", "author_email": "t@t",
    })

    gid1 = proj.group(1)
    gid2 = proj.group(2)
    proj.local_branch("develop")
    (proj.wt(gid1) / "only_develop.txt").write_text("d\n", encoding="utf-8")
    proj.ready(gid1)
    assert svc.finalize(gid1, "merge", target_branch="develop")["result"]["status"] == "merged"
    proj.origin_commit("main", "main_only.txt", "m\n")

    updated = svc.update_from_base(gid2)
    assert updated["result"]["status"] == "updated"
    assert (proj.wt(gid2) / "main_only.txt").exists()
    assert not (proj.wt(gid2) / "only_develop.txt").exists()
    state = svc.get_finalize_state(gid2)["state"]
    assert state["base_branch"] == "main"
    status = svc.project_git_status(proj.pid)["status"]
    assert status["base_branch"] == "main"
    assert proj.base_head_branch() == "main"
    assert svc.base_checkout_dirty_status(proj.pid)["dirty"] is False
    changes = svc.read_group_changes(proj.pid, gid2)
    paths = json.dumps(changes)
    assert "only_develop.txt" not in paths


# ── Carrier ──────────────────────────────────────────────────────────────────

@needs_git
def test_target_carrier_through_route_and_approval_seam(proj):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api import inbox_routes  # noqa: F401 — installs the archive seam
    from modules.flow_gate.api.v1 import git_routes
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.workflow.routers.workflow import DocumentBodyRequest

    assert getattr(svc, "_flowgate_git_archive_installed", False)
    assert DocumentBodyRequest(doc_id="x", git_action="merge", git_target_branch="develop").git_target_branch == "develop"

    gid1 = proj.group(1)
    gid2 = proj.group(2)
    proj.local_branch("develop")
    proj.local_branch("release")
    (proj.wt(gid1) / "r1.txt").write_text("1\n", encoding="utf-8")
    (proj.wt(gid2) / "r2.txt").write_text("2\n", encoding="utf-8")
    proj.ready(gid1)
    proj.ready(gid2)

    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "admin", "is_admin": True}
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        f"/api/v1/groups/{gid1}/git/finalize",
        json={"action": "merge", "git_target_branch": "develop"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["target_branch"] == "develop"
    assert "r1.txt" in proj.origin_files("develop")

    assert svc.precheck_approve_git_target(gid2, "merge", "release") == "release"
    outcome = svc.run_approve_git_action(gid2, "merge", "release")
    assert outcome["ok"] is True, outcome
    assert outcome["result"]["target_branch"] == "release"
    assert "r2.txt" in proj.origin_files("release")
    assert "r1.txt" not in proj.origin_files("main") and "r2.txt" not in proj.origin_files("main")


# ── N ────────────────────────────────────────────────────────────────────────
# flowgate.default.0594 T0016 §8.2 — a v0.2-style integration branch is not a
# one-shot target: several DIFFERENT groups finalize into it in turn, and each
# later merge builds on what the previous one just pushed, never touching main.

@needs_git
def test_N_sequential_groups_grow_the_same_integration_branch(proj):
    from modules.flow_gate.services import git_service as svc

    g_a = proj.group(1)
    proj.local_branch("integration-v2")
    (proj.wt(g_a) / "a.txt").write_text("a\n", encoding="utf-8")
    proj.ready(g_a)
    out_a = svc.finalize(g_a, "merge", target_branch="integration-v2")
    assert out_a["result"]["status"] == "merged"
    assert not proj.workspace("integration-v2").exists()

    g_b = proj.group(2)
    (proj.wt(g_b) / "b.txt").write_text("b\n", encoding="utf-8")
    proj.ready(g_b)
    out_b = svc.finalize(g_b, "merge", target_branch="integration-v2")
    assert out_b["result"]["status"] == "merged"
    assert not proj.workspace("integration-v2").exists()

    g_c = proj.group(3)
    (proj.wt(g_c) / "c.txt").write_text("c\n", encoding="utf-8")
    proj.ready(g_c)
    out_c = svc.finalize(g_c, "merge", target_branch="integration-v2")
    assert out_c["result"]["status"] == "merged"
    assert not proj.workspace("integration-v2").exists()

    files = proj.origin_files("integration-v2")
    assert "a.txt" in files and "b.txt" in files and "c.txt" in files
    main_files = proj.origin_files("main")
    assert "a.txt" not in main_files and "b.txt" not in main_files and "c.txt" not in main_files
    assert proj.base_head_branch() == "main"


# ── O ────────────────────────────────────────────────────────────────────────
# T0016 §2.2/§3.2 — the last non-base target a finalize actually merged into
# becomes this project's persisted suggestion for the NEXT finalize dialog
# (surfaced through list_branches), and the same setting can be changed or
# cleared directly (a distinct action from running an actual branch merge).

@needs_git
def test_O_default_merge_target_persists_and_is_settable_directly(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt

    assert (db_git.get_config(proj.pid) or {}).get("default_merge_target") is None

    gid = proj.group(1)
    assert svc.list_branches(proj.pid)["default_merge_target"] is None
    proj.local_branch("integration-v2")
    (proj.wt(gid) / "a.txt").write_text("a\n", encoding="utf-8")
    proj.ready(gid)
    out = svc.finalize(gid, "merge", target_branch="integration-v2")
    assert out["result"]["status"] == "merged"
    assert db_git.get_config(proj.pid)["default_merge_target"] == "integration-v2"
    assert svc.list_branches(proj.pid)["default_merge_target"] == "integration-v2"

    # T0016 §3.2's own action: set/clear the suggestion directly, no merge involved.
    proj.local_branch("integration-v3")
    result = mt.set_project_default_target(proj.pid, "integration-v3")
    assert result == {"ok": True, "default_merge_target": "integration-v3"}
    assert db_git.get_config(proj.pid)["default_merge_target"] == "integration-v3"

    # server-side allow-list still applies — never trusts the caller.
    with pytest.raises(svc.GitServiceError) as caught:
        mt.set_project_default_target(proj.pid, "does-not-exist")
    assert caught.value.code == "merge_target_not_found"

    cleared = mt.set_project_default_target(proj.pid, None)
    assert cleared == {"ok": True, "default_merge_target": None}
    assert db_git.get_config(proj.pid)["default_merge_target"] is None

    # T0016 §6.1 — the branch currently pinned as the merge-target suggestion
    # is itself undeletable, both through the guard and the catalog's can_delete
    # flag, until it is retargeted or cleared.
    mt.set_project_default_target(proj.pid, "integration-v3")
    assert svc.check_branch_delete(proj.pid, "integration-v3") == "branch_is_default_merge_target"
    row = next(b for b in svc.list_branches(proj.pid)["branches"] if b["name"] == "integration-v3")
    assert row["can_delete"] is False
    assert row["delete_blocked_reason"] == "branch_is_default_merge_target"
    with pytest.raises(svc.GitServiceError) as caught:
        svc.delete_branch(proj.pid, "integration-v3")
    assert caught.value.code == "branch_is_default_merge_target"
    assert svc.list_branches(proj.pid)["default_merge_target"] == "integration-v3"

    # clearing the suggestion first unblocks the delete.
    mt.set_project_default_target(proj.pid, None)
    svc.delete_branch(proj.pid, "integration-v3")
    assert svc.list_branches(proj.pid)["default_merge_target"] is None

    # defense in depth: even if a dangling suggestion occurred by some other
    # path (e.g. the branch ref removed outside this API), the catalog must
    # never surface it as the current target.
    proj.local_branch("integration-v4")
    mt.set_project_default_target(proj.pid, "integration-v4")
    _git(["branch", "-D", "integration-v4"], cwd=proj.base)
    assert svc.list_branches(proj.pid)["default_merge_target"] is None


@needs_git
def test_P_default_merge_target_route(proj):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api.v1 import git_routes
    from modules.flow_gate.auth.middleware import get_current_user

    proj.group(1)
    proj.local_branch("integration-v2")
    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "admin", "is_admin": True}
    client = TestClient(app, raise_server_exceptions=False)

    response = client.put(
        f"/api/v1/projects/{proj.pid}/git/branches/default-target",
        json={"branch": "integration-v2"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["default_merge_target"] == "integration-v2"
    catalog = client.get(f"/api/v1/projects/{proj.pid}/git/branches")
    assert catalog.json()["default_merge_target"] == "integration-v2"

    bad = client.put(
        f"/api/v1/projects/{proj.pid}/git/branches/default-target",
        json={"branch": "does-not-exist"},
    )
    assert bad.status_code == 404, bad.text

    cleared = client.put(
        f"/api/v1/projects/{proj.pid}/git/branches/default-target", json={},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["default_merge_target"] is None
