"""recorded work-base commits, one scope floor, merge target.

Real git end-to-end (bare origin + service base checkout, same harness as
test_git_merge_target_0594.py) on a temporary SQLite with the real migrations. Every
test builds its OWN project, so nothing leaks between cases.

Fixture shape: ``main`` and ``v0.2`` on origin, where ``v0.2`` carries commits ``main``
does not have (``v02_only_*.txt``).  A group forked from ``v0.2`` used to report that
whole ``main..v0.2`` history as its own change.

  A   main-based group keeps its result
  B   v0.2 group: scope / changes / file diff / review package / TR check /
      final recheck report exactly the group's two files
  C   local-only work base; C' origin ahead of local (fork = origin, initial sync
      does not rewind it)
  E   origin-only advance absorbed by update is not group work (+ name-based contrast);
      repeated update; conflict abort/resolve; untracked refusal; rewritten work base
  D/F project base change pins locked NULL groups; new groups pin the base
  LB  legacy backfill: reset marker, legacy marker, already-merged group
  SB  update history unproven, manual same-subject merge, confirm API valid/invalid
  R   record failures: an audit-log failure on a clean or conflict update keeps the
      previous floor; a failed work_base_ref pin leaves the backfill unapplied
  G   default merge target = work base; non-base merge records unmerge_supported=False;
      unmerge stays 409 with recovery options; preview counts incoming commits
  I   terminal reopen records a new floor
  K   remote diff/log/merge_preview default target_ref = work base
  L   static contract: no project-base merge-base left in the scope readers
"""
from __future__ import annotations

import base64
import inspect
import itertools
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
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

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary unavailable")


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._conn.execute(sql, params or [])
        self._conn.commit()
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
        self._tx_lock = threading.Lock()

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
        with self._tx_lock:
            yield _MockTxn(self._conn)

    def close(self):
        self._conn.close()


@pytest.fixture(scope="module")
def storage_dir():
    tmp = tempfile.mkdtemp(prefix="fg-wb0665-storage-")
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
V02_ONLY = ["v02_only_1.txt", "v02_only_2.txt", "v02_only_3.txt"]


class Proj:
    """bare origin with main + v0.2 (v0.2 = main + 3 files), base checkout provisioned."""

    def __init__(self, tmp: Path):
        from modules.flow_gate.db import projects
        from modules.flow_gate.services import git_service as svc

        n = next(_COUNTER)
        self.pid = f"wb{n:02d}"
        self.name = f"WB{n:02d}"
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
        _git(["checkout", "-b", "v0.2"], cwd=self.seed)
        for name in V02_ONLY:
            (self.seed / name).write_text(f"{name}\n", encoding="utf-8")
            _git(["add", "-A"], cwd=self.seed)
            _git(["commit", "-m", f"v0.2 {name}"], cwd=self.seed)
        _git(["push", "origin", "v0.2"], cwd=self.seed)
        _git(["checkout", "main"], cwd=self.seed)
        svc.save_config(self.pid, {
            "repo_url": self.bare.as_uri(), "provider": "generic",
            "base_branch": "main", "default_finalize_action": "merge", "enabled": True,
            "author_name": "T", "author_email": "t@t",
        })
        assert svc.provision_base(self.pid, "test")["status"] in ("ok", "cloned", "adopted", "ready"), \
            svc.provision_base(self.pid, "test")
        self.base_git("branch", "v0.2", "origin/v0.2")

    # ── helpers ─────────────────────────────────────────────────────────────
    @property
    def base(self) -> Path:
        from modules.flow_gate.storage.paths import src_root
        return src_root(self.name, "main")

    def base_git(self, *args: str) -> str:
        return _git(list(args), cwd=self.base).strip()

    def group(self, seq: int, work_base: str | None = None) -> str:
        from modules.flow_gate.db import groups as db_groups
        from modules.flow_gate.services import git_service as svc
        gid = f"{self.pid}.default.{seq:04d}"
        db_groups.create({
            "group_id": gid, "project_id": self.pid, "module": "default", "title": "t",
            "work_base_ref": work_base,
        })
        assert svc.ensure_worktree(self.pid, "default", gid) == "ok"
        return gid

    def wt(self, gid: str) -> Path:
        from modules.flow_gate.storage.paths import src_root
        return src_root(self.name, gid.replace(".", "_"))

    def wt_git(self, gid: str, *args: str) -> str:
        return _git(list(args), cwd=self.wt(gid)).strip()

    def commit(self, gid: str, path: str, content: str, msg: str | None = None) -> str:
        (self.wt(gid) / path).write_text(content, encoding="utf-8")
        self.wt_git(gid, "add", "-A")
        self.wt_git(gid, "commit", "-m", msg or f"edit {path}")
        return self.wt_git(gid, "rev-parse", "HEAD")

    def origin_commit(self, branch: str, path: str, content: str) -> str:
        _git(["fetch", "origin"], cwd=self.seed)
        _git(["checkout", "-B", branch, f"origin/{branch}"], cwd=self.seed)
        (self.seed / path).write_text(content, encoding="utf-8")
        _git(["add", "-A"], cwd=self.seed)
        _git(["commit", "-m", f"{branch} {path}"], cwd=self.seed)
        _git(["push", "origin", branch], cwd=self.seed)
        sha = _git(["rev-parse", "HEAD"], cwd=self.seed).strip()
        _git(["checkout", "main"], cwd=self.seed)
        return sha

    def ready(self, gid: str) -> None:
        from modules.flow_gate.db import git_integration as db_git
        _seed_wf_done_root(gid, self.pid)
        db_git.set_status(gid, "awaiting_choice")


def _seed_wf_done_root(group_id: str, project_id: str) -> None:
    from modules.flow_gate.db import documents as db_docs
    doc_id = f"{group_id}.0001-R"
    if db_docs.get_by_id(doc_id) is None:
        db_docs.create({
            "doc_id": doc_id, "project_id": project_id, "module": "default",
            "group_id": group_id, "type_code": "R", "seq": 1, "title": "root",
            "file_path": f"documents/{group_id}/0001-R.md",
        })
    db_docs.update(doc_id, {"doc_review_status": "wf_done"})


def _ledger_commit(group_id: str, project_id: str, seq: int, sha: str, created_at: str | None = None) -> None:
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import tr_commit_ledger as db_ledger
    from modules.flow_gate.db.connection import get_store
    doc_id = f"{group_id}.{seq:04d}-TR"
    if db_docs.get_by_id(doc_id) is None:
        db_docs.create({
            "doc_id": doc_id, "project_id": project_id, "module": "default",
            "group_id": group_id, "type_code": "TR", "seq": seq, "title": "tr",
            "file_path": f"documents/{group_id}/{seq:04d}-TR.md",
        })
    db_ledger.record_commit(group_id=group_id, doc_id=doc_id, commit_sha=sha, commit_subject="tr")
    if created_at:
        get_store()._execute(
            "UPDATE tr_commit_ledger SET created_at = ? WHERE doc_id = ?", [created_at, doc_id],
        )


def _forget_floor(group_id: str) -> None:
    """Simulate a group created before migration 129."""
    from modules.flow_gate.db.connection import get_store
    get_store()._execute(
        "UPDATE group_git_state SET work_base_sha = NULL, work_base_sync_sha = NULL, "
        "work_base_state = NULL, work_base_evidence = NULL WHERE group_id = ?",
        [group_id],
    )


def _set_marker(group_id: str, sha: str | None, at: str) -> None:
    from modules.flow_gate.db.connection import get_store
    get_store()._execute(
        "UPDATE group_git_state SET initial_source_sync_sha = ?, initial_source_sync_at = ? "
        "WHERE group_id = ?",
        [sha, at, group_id],
    )


def _state(gid: str) -> dict:
    from modules.flow_gate.db import git_integration as db_git
    return db_git.get_state(gid)


def _scope(pid: str, gid: str) -> set[str]:
    from modules.flow_gate.services import git_service as svc
    result = svc.collect_scope_changes(pid, gid)
    assert result["available"], result
    return set(result["paths"])


@pytest.fixture
def proj(tmp_path, monkeypatch):
    from modules.flow_gate.services import git_service as svc
    monkeypatch.setattr(svc, "_sweep_daemon_started", True)
    for key, value in (("GIT_COMMITTER_NAME", "T"), ("GIT_COMMITTER_EMAIL", "t@t")):
        monkeypatch.setenv(key, value)
    p = Proj(tmp_path)
    yield p
    svc.delete_config(p.pid)


# ── A / B ────────────────────────────────────────────────────────────────────

def test_A_main_group_keeps_its_result(proj):
    gid = proj.group(1)
    state = _state(gid)
    assert state["work_base_state"] == "verified"
    assert state["work_base_sha"] == proj.base_git("rev-parse", "main")
    proj.commit(gid, "a.txt", "a\n")
    (proj.wt(gid) / "b.txt").write_text("b\n", encoding="utf-8")
    assert _scope(proj.pid, gid) == {"a.txt", "b.txt"}


def test_B_v02_group_reports_only_its_two_files_everywhere(proj, monkeypatch):
    import io
    import zipfile
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services import review_package_service as rps
    from modules.flow_gate.services import tr_scope_service as trs

    gid = proj.group(2, work_base="v0.2")
    assert _state(gid)["work_base_sha"] == proj.base_git("rev-parse", "v0.2")
    proj.commit(gid, "shared.txt", "group edit\n")
    (proj.wt(gid) / "new_file.txt").write_text("new\n", encoding="utf-8")

    expected = {"shared.txt", "new_file.txt"}
    assert _scope(proj.pid, gid) == expected

    # Contrast: the old project-base measurement drags main..v0.2 in.
    old = proj.wt_git(gid, "merge-base", "refs/heads/main", "HEAD")
    old_paths = set(proj.wt_git(gid, "diff", "--name-only", old).split())
    assert set(V02_ONLY) <= old_paths

    changes = svc.read_group_changes(proj.pid, gid)["data"]
    assert {c["path"] for c in changes["changes"]} == expected
    assert changes["work_base_ref"] == "v0.2"
    assert changes["scope_base_sha"] == proj.base_git("rev-parse", "v0.2")

    diff = svc.read_group_file_diff(proj.pid, gid, "shared.txt")["data"]
    assert diff["old"]["content"] == "line1\n"
    assert diff["status"] == "M"
    assert svc.read_group_file_diff(proj.pid, gid, V02_ONLY[0])["data"]["status"] == "unchanged" \
        or svc.read_group_file_diff(proj.pid, gid, V02_ONLY[0])["data"]["old"]["exists"]

    monkeypatch.setattr(trs, "resolve_stage", lambda project_id: trs.STAGE_ENFORCE)
    verdict = trs.evaluate(proj.pid, gid, "## 변경 파일\n\n- shared.txt\n- new_file.txt\n")
    assert set(verdict["detected"]) == expected
    assert verdict["unreported"] == [] and verdict["verdict"] == trs.VERDICT_PASS
    assert verdict["scope_base_sha"] == proj.base_git("rev-parse", "v0.2")

    recheck = trs.evaluate_group_unreported(proj.pid, gid)
    assert recheck["checked"] is True
    assert {row["path"] for row in recheck["unreported"]} == expected

    monkeypatch.setattr(rps.tr_scope_service, "group_declared_paths",
                        lambda group_id, exclude_doc_id=None: [])
    package = rps.build_review_package(proj.pid, gid)
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        meta = json.loads(archive.read("metadata.json"))
        patch = archive.read("diff.patch").decode()
    assert set(meta["changed_files"]) == expected
    assert meta["base_sha"] == proj.base_git("rev-parse", "v0.2")
    assert meta["work_base_ref"] == "v0.2"
    assert not any(name in patch for name in V02_ONLY)


# ── C / C' ───────────────────────────────────────────────────────────────────

def test_C_local_only_work_base_records_its_tip(proj):
    from modules.flow_gate.services import git_service as svc
    svc.create_branch(proj.pid, "local-only", "main")
    proj.base_git("commit", "--allow-empty", "-m", "unused")   # base moves, local-only does not
    tip = proj.base_git("rev-parse", "local-only")
    gid = proj.group(3, work_base="local-only")
    state = _state(gid)
    assert state["work_base_sha"] == tip and state["work_base_state"] == "verified"
    proj.commit(gid, "x.txt", "x\n")
    assert _scope(proj.pid, gid) == {"x.txt"}




# ── E family ─────────────────────────────────────────────────────────────────











# ── D / F ────────────────────────────────────────────────────────────────────



# ── LB: legacy backfill ──────────────────────────────────────────────────────







# ── SB: update history and the confirm API ───────────────────────────────────









# ── record failures never leave a stale or mutable floor ────────────────────

def _fail_audit_log(monkeypatch):
    from modules.flow_gate.db import git_integration as db_git

    def boom(*_args, **_kwargs):
        raise RuntimeError("audit insert failed")
    monkeypatch.setattr(db_git, "append_work_base_log", boom)








# ── G / M: merge target ──────────────────────────────────────────────────────







# ── I: terminal reopen ───────────────────────────────────────────────────────



# ── K: remote tool default target ────────────────────────────────────────────



# ── L: static contract ───────────────────────────────────────────────────────
