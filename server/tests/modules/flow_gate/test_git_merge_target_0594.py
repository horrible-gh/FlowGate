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

from group_lock_stub import held_locks  # noqa: E402

_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not _GIT, reason="git binary unavailable")


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        # No commit here: a transaction that commits every statement cannot roll
        # anything back, and then an atomic unit of work (0555 D0005 INV-6: AC
        # approval + root completion + intent consume, all or nothing) silently
        # tests as "each write landed on its own". begin_transaction below owns
        # the commit/rollback.
        self._cur = self._conn.execute(sql, params or [])

    @property
    def cursor(self):
        """The live cursor, so `_execute_affected` can read a real rowcount.

        FlowGateStore._execute_affected reads the affected-row count off the
        transaction's cursor (that is the one portable place all three real
        adapters expose it). A mock that hides its cursor makes every CAS write
        raise "database driver did not expose affected row count" — a harness
        gap, not a product one (0555 T0008).
        """
        return self._cur

    def fetchone(self):
        row = self._cur.fetchone() if self._cur else None
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()] if self._cur else []


class _MockDB:
    def __init__(self, db_path: str):
        import threading
        self._mutex = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql, params=None):
        with self._mutex:
            self._conn.execute(sql, params or [])
            self._conn.commit()

    def fetch_one(self, sql, params=None):
        with self._mutex:
            row = self._conn.execute(sql, params or []).fetchone()
            return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        with self._mutex:
            return [dict(row) for row in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        # One connection backs this fixture. Serialize its complete transaction,
        # including rollback, so concurrent requests cannot consume each other's
        # cursors or commit a different thread's unit of work. Domain locks and
        # session claims still run through their real database CAS operations.
        with self._mutex:
            txn = _MockTxn(self._conn)
            try:
                yield txn
            except BaseException:
                self._conn.rollback()
                raise
            self._conn.commit()

    def close(self):
        with self._mutex:
            self._conn.close()


@pytest.fixture(scope="module")
def storage_dir(tmp_path_factory):
    tmp = str(tmp_path_factory.mktemp("fg-mt0594-storage"))
    previous = os.environ.get("FLOWGATE_STORAGE_DIR")
    os.environ["FLOWGATE_STORAGE_DIR"] = tmp
    yield Path(tmp)
    if previous is None:
        os.environ.pop("FLOWGATE_STORAGE_DIR", None)
    else:
        os.environ["FLOWGATE_STORAGE_DIR"] = previous
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="module", autouse=True)
def patch_store(storage_dir, migrated_sqlite_db):
    db_path = migrated_sqlite_db("merge-target.db")
    mock_db = _MockDB(db_path)
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

    def __init__(self, tmp: Path, git_repositories):
        from modules.flow_gate.db import projects
        from modules.flow_gate.services import git_service as svc

        n = next(_COUNTER)
        self.pid = f"mt{n:02d}"
        self.name = f"MT{n:02d}"
        projects.create({"project_id": self.pid, "project_name": self.name})
        self.bare, self.seed = git_repositories("merge-target-0594", tmp)
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
def proj(tmp_path, monkeypatch, git_repositories):
    from modules.flow_gate.services import git_service as svc
    monkeypatch.setattr(svc, "_sweep_daemon_started", True)   # never spawn the thread
    p = Proj(tmp_path, git_repositories)
    yield p
    from modules.flow_gate.services.git import merge_target as mt
    mt.settle_check_targets(30)          # flowgate.v02test.0020: no background candidate computation outlives a test
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



# ── A ────────────────────────────────────────────────────────────────────────



# ── B ────────────────────────────────────────────────────────────────────────



# ── C ────────────────────────────────────────────────────────────────────────



# ── D ────────────────────────────────────────────────────────────────────────



# ── E ────────────────────────────────────────────────────────────────────────



def _open_attempt(p: Proj, gid: str, branch: str, holder: str):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services.git import merge_target as mt

    cfg = db_git.get_config(p.pid)
    target = mt.plan_finalize_target(gid, p.pid, cfg, None if branch == "main" else branch)
    attempt = mt.open_attempt(gid, target, "merge", holder, holder.split(":", 1)[1])
    mt.prepare_workspace(attempt)
    return attempt




def _live_job(project_id: str, tag: str) -> str:
    """A branch_merge_publish job that has been created and not finished (a runner still has
    it): what an attempt names as its holder while its merge is in flight."""
    from modules.flow_gate.services.git import job_store

    created = job_store.create_or_get_job(
        "branch_merge_publish", project_id, {"branch_merge_attempt_id": f"e3-{tag}:0"},
        target_key="develop", payload={"source_branch": "x", "target_branch": "develop"},
        wake=False)
    assert created.ok, created
    return created.job["job_id"]


def _end_job(job_id: str, status: str = "failed") -> None:
    from modules.flow_gate.db.connection import get_store

    with get_store().transaction():
        get_store()._execute(
            "UPDATE operation_job SET status = ?, lease_owner = NULL, lease_token = NULL, "
            "lease_until = NULL WHERE job_id = ?", [status, job_id])




# ── F / G ────────────────────────────────────────────────────────────────────





def _merge_head(tree: Path) -> str:
    return _git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=tree, check=False).strip()






class _Crash(BaseException):
    """Process death: nothing after the raise point runs (not even fail_attempt)."""


def _crash_finalize(proj, monkeypatch, gid: str, action: str, *, where: str):
    from modules.flow_gate.services import git_service as svc

    with pytest.MonkeyPatch.context() as crash:
        _arm_crash(crash, where)
        with pytest.raises(_Crash):
            svc.finalize(gid, action, target_branch="develop")


def _recover_job_after_crash(gid: str) -> dict:
    """The process died inside the finalize job (0669 unit 9a): its lease lapses, the Sweeper
    reconciles it, and the next claim settles the attempt under the target locks. Returns the
    job after that claim."""
    from modules.flow_gate.db import operation_job as db_jobs
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_runner, job_sweeper

    job = db_jobs.jobs_of_group(gid, "base_publish", ("running",))[-1]
    with get_store().transaction():
        get_store()._execute("UPDATE operation_job SET lease_until = ? WHERE job_id = ?",
                             ["2000-01-01T00:00:00+00:00", job["job_id"]])
    assert job_sweeper.step_expired_leases(full=True) >= 1
    with get_store().transaction():
        get_store()._execute("UPDATE operation_job SET available_at = ? WHERE job_id = ?",
                             ["2000-01-01T00:00:00+00:00", job["job_id"]])
    claim = job_runner.try_claim(job["job_id"])
    assert claim is not None, db_jobs.get_job(job["job_id"])
    job_runner.execute(claim)
    return db_jobs.get_job(job["job_id"])


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








# ── H ────────────────────────────────────────────────────────────────────────





# ── I ────────────────────────────────────────────────────────────────────────



# ── J / K ────────────────────────────────────────────────────────────────────





# ── L ────────────────────────────────────────────────────────────────────────



# ── M ────────────────────────────────────────────────────────────────────────



# ── Carrier ──────────────────────────────────────────────────────────────────



# ── N ────────────────────────────────────────────────────────────────────────
# flowgate.default.0594 T0016 §8.2 — a v0.2-style integration branch is not a
# one-shot target: several DIFFERENT groups finalize into it in turn, and each
# later merge builds on what the previous one just pushed, never touching main.



# ── O ────────────────────────────────────────────────────────────────────────
# T0016 §2.2/§3.2 — the last non-base target a finalize actually merged into
# becomes this project's persisted suggestion for the NEXT finalize dialog
# (surfaced through list_branches), and the same setting can be changed or
# cleared directly (a distinct action from running an actual branch merge).






# ── flowgate.default.0683 T0004 — group finalize blocker / plan-time recovery ────





# ── flowgate.v02.0086 — base checkout off the project base branch (NR0003 §5.3) ──

def _merge_rows(gid: str) -> list[dict]:
    from modules.flow_gate.db.connection import get_store
    return get_store()._fetch_all("SELECT * FROM git_merge_session WHERE group_id = ?", [gid])


def _assert_off_branch(exc) -> None:
    assert (exc.status, exc.code) == (409, "base_checkout_branch_mismatch")
    assert exc.details["checked_out_branch"] == "develop"
    assert exc.details["base_branch"] == "main"
    assert "'develop'" in exc.message and "'main'" in exc.message








# ── flowgate.v02.0098 T#1: the check card's common merge target judgement (D0004 §3.2 / §7) ──────────────────────

def _check_view(p: Proj, gid: str, selected=None, refresh=True) -> dict:
    from modules.flow_gate.services.git import merge_target as mt
    return mt.check_target_view(p.pid, gid, selected, refresh=refresh)


def _judge(p: Proj, gid: str, name: str, **kw) -> dict:
    from modules.flow_gate.services.git import merge_target as mt
    return mt.judge_check_target(p.pid, gid, name, **kw)


def _refused(p: Proj, gid: str, name: str, **kw):
    from modules.flow_gate.services import git_service as svc
    with pytest.raises(svc.GitServiceError) as caught:
        _judge(p, gid, name, **kw)
    return caught.value


@pytest.fixture
def check_budget(monkeypatch):
    """What is judged, not how fast this host spawns Git (the 3 s budget has its own test below)."""
    from modules.flow_gate.services.git import merge_target as mt
    monkeypatch.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 60.0)
    mt.reset_check_targets()
    yield
    assert mt.settle_check_targets(60)
    mt.reset_check_targets()


def _feature_group(p: Proj, seq: int, name: str = "feature/v0.2.0") -> str:
    """origin and the base checkout get ``name`` = main + 1 commit (main lacks it); a new group pins it as work base."""
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.services import git_service as svc

    if not (p.base / ".git").exists():
        assert svc.provision_base(p.pid, "test")["status"] != "failed"
    _git(["fetch", "origin"], cwd=p.seed)
    _git(["checkout", "-B", name, "origin/main"], cwd=p.seed)
    (p.seed / "v02.txt").write_text("v02\n", encoding="utf-8")
    _git(["add", "-A"], cwd=p.seed)
    _git(["commit", "-m", "v02"], cwd=p.seed)
    _git(["push", "origin", name], cwd=p.seed)
    _git(["checkout", "main"], cwd=p.seed)
    _git(["fetch", "origin"], cwd=p.base)
    _git(["branch", name, f"origin/{name}"], cwd=p.base)
    gid = f"{p.pid}.default.{seq:04d}"
    db_groups.create({"group_id": gid, "project_id": p.pid, "module": "default", "title": "t0098",
                      "work_base_ref": name})
    assert svc.ensure_worktree(p.pid, "default", gid) == "ok"
    return gid














def _legacy_group(p: Proj, seq: int) -> str:
    """A group from before the work base records: ``groups.work_base_ref`` NULL and no floor record."""
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services import git_service as svc

    gid = f"{p.pid}.default.{seq:04d}"
    db_groups.create({"group_id": gid, "project_id": p.pid, "module": "default", "title": "t0098"})
    assert svc.ensure_worktree(p.pid, "default", gid) == "ok"
    get_store()._execute("UPDATE groups SET work_base_ref = NULL WHERE group_id = ?", [gid])
    get_store()._execute(
        "UPDATE group_git_state SET work_base_sha = NULL, work_base_sync_sha = NULL, work_base_state = NULL, "
        "work_base_evidence = NULL, work_base_recorded_at = NULL WHERE group_id = ?", [gid])
    return gid
