"""flowgate.v02test.0052 T0003 — abort 막힘 풀기 (server).

merge 457 (0036 → feature/v0.2.0) could not be aborted: ``git merge --abort`` exited 128
five times with "Entry '<path>' not uptodate. Cannot merge." although nothing had changed.
A staged file had been rewritten with identical bytes (``apply_eol_separation`` staged an
EOL-only file, then ``rerere_checkpoint.start_session``'s ``_restore`` wrote the same bytes
back seconds later), so only the index stat cache was stale. Every button of the conflict
card failed through that one abort and nothing recorded why.

Real git end-to-end (bare origin + service clone + managed target workspace, like
test_git_merge_target_0594.py) on a temporary SQLite with the real migrations:

  1  stale index stat: a raw ``git merge --abort`` fails (the 457 state); the server's
     user abort and the TTL sweep both succeed (common abort: refresh first)
  2  the cause: EOL separation + rerere restore no longer leave a stat-stale index
  3  MERGE_HEAD gone: abort closes "already cleaned"; resolve is refused before writing
  4  a failed abort carries git's one-line reason (API details/params, session audit,
     session "정리 실패" marker, conflict list, administrator list); sweep failure too
  5  administrator force cleanup: non-admin 403, preview, reset of the managed target
     workspace only, session ``aborted``, group branch untouched; base target refused
  6  reading the conflict list is not activity (touched_at unchanged)
  7  opening the review (merge review / TR review GET) is not activity; a review
     conversation turn is
  8  a failed abort is audited on the session (ref_operations[].stderr_tail +
     context.abort_failure) on every session path: [보류] hold, group-update sweep,
     Graph attempt give-up, interrupted-attempt recovery, disposed group (which also
     keeps its session open instead of closing it over a still-merging workspace)
"""
from __future__ import annotations

import base64
import itertools
import os
import shutil
import sqlite3
import subprocess
import sys
import time
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
    tmp = str(tmp_path_factory.mktemp("fg-abort0052-storage"))
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
    db_path = str(storage_dir / "fixture.db")
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
    return proc


_COUNTER = itertools.count(1)


class Proj:
    """One isolated project: bare origin (main + develop), seed clone, enabled config."""

    def __init__(self, tmp: Path):
        from modules.flow_gate.db import projects
        from modules.flow_gate.services import git_service as svc

        n = next(_COUNTER)
        self._local: set[str] = set()
        self.pid = f"ab{n:02d}"
        self.name = f"AB{n:02d}"
        projects.create({"project_id": self.pid, "project_name": self.name})
        self.bare = tmp / f"{self.pid}.git"
        self.seed = tmp / f"{self.pid}-seed"
        _git(["init", "--bare", "-b", "main", str(self.bare)])
        _git(["init", "-b", "main", str(self.seed)])
        _git(["config", "core.autocrlf", "false"], cwd=self.seed)
        # byte-exact on a host whose system git config sets autocrlf=true (as 0608 does)
        (self.seed / ".gitattributes").write_bytes(b"* -text\n")
        (self.seed / "README.md").write_bytes(b"hello\n")
        (self.seed / "shared.txt").write_bytes(b"line1\n")
        (self.seed / "other.txt").write_bytes(b"other1\n")
        (self.seed / "eol.txt").write_bytes(b"a\nb\nc\n")
        _git(["add", "-A"], cwd=self.seed)
        _git(["commit", "-m", "init"], cwd=self.seed)
        _git(["remote", "add", "origin", str(self.bare)], cwd=self.seed)
        _git(["push", "origin", "main"], cwd=self.seed)
        _git(["push", "origin", "main:develop"], cwd=self.seed)
        svc.save_config(self.pid, {
            "repo_url": self.bare.as_uri(), "provider": "generic",
            "base_branch": "main", "default_finalize_action": "merge", "enabled": True,
        })

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
        """A local target branch in the base checkout (a remote-only one is refused)."""
        from modules.flow_gate.services import git_service as svc
        if name != "main" and name not in self._local:
            svc.create_branch(self.pid, name, "main")
            self._local.add(name)

    def origin_commit(self, branch: str, files: dict) -> None:
        _git(["fetch", "origin"], cwd=self.seed)
        _git(["checkout", "-B", branch, f"origin/{branch}"], cwd=self.seed)
        for path, data in files.items():
            target = self.seed / path
            target.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        _git(["add", "-A"], cwd=self.seed)
        _git(["commit", "-m", f"{branch} change"], cwd=self.seed)
        _git(["push", "origin", branch], cwd=self.seed)
        _git(["checkout", "main"], cwd=self.seed)

    def origin_sha(self, branch: str) -> str:
        return _git(["rev-parse", branch], cwd=self.bare).stdout.strip()


def _seed_wf_done_root(group_id: str, project_id: str) -> None:
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups

    if db_groups.get_by_id(group_id) is None:
        db_groups.create({
            "group_id": group_id, "project_id": project_id,
            "module": "default", "title": "t0052",
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
    from modules.flow_gate.services.git import merge_target as mt
    mt.settle_check_targets(30)
    svc.delete_config(p.pid)


def _session(merge_id: int) -> dict:
    from modules.flow_gate.db import git_integration as db_git
    return db_git.get_session(merge_id)


def _ctx(merge_id: int) -> dict:
    from modules.flow_gate.db import git_integration as db_git
    return db_git.session_context(_session(merge_id))


def _two_file_conflict(p: Proj, gid: str, branch: str = "develop") -> dict:
    """The group and ``branch`` both edit shared.txt and other.txt → two conflicted files."""
    from modules.flow_gate.services import git_service as svc
    p.local_branch(branch)
    (p.wt(gid) / "shared.txt").write_bytes(f"{gid} version\n".encode())
    (p.wt(gid) / "other.txt").write_bytes(f"{gid} other\n".encode())
    p.origin_commit(branch, {"shared.txt": f"{branch} version\n", "other.txt": f"{branch} other\n"})
    p.ready(gid)
    out = svc.finalize(gid, "merge", target_branch=None if branch == "main" else branch)
    assert out["result"]["status"] == "conflict", out
    assert sorted(out["result"]["conflict_files"]) == ["other.txt", "shared.txt"]
    return out["result"]


def _rewrite_same_bytes_later(path: Path) -> None:
    """What merge 457's file went through: identical bytes written again, later."""
    data = path.read_bytes()
    path.write_bytes(data)
    later = time.time() + 30
    os.utime(path, (later, later))


def _stat_dirty(root: Path) -> list[str]:
    """Index entries whose stat no longer matches (no refresh: exactly what abort sees)."""
    out = _git(["diff-files", "--name-only"], cwd=root).stdout
    return sorted({line for line in out.splitlines() if line})


def _merge_head(root: Path):
    proc = _git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=root, check=False)
    return proc.stdout.strip() or None


def _age_session(merge_id: int, hours: int = 48) -> None:
    from datetime import datetime, timedelta, timezone
    from modules.flow_gate.db.connection import get_store
    old = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    get_store()._execute(
        "UPDATE git_merge_session SET touched_at = ?, created_at = ? WHERE merge_id = ?",
        [old, old, merge_id],
    )


def _git_path(root: Path, name: str) -> Path:
    rel = _git(["rev-parse", "--git-path", name], cwd=root).stdout.strip()
    return Path(rel) if Path(rel).is_absolute() else root / rel


def _hold_index_lock(root: Path) -> Path:
    """An ``index.lock`` left behind: no refresh can fix it, so the abort really fails."""
    lock = _git_path(root, "index.lock")
    lock.write_text("", encoding="utf-8")
    return lock


def _group_branch_sha(p: Proj, gid: str) -> str:
    from modules.flow_gate.db import git_integration as db_git
    branch = db_git.get_state(gid)["branch"]
    return _git(["rev-parse", f"refs/heads/{branch}"], cwd=p.base).stdout.strip()


def _routes_client(user: dict):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.v1 import git_routes

    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[git_routes.get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


ADMIN = {"user_id": "admin-1", "is_admin": True}
MEMBER = {"user_id": "member-1", "is_admin": False}


# ── 1. stale index stat: user abort and TTL sweep ─────────────────────────────────────

def _stale_state(p: Proj, gid: str) -> tuple[int, Path]:
    from modules.flow_gate.services import git_service as svc
    result = _two_file_conflict(p, gid)
    merge_id = result["merge_id"]
    root = svc.resolve_conflict_src_root(gid, merge_id)
    out = svc.resolve_conflicts(gid, merge_id, [{
        "path": "shared.txt", "content": f"{gid} version\ndevelop version\n",
    }], False)
    assert out["result"]["status"] == "conflict"
    assert _stat_dirty(root) == ["other.txt"]          # only the still-unmerged file
    _rewrite_same_bytes_later(root / "shared.txt")
    assert "shared.txt" in _stat_dirty(root)            # same blob, stale stat (457)
    return merge_id, root


def test_raw_abort_fails_on_stale_stat_but_user_abort_succeeds(proj):
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    merge_id, root = _stale_state(proj, gid)
    # The exact 457 failure, with plain git (what the server ran before this card):
    raw = _git(["merge", "--abort"], cwd=root, check=False)
    assert raw.returncode == 128
    assert "not uptodate" in raw.stderr
    assert _merge_head(root) is not None                 # nothing changed

    out = svc.abort_merge(gid, merge_id)
    assert out["result"]["status"] == "waiting"
    assert out["result"]["already_cleaned"] is False
    assert _session(merge_id)["status"] == "aborted"
    assert not root.exists() or _merge_head(root) is None
    ops = _ctx(merge_id).get("ref_operations") or []
    assert ops and ops[-1]["reason"] == "user_abort" and ops[-1]["returncode"] == 0
    assert "stderr_tail" not in ops[-1]                  # success keeps no output


def test_ttl_sweep_abort_succeeds_on_stale_stat(proj):
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(2)
    merge_id, root = _stale_state(proj, gid)
    _age_session(merge_id)
    svc.merge_session_sweep()
    session = _session(merge_id)
    assert session["status"] == "aborted", _ctx(merge_id)
    assert not root.exists() or _merge_head(root) is None
    assert "abort_failure" not in _ctx(merge_id)


def test_common_abort_returns_reason_and_refresh_code(tmp_path):
    from modules.flow_gate.services import git_service as svc

    repo = tmp_path / "r"
    _git(["init", "-b", "main", str(repo)])
    _git(["config", "core.autocrlf", "false"], cwd=repo)
    (repo / "a.txt").write_bytes(b"a\n")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-m", "c"], cwd=repo)
    result = svc._abort_merge(repo)
    assert result.returncode != 0 and not result.ok
    assert result.reason and "MERGE_HEAD" in result.reason
    assert result.refresh_returncode == 0





# ── 2. the cause: EOL separation + rerere restore keep the index fresh ───────────────





# ── 3. MERGE_HEAD gone: abort "already cleaned", resolve refused before writing ───────





# ── 4. a failed abort says why: API, audit, session marker, lists ─────────────────────





# ── 5. administrator force cleanup ────────────────────────────────────────────────────





# ── 6. reading the conflict list is not activity ──────────────────────────────────────



# ── 7. opening a review is not activity either (일반 병합 · TR 검토) ───────────────────

def _resolve_everything(p: Proj, gid: str) -> tuple[int, Path]:
    """A finalize merge with both files resolved: ``resolved_pending_review``."""
    from modules.flow_gate.services import git_service as svc
    merge_id = _two_file_conflict(p, gid)["merge_id"]
    root = svc.resolve_conflict_src_root(gid, merge_id)
    out = svc.resolve_conflicts(gid, merge_id, [
        {"path": "shared.txt", "content": f"{gid} version\ndevelop version\n"},
        {"path": "other.txt", "content": f"{gid} other\ndevelop other\n"},
    ], True)
    assert out["result"]["status"] == "resolved_pending_review", out
    return merge_id, root






# ── 8. every session abort failure is audited, not only the user [abort] ─────────────

def _assert_abort_audited(merge_id: int, reason: str, trigger: str) -> dict:
    """``ref_operations[-1]`` holds the failed abort with git's reason (``stderr_tail``)
    and the session carries the "정리 실패" marker with the same reason."""
    context = _ctx(merge_id)
    op = context["ref_operations"][-1]
    assert op["operation"] == "merge" and op["reason"] == reason, op
    assert op["status"] == "completed" and op["returncode"] not in (None, 0)
    assert "index.lock" in op["stderr_tail"], op
    failure = context["abort_failure"]
    assert failure["trigger"] == trigger, failure
    assert failure["reason"] == op["stderr_tail"]
    assert _session(merge_id)["status"] == "open"          # nothing hidden as success
    return context


def _conflict_in_group_worktree(p: Proj, gid: str) -> tuple[Path, str]:
    """A real conflicted ``git merge`` in the group worktree; returns (root, MERGE_HEAD)."""
    root = p.wt(gid)
    _git(["config", "core.autocrlf", "false"], cwd=root)
    (root / "shared.txt").write_bytes(b"side\n")
    _git(["commit", "-am", "side"], cwd=root)
    side = _git(["rev-parse", "HEAD"], cwd=root).stdout.strip()
    _git(["reset", "--hard", "HEAD~1"], cwd=root)
    (root / "shared.txt").write_bytes(b"group\n")
    _git(["commit", "-am", "group"], cwd=root)
    merged = _git(["merge", "--no-ff", "--no-commit", side], cwd=root, check=False)
    assert merged.returncode != 0 and _merge_head(root) == side
    return root, side
