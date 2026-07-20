"""flowgate.default.0280 T0007 — REAL-worktree resolution for the test runner.

NR0003 §5 found the hole this file fills. ``test_test_run_src_root_0152.py``
pins the 0190 fix by monkeypatching ``resolve_project_src_root`` itself, so it
proves only that the runner *forwards* group_id — never that a forwarded group_id
actually resolves to the group's worktree. Every layer below the forward
(git config → ledger → directory) was untested end-to-end, which is precisely
where B0001's "the tests ran in main" would live if it were real.

These tests provision a real git worktree through ``ensure_worktree`` and then
assert on the resolver the runner actually calls, with nothing mocked in between:

  - the resolver returns the worktree, not the base tree;
  - work-branch-only files are visible through the resolved root and absent from
    base (the B0001 symptom stated as an assertion);
  - the admission guard and the async worker resolve the SAME tree (the 0190
    invariant, verified through the real resolver rather than a recorded call);
  - a released worktree — the post-merge re-run — falls back to base AND reports
    ``worktree_unregistered`` as the reason (0280 T0005).

Harness mirrors test_git_integration_0115.py: TESTING=1, a temporary SQLite built
from the real sqlite migrations, a temporary FLOWGATE_STORAGE_DIR, and a skip
marker for hosts whose git cannot clone a local ``file://`` origin.
"""
from __future__ import annotations

import base64
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_GIT_ENCRYPT_KEY"] = base64.b64encode(b"K" * 32).decode()

_TMP_STORAGE = tempfile.mkdtemp(prefix="fg-wt-0280-storage-")
os.environ["FLOWGATE_STORAGE_DIR"] = _TMP_STORAGE

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

_GIT = shutil.which("git") is not None

PROJECT_ID = "wtprj"
PROJECT_NAME = "WtProj"
GROUP = "wtprj.default.0280"
WORK_BRANCH = "wtprj_default_0280"


def _run_git(args, cwd=None):
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t",
    })
    return subprocess.run(
        ["git", *args], cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, env=env,
    )


def _git(args, cwd=None):
    proc = _run_git(args, cwd)
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout


def _git_can_clone_local_file_url() -> bool:
    """Same capability probe as 0115: git-for-Windows cannot clone file:///C:/…

    An environment gap must skip, never fail — the code under test is innocent.
    """
    if not _GIT:
        return False
    probe = Path(tempfile.mkdtemp(prefix="fg-wt-probe-"))
    try:
        bare, work = probe / "o.git", probe / "w"
        if _run_git(["init", "--bare", "-b", "main", str(bare)]).returncode:
            return False
        if _run_git(["init", "-b", "main", str(work)]).returncode:
            return False
        (work / "f.txt").write_text("probe\n", encoding="utf-8")
        if _run_git(["add", "-A"], cwd=work).returncode:
            return False
        if _run_git(["commit", "-m", "probe"], cwd=work).returncode:
            return False
        _run_git(["remote", "add", "origin", str(bare)], cwd=work)
        if _run_git(["push", "origin", "main"], cwd=work).returncode:
            return False
        return _run_git(["clone", bare.as_uri(), str(probe / "c")]).returncode == 0
    except Exception:
        return False
    finally:
        shutil.rmtree(probe, ignore_errors=True)


needs_git = pytest.mark.skipif(
    not (_GIT and _git_can_clone_local_file_url()),
    reason="git binary or local file:// clone capability unavailable",
)


# ── store harness (mirrors test_git_integration_0115.py) ─────────────────────


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

    projects.create({"project_id": PROJECT_ID, "project_name": PROJECT_NAME})
    yield


@pytest.fixture(scope="module")
def worktree(seed):
    """A real cloned origin + a provisioned group worktree, torn down after."""
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.storage.paths import src_root

    tmp = Path(tempfile.mkdtemp(prefix="fg-wt-0280-origin-"))
    bare, seedwt = tmp / "origin.git", tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("base tree\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)

    svc.save_config(PROJECT_ID, {
        "repo_url": bare.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    assert svc.ensure_worktree(PROJECT_ID, "default", GROUP) == "ok"
    wt = src_root(PROJECT_NAME, WORK_BRANCH)
    assert wt.is_dir(), "worktree provisioning did not create the directory"

    yield {"worktree": wt.resolve(), "base": src_root(PROJECT_NAME, "main"), "tmp": tmp}

    svc.delete_config(PROJECT_ID)
    shutil.rmtree(tmp, ignore_errors=True)


# ── migration 069 sanity ─────────────────────────────────────────────────────


class TestMigration:
    def test_test_runs_has_source_root_columns(self, tmp_db):
        """069_test_run_source_root.sql must apply (0280 T0005)."""
        mock_db, _ = tmp_db
        cols = [r["name"] for r in mock_db.fetch_all("PRAGMA table_info(test_runs)")]
        assert "source_root" in cols
        assert "source_root_kind" in cols


# ── the resolver the runner actually calls ───────────────────────────────────


@needs_git
class TestWorktreeResolution:
    def _doc(self):
        return {
            "doc_id": "wtprj.default.0280.0001-TS",
            "project_id": PROJECT_ID,
            # Every document in a group carries branch="main" — that is exactly why
            # the TSR could not be trusted to report the execution branch (§4-A).
            "branch": "main",
            "group_id": GROUP,
        }

    def test_resolver_returns_the_worktree_not_base(self, worktree):
        from modules.flow_gate.storage.paths import resolve_project_src_root

        doc = self._doc()
        resolved = resolve_project_src_root(
            doc["project_id"], doc["branch"], group_id=doc["group_id"]
        )

        assert resolved == worktree["worktree"]
        assert resolved.name == WORK_BRANCH
        # The whole point: passing branch="main" must NOT land in the base tree.
        assert resolved != worktree["base"].resolve()

    def test_same_call_without_group_id_lands_in_base(self, worktree):
        """Pins WHY group_id matters — drop it and the run really does go to base."""
        from modules.flow_gate.storage.paths import resolve_project_src_root

        resolved = resolve_project_src_root(PROJECT_ID, "main")

        assert resolved is not None
        assert resolved != worktree["worktree"]
        assert resolved.name == "main"

    def test_work_branch_files_are_visible_through_the_resolved_root(self, worktree):
        """The B0001 symptom as an assertion.

        A TS case referencing a file created on the work branch failed with a fast
        file-not-found when the runner resolved base. The resolved root must be the
        tree that actually holds the group's work.
        """
        from modules.flow_gate.storage.paths import resolve_project_src_root

        (worktree["worktree"] / "branch_only.txt").write_text("work\n", encoding="utf-8")

        resolved = resolve_project_src_root(PROJECT_ID, "main", group_id=GROUP)

        assert (resolved / "branch_only.txt").is_file()
        # And it is genuinely absent from base — the two are different trees.
        assert not (worktree["base"] / "branch_only.txt").exists()

    def test_admission_guard_and_worker_resolve_the_same_tree(self, worktree, monkeypatch):
        """0190 invariant: the guard must not check a different tree than the run uses.

        Verified through the REAL resolver — a recorded-call assertion (0152) passes
        even if the two call sites resolve to different places.
        """
        from modules.flow_gate.services import test_run_service
        from modules.flow_gate.storage import paths as storage_paths

        doc = self._doc()
        resolved: list = []
        real = storage_paths.resolve_project_src_root

        def recording(*args, **kwargs):
            out = real(*args, **kwargs)
            resolved.append(out)
            return out

        # Admission additionally requires type_code/review status; the worker ignores both.
        admitted = {**doc, "type_code": "TS", "doc_review_status": "approved"}
        monkeypatch.setattr(storage_paths, "resolve_project_src_root", recording)
        monkeypatch.setattr(test_run_service.db_docs, "get_by_id", lambda _id: admitted)
        monkeypatch.setattr(
            test_run_service.process_service, "is_group_disposed", lambda _g: False
        )
        # Admission: stop it right after the src_root guard, which is all we assert on.
        monkeypatch.setattr(
            test_run_service, "_read_doc_content",
            lambda _doc: (_ for _ in ()).throw(RuntimeError("stop after guard")),
        )
        # Worker: stop it right after resolution + bookkeeping.
        monkeypatch.setattr(
            test_run_service, "_allocate_port",
            lambda: (_ for _ in ()).throw(RuntimeError("stop after resolve")),
        )

        with pytest.raises(RuntimeError):
            test_run_service.validate_and_create_run(
                doc_id=doc["doc_id"], runner_id="usr", triggered_via="token"
            )
        with pytest.raises(RuntimeError):
            test_run_service._execute_run_inner(
                {"run_id": "trun_wt_0280", "doc_id": doc["doc_id"]}
            )

        assert len(resolved) == 2, f"expected one resolution each, got {resolved}"
        assert resolved[0] == resolved[1] == worktree["worktree"]

    def test_run_records_the_worktree_it_used(self, worktree, monkeypatch):
        """T0005's bookkeeping, exercised against a real worktree."""
        from modules.flow_gate.services import test_run_service
        from modules.flow_gate.storage import paths as storage_paths

        doc = self._doc()
        monkeypatch.setattr(test_run_service.db_docs, "get_by_id", lambda _id: doc)
        recorded: dict = {}
        monkeypatch.setattr(
            test_run_service.db_test_runs, "set_run_source_root",
            lambda run_id, source_root, source_root_kind: recorded.update(
                source_root=source_root, kind=source_root_kind
            ),
        )
        monkeypatch.setattr(
            test_run_service, "_allocate_port",
            lambda: (_ for _ in ()).throw(RuntimeError("stop")),
        )

        with pytest.raises(RuntimeError):
            test_run_service._execute_run_inner(
                {"run_id": "trun_wt_0280b", "doc_id": doc["doc_id"]}
            )

        assert recorded["kind"] == "worktree"
        assert recorded["source_root"] == f"src/{PROJECT_NAME}/{WORK_BRANCH}"
        assert storage_paths.classify_src_root(
            PROJECT_ID, GROUP, worktree["worktree"]
        ) == "worktree"


# ── post-merge re-run: the silent switch NR0003 §4-B warned about ────────────


@needs_git
class TestReleasedWorktree:
    def test_released_worktree_falls_back_to_base_with_a_named_reason(self, worktree):
        """Merging releases the worktree; a re-run then silently moves to base.

        The fallback itself is intended (the tree may be gone). What T0005 changed is
        that it is now named, so the next report can be settled instead of argued.
        """
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import resolve_project_src_root

        before = resolve_project_src_root(PROJECT_ID, "main", group_id=GROUP)
        assert before == worktree["worktree"]

        db_git.unregister_worktree(GROUP)
        try:
            after = resolve_project_src_root(PROJECT_ID, "main", group_id=GROUP)
            path, reason = svc.effective_src_root_ex(PROJECT_ID, GROUP)

            # The directory still exists, so this is NOT a vanished-tree fallback —
            # the ledger flag alone moved the run to base. That is the re-run hazard.
            assert worktree["worktree"].is_dir()
            assert after == worktree["base"].resolve()
            assert path is None
            assert reason == "worktree_unregistered"
        finally:
            db_git.register_worktree(GROUP, PROJECT_ID, WORK_BRANCH)

    def test_reregistering_restores_the_worktree(self, worktree):
        from modules.flow_gate.storage.paths import resolve_project_src_root

        assert resolve_project_src_root(
            PROJECT_ID, "main", group_id=GROUP
        ) == worktree["worktree"]
