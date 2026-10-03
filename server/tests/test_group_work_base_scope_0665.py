"""flowgate.default.0665 T0004 — recorded work-base commits, one scope floor, merge target.

Real git end-to-end (bare origin + service base checkout, same harness as
test_git_merge_target_0594.py) on a temporary SQLite with the real migrations. Every
test builds its OWN project, so nothing leaks between cases.

Fixture shape: ``main`` and ``v0.2`` on origin, where ``v0.2`` carries commits ``main``
does not have (``v02_only_*.txt``).  A group forked from ``v0.2`` used to report that
whole ``main..v0.2`` history as its own change (B0001).

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

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary unavailable")


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


def test_C_prime_origin_ahead_forks_from_origin_and_initial_sync_keeps_it(proj):
    from modules.flow_gate.services import git_service as svc
    origin_tip = proj.origin_commit("v0.2", "origin_ahead.txt", "o\n")
    gid = proj.group(4, work_base="v0.2")
    local_tip = proj.base_git("rev-parse", "refs/heads/v0.2")
    assert local_tip != origin_tip
    assert _state(gid)["work_base_sha"] == origin_tip
    assert proj.wt_git(gid, "rev-parse", "HEAD") == origin_tip

    out = svc.ensure_initial_group_source_sync(proj.pid, "default", gid)
    assert out["performed"] is True and out["sha"] == origin_tip
    assert (proj.wt(gid) / "origin_ahead.txt").exists()   # not rewound to the local tip
    assert _scope(proj.pid, gid) == set()


# ── E family ─────────────────────────────────────────────────────────────────

def test_E_origin_only_advance_absorbed_by_update_is_not_group_work(proj):
    from modules.flow_gate.services import git_service as svc
    gid = proj.group(5, work_base="v0.2")
    proj.commit(gid, "mine.txt", "m\n")
    x1 = proj.origin_commit("v0.2", "x1.txt", "1\n")
    x2 = proj.origin_commit("v0.2", "x2.txt", "2\n")
    local_tip = proj.base_git("rev-parse", "refs/heads/v0.2")

    out = svc.update_from_base(gid)["result"]
    assert out["status"] == "updated"
    assert out["source_ref"] == "origin/v0.2" and out["source_sha"] == x2
    assert _state(gid)["work_base_sync_sha"] == x2
    assert proj.base_git("rev-parse", "refs/heads/v0.2") == local_tip   # local never moved

    assert _scope(proj.pid, gid) == {"mine.txt"}
    changes = svc.read_group_changes(proj.pid, gid)["data"]["changes"]
    assert {c["path"] for c in changes} == {"mine.txt"}
    # Contrast: a NAME-based floor (local v0.2 tip) counts X1..Xn as group work.
    named = proj.wt_git(gid, "merge-base", "HEAD", "refs/heads/v0.2")
    assert {"x1.txt", "x2.txt"} <= set(proj.wt_git(gid, "diff", "--name-only", named).split())
    from modules.flow_gate.db import git_integration as db_git
    log = db_git.list_work_base_log(gid)
    assert [row["kind"] for row in log] == ["fork", "update"]
    assert log[-1]["source_sha"] == x2 and x1 != x2


def test_E_repeat_floor_only_moves_forward(proj):
    from modules.flow_gate.services import git_service as svc
    gid = proj.group(6, work_base="v0.2")
    first = proj.origin_commit("v0.2", "r1.txt", "1\n")
    assert svc.update_from_base(gid)["result"]["source_sha"] == first
    # Second round: the LOCAL work base is ahead of origin now.
    proj.base_git("checkout", "-q", "v0.2")
    proj.base_git("merge", "-q", "--ff-only", "origin/v0.2")
    (proj.base / "r2.txt").write_text("2\n", encoding="utf-8")
    proj.base_git("add", "-A")
    proj.base_git("commit", "-m", "local r2")
    second = proj.base_git("rev-parse", "HEAD")
    proj.base_git("checkout", "-q", "main")
    out = svc.update_from_base(gid)["result"]
    assert out["source_ref"] == "v0.2" and out["source_sha"] == second
    assert _state(gid)["work_base_sync_sha"] == second
    assert proj.base_git("merge-base", "--is-ancestor", first, second) == ""
    assert _scope(proj.pid, gid) == set()


def test_E_conflict_abort_keeps_floor_and_resolution_records_source(proj):
    from modules.flow_gate.services import git_service as svc
    gid = proj.group(7, work_base="v0.2")
    proj.commit(gid, "shared.txt", "group version\n")
    floor = _state(gid)["work_base_sha"]
    tip = proj.origin_commit("v0.2", "shared.txt", "v0.2 version\n")

    first = svc.update_from_base(gid)["result"]
    assert first["status"] == "conflict" and first["source_sha"] == tip
    svc.abort_merge(gid, first["merge_id"])
    assert _state(gid)["work_base_sync_sha"] is None
    assert _state(gid)["work_base_sha"] == floor
    assert _scope(proj.pid, gid) == {"shared.txt"}

    second = svc.update_from_base(gid)["result"]
    assert second["status"] == "conflict"
    resolved = svc.resolve_conflicts(
        gid, second["merge_id"],
        [{"path": "shared.txt", "content": "group version\nv0.2 version\n"}], True,
    )["result"]
    assert resolved["status"] == "updated" and resolved["source_sha"] == tip
    assert _state(gid)["work_base_sync_sha"] == tip
    assert _scope(proj.pid, gid) == {"shared.txt"}


def test_E_untracked_refusal_does_not_move_the_floor(proj):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git_service import GitServiceError
    gid = proj.group(8, work_base="v0.2")
    # A policy-excluded path is never absorbed, so it stays untracked and blocks.
    proj.origin_commit("v0.2", "debug.log", "theirs\n")
    (proj.wt(gid) / "debug.log").write_text("mine, untracked\n", encoding="utf-8")
    # The recover probe classifies against the SAME source the update merges
    # (main has no debug.log, so a project-base probe would find no blocker).
    with pytest.raises(GitServiceError) as exc:
        svc.update_from_base(gid)
    assert exc.value.code == "group_untracked_conflict"
    assert _state(gid)["work_base_sync_sha"] is None
    with pytest.raises(GitServiceError) as bad:
        svc.group_update_untracked_recover(gid, ["README.md"], "remove")
    assert "debug.log" in bad.value.details["allowed"]


def test_E_prime_rewritten_work_base_fails_explicitly(proj):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git_service import GitServiceError
    gid = proj.group(9, work_base="v0.2")
    proj.commit(gid, "g.txt", "g\n")
    # v0.2 is rewritten on both sides: a new root-less history replaces it.
    _git(["checkout", "-B", "v0.2", "main"], cwd=proj.seed)
    (proj.seed / "rewritten.txt").write_text("r\n", encoding="utf-8")
    _git(["add", "-A"], cwd=proj.seed)
    _git(["commit", "-m", "rewrite"], cwd=proj.seed)
    _git(["push", "-f", "origin", "v0.2"], cwd=proj.seed)
    _git(["checkout", "main"], cwd=proj.seed)
    proj.base_git("fetch", "origin", "+v0.2:refs/remotes/origin/v0.2")
    proj.base_git("branch", "-f", "v0.2", "origin/v0.2")

    result = svc.collect_scope_changes(proj.pid, gid)
    assert result["available"] is False
    assert result["reason"] == "group_work_base_diverged"
    with pytest.raises(GitServiceError) as exc:
        svc.read_group_changes(proj.pid, gid)
    assert exc.value.code == "group_work_base_diverged"
    with pytest.raises(GitServiceError) as upd:
        svc.update_from_base(gid)
    assert upd.value.code in ("group_work_base_diverged", "group_work_base_source_rewritten")


# ── D / F ────────────────────────────────────────────────────────────────────

def test_D_F_new_groups_pin_the_base_and_base_change_pins_locked_null_rows(proj):
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.workflow import pipeline_service

    created = pipeline_service.create_group(
        project_id=proj.pid, module="default", title="t", actor_user_id="u",
        user_permissions={"project.group.manage"}, group_id=f"{proj.pid}.default.0100",
    )
    assert created["work_base_ref"] == "main"            # F: never NULL for a Git project

    legacy = proj.group(10)                                # NULL work_base_ref, locked
    assert db_groups.get_by_id(legacy)["work_base_ref"] is None
    floor = _state(legacy)["work_base_sha"]
    svc.create_branch(proj.pid, "develop", "main")
    out = svc.save_config(proj.pid, {
        "repo_url": proj.bare.as_uri(), "provider": "generic", "base_branch": "develop",
        "default_finalize_action": "merge", "enabled": True,
    })
    assert legacy in out["pinned_work_base_groups"]
    assert db_groups.get_by_id(legacy)["work_base_ref"] == "main"
    assert svc.resolve_group_work_base_ref(proj.pid, legacy) == "main"
    assert _state(legacy)["work_base_sha"] == floor
    proj.commit(legacy, "d.txt", "d\n")
    assert _scope(proj.pid, legacy) == {"d.txt"}


# ── LB: legacy backfill ──────────────────────────────────────────────────────

def test_LB1_reset_marker_is_verified(proj):
    from modules.flow_gate.services import git_service as svc
    gid = proj.group(11, work_base="v0.2")
    fork = _state(gid)["work_base_sha"]
    _forget_floor(gid)
    _set_marker(gid, fork, "2026-01-02T00:00:00")
    proj.commit(gid, "after.txt", "a\n")
    _ledger_commit(gid, proj.pid, 2, proj.wt_git(gid, "rev-parse", "HEAD"), "2026-01-03T00:00:00")

    assert svc.collect_scope_changes(proj.pid, gid)["reason"] == "group_work_base_unverified"
    report = svc.backfill_group_work_base(proj.pid)          # dry-run first
    row = next(r for r in report["groups"] if r["group_id"] == gid)
    assert row["verdict"] == "verified" and row["origin"] == "initial_sync_marker"
    assert _state(gid)["work_base_state"] is None            # dry-run wrote nothing
    svc.backfill_group_work_base(proj.pid, apply=True, actor="admin")
    assert _state(gid)["work_base_sha"] == fork and _state(gid)["work_base_state"] == "verified"
    assert _scope(proj.pid, gid) == {"after.txt"}


def test_LB2_legacy_marker_is_not_copied_and_prior_tr_files_stay_in_scope(proj):
    from modules.flow_gate.services import git_service as svc
    gid = proj.group(12, work_base="v0.2")
    c1 = proj.commit(gid, "tr1.txt", "1\n")
    _ledger_commit(gid, proj.pid, 2, c1, "2026-01-01T00:00:00")
    c2 = proj.commit(gid, "tr2.txt", "2\n")
    _ledger_commit(gid, proj.pid, 3, c2, "2026-01-01T00:00:01")
    _forget_floor(gid)
    _set_marker(gid, c2, "2026-01-02T00:00:00")   # legacy path: marker = HEAD with TR work
    c3 = proj.commit(gid, "tr3.txt", "3\n")
    _ledger_commit(gid, proj.pid, 4, c3, "2026-01-03T00:00:00")

    # Contrast: the marker passes "ancestor of HEAD" yet would drop tr1/tr2.
    assert proj.wt_git(gid, "merge-base", "--is-ancestor", c2, "HEAD") == ""
    assert "tr1.txt" not in proj.wt_git(gid, "diff", "--name-only", c2)

    svc.backfill_group_work_base(proj.pid, apply=True)
    state = _state(gid)
    assert state["work_base_state"] == "verified"
    assert state["work_base_sha"] == proj.base_git("rev-parse", "v0.2")
    assert _scope(proj.pid, gid) == {"tr1.txt", "tr2.txt", "tr3.txt"}


def test_LB4_group_already_merged_into_its_work_base_never_reports_empty(proj):
    from modules.flow_gate.services import git_service as svc
    gid = proj.group(13, work_base="v0.2")
    c1 = proj.commit(gid, "merged_work.txt", "w\n")
    _ledger_commit(gid, proj.pid, 2, c1, "2026-01-01T00:00:00")
    proj.base_git("branch", "-f", "v0.2", c1)               # someone merged it by hand
    _forget_floor(gid)
    _set_marker(gid, c1, "2026-01-02T00:00:00")
    svc.backfill_group_work_base(proj.pid, apply=True)
    state = _state(gid)
    if state["work_base_state"] == "verified":
        assert "merged_work.txt" in _scope(proj.pid, gid)
    else:
        assert svc.collect_scope_changes(proj.pid, gid)["reason"] == "group_work_base_unverified"


# ── SB: update history and the confirm API ───────────────────────────────────

def test_SB1_no_update_history_is_verified_with_null_sync(proj):
    from modules.flow_gate.services import git_service as svc
    gid = proj.group(14, work_base="v0.2")
    proj.commit(gid, "s.txt", "s\n")
    _forget_floor(gid)
    svc.backfill_group_work_base(proj.pid, apply=True)
    state = _state(gid)
    assert state["work_base_state"] == "verified" and state["work_base_sync_sha"] is None


def test_SB2_SB3_update_history_is_unverified_and_a_manual_same_subject_merge_cannot_be_confirmed(proj):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git_service import GitServiceError
    gid = proj.group(15, work_base="v0.2")
    fork = _state(gid)["work_base_sha"]
    proj.commit(gid, "own.txt", "o\n")
    # A feature branch off the fork (NOT in v0.2), merged with the FlowGate subject.
    proj.wt_git(gid, "branch", "feature", fork)
    proj.wt_git(gid, "checkout", "-q", "feature")
    proj.commit(gid, "feature.txt", "f\n")
    proj.wt_git(gid, "checkout", "-q", gid.replace(".", "_"))
    proj.wt_git(gid, "-c", "user.name=FlowGate", "-c", "user.email=flowgate@localhost",
                "merge", "--no-ff", "feature", "-m", f"Merge base 'v0.2' into '{gid.replace('.', '_')}'")
    feature_sha = proj.wt_git(gid, "rev-parse", "feature")
    _forget_floor(gid)

    report = svc.backfill_group_work_base(proj.pid, apply=True)
    row = next(r for r in report["groups"] if r["group_id"] == gid)
    assert row["verdict"] == "unverified" and row["reason"] == "update_history_unproven"
    assert row["candidates"][0]["second_parent"] == feature_sha
    result = svc.collect_scope_changes(proj.pid, gid)
    assert result["reason"] == "group_work_base_unverified"
    assert result["work_base_error"]["details"]["candidates"]
    with pytest.raises(GitServiceError) as upd:
        svc.update_from_base(gid)                      # refused before confirmation
    assert upd.value.code == "group_work_base_unverified"

    # SB-3: the feature tip is not v0.2 history -> rule 7 refuses it.
    with pytest.raises(GitServiceError) as bad:
        svc.confirm_group_work_base(proj.pid, gid, work_base_sha=fork,
                                    work_base_sync_sha=feature_sha, actor="admin")
    assert bad.value.status == 422 and bad.value.details["reason"] == "outside_work_base_history"
    assert _state(gid)["work_base_state"] == "unverified"

    # SB-6: floor = fork is always allowed (over-inclusive, never drops work).
    ok = svc.confirm_group_work_base(proj.pid, gid, work_base_sha=fork,
                                     work_base_sync_sha=None, actor="admin", basis="checked")
    assert ok["work_base_state"] == "confirmed"
    assert _scope(proj.pid, gid) == {"own.txt", "feature.txt"}
    from modules.flow_gate.db import git_integration as db_git
    assert db_git.list_work_base_log(gid)[-1]["kind"] == "manual_confirm"


def test_SB5_confirm_rejects_a_floor_containing_group_work(proj):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git_service import GitServiceError
    gid = proj.group(16, work_base="v0.2")
    c1 = proj.commit(gid, "tr.txt", "t\n")
    _ledger_commit(gid, proj.pid, 2, c1)
    before = dict(_state(gid))
    with pytest.raises(GitServiceError) as exc:
        svc.confirm_group_work_base(proj.pid, gid, work_base_sha=c1,
                                    work_base_sync_sha=None, actor="admin")
    assert exc.value.status == 422
    assert exc.value.details["reason"] in ("outside_work_base_history", "floor_contains_group_work")
    after = _state(gid)
    assert after["work_base_sha"] == before["work_base_sha"]
    assert after["work_base_state"] == before["work_base_state"]


def test_unverified_group_blocks_the_final_approval_recheck(proj):
    from modules.flow_gate.services import tr_scope_service as trs
    gid = proj.group(17, work_base="v0.2")
    _forget_floor(gid)
    out = trs.evaluate_group_unreported(proj.pid, gid)
    assert out["checked"] is False and out["blocking"] is True
    assert out["work_base_error"]["code"] == "group_work_base_unverified"


# ── record failures never leave a stale or mutable floor ────────────────────

def _fail_audit_log(monkeypatch):
    from modules.flow_gate.db import git_integration as db_git

    def boom(*_args, **_kwargs):
        raise RuntimeError("audit insert failed")
    monkeypatch.setattr(db_git, "append_work_base_log", boom)


def test_E_clean_update_audit_failure_keeps_the_previous_floor(proj, monkeypatch):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git_service import GitServiceError
    gid = proj.group(30, work_base="v0.2")
    proj.commit(gid, "mine.txt", "m\n")
    fork = _state(gid)["work_base_sha"]
    tip = proj.origin_commit("v0.2", "u1.txt", "1\n")
    head = proj.wt_git(gid, "rev-parse", "HEAD")

    with monkeypatch.context() as m:
        _fail_audit_log(m)
        with pytest.raises(GitServiceError) as exc:
            svc.update_from_base(gid)
    assert exc.value.code == "group_work_base_record_failed"
    state = _state(gid)
    assert state["work_base_sync_sha"] is None and state["work_base_sha"] == fork
    assert proj.wt_git(gid, "rev-parse", "HEAD") == head              # merge rolled back
    assert _scope(proj.pid, gid) == {"mine.txt"}
    assert [row["kind"] for row in db_git.list_work_base_log(gid)] == ["fork"]

    # The kept floor is still usable: a retry absorbs the same source.
    assert svc.update_from_base(gid)["result"]["source_sha"] == tip
    assert _state(gid)["work_base_sync_sha"] == tip

    # A recorded sync value is restored too, not only a NULL one.
    tip2 = proj.origin_commit("v0.2", "u2.txt", "2\n")
    head = proj.wt_git(gid, "rev-parse", "HEAD")
    with monkeypatch.context() as m:
        _fail_audit_log(m)
        with pytest.raises(GitServiceError):
            svc.update_from_base(gid)
    assert _state(gid)["work_base_sync_sha"] == tip
    assert proj.wt_git(gid, "rev-parse", "HEAD") == head
    assert svc.update_from_base(gid)["result"]["source_sha"] == tip2
    assert _state(gid)["work_base_sync_sha"] == tip2
    assert _scope(proj.pid, gid) == {"mine.txt"}


def test_E_conflict_resolution_audit_failure_keeps_the_previous_floor(proj, monkeypatch):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git_service import GitServiceError
    gid = proj.group(31, work_base="v0.2")
    proj.commit(gid, "shared.txt", "group version\n")
    fork = _state(gid)["work_base_sha"]
    tip = proj.origin_commit("v0.2", "shared.txt", "v0.2 version\n")
    head = proj.wt_git(gid, "rev-parse", "HEAD")
    resolution = [{"path": "shared.txt", "content": "group version\nv0.2 version\n"}]

    first = svc.update_from_base(gid)["result"]
    assert first["status"] == "conflict"
    with monkeypatch.context() as m:
        _fail_audit_log(m)
        with pytest.raises(GitServiceError) as exc:
            svc.resolve_conflicts(gid, first["merge_id"], resolution, True)
    assert exc.value.code == "group_work_base_record_failed"
    state = _state(gid)
    assert state["work_base_sync_sha"] is None and state["work_base_sha"] == fork
    assert proj.wt_git(gid, "rev-parse", "HEAD") == head              # resolution undone
    assert db_git.get_session(first["merge_id"])["status"] == "aborted"
    assert _scope(proj.pid, gid) == {"shared.txt"}
    assert [row["kind"] for row in db_git.list_work_base_log(gid)] == ["fork"]

    second = svc.update_from_base(gid)["result"]
    assert second["status"] == "conflict"
    resolved = svc.resolve_conflicts(gid, second["merge_id"], resolution, True)["result"]
    assert resolved["status"] == "updated" and _state(gid)["work_base_sync_sha"] == tip


def test_backfill_leaves_a_group_unapplied_when_its_work_base_ref_pin_fails(proj, monkeypatch):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.services import git_service as svc
    gid = proj.group(32)                                     # NULL work_base_ref
    proj.commit(gid, "p.txt", "p\n")
    _forget_floor(gid)
    log_before = len(db_git.list_work_base_log(gid))

    def raising_pin(*_args, **_kwargs):
        raise RuntimeError("pin write failed")
    for failing_pin in (raising_pin, lambda *_a, **_k: None):   # raises / silently no-op
        with monkeypatch.context() as m:
            m.setattr(db_groups, "update_work_base_ref", failing_pin)
            report = svc.backfill_group_work_base(proj.pid, apply=True, actor="admin")
        row = next(r for r in report["groups"] if r["group_id"] == gid)
        assert row["verdict"] == "verified"                     # classification itself is fine
        assert row["applied"] is False and row["apply_error"] == "work_base_ref_pin_failed"
        assert gid in report["apply_failed"]
        assert db_groups.get_by_id(gid)["work_base_ref"] is None
        state = _state(gid)
        assert state["work_base_state"] is None and state["work_base_sha"] is None
        assert len(db_git.list_work_base_log(gid)) == log_before
        assert svc.collect_scope_changes(proj.pid, gid)["reason"] == "group_work_base_unverified"

    report = svc.backfill_group_work_base(proj.pid, apply=True, actor="admin")
    row = next(r for r in report["groups"] if r["group_id"] == gid)
    assert row["applied"] is True and gid not in report["apply_failed"]
    assert db_groups.get_by_id(gid)["work_base_ref"] == "main"
    assert _state(gid)["work_base_state"] == "verified"
    assert _scope(proj.pid, gid) == {"p.txt"}


# ── G / M: merge target ──────────────────────────────────────────────────────

def test_G_default_target_is_the_work_base_and_non_base_merge_is_recorded(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git_service import GitServiceError
    main_before = proj.base_git("rev-parse", "main")
    gid = proj.group(18, work_base="v0.2")
    proj.commit(gid, "feature.txt", "f\n")
    view = svc.get_finalize_state(gid)["state"]
    assert view["default_target"] == "v0.2" and view["default_target_unmerge_supported"] is False

    proj.ready(gid)
    out = svc.finalize(gid, "merge_only")["result"]        # no target: unattended default
    assert out["status"] == "merged" and out["target_branch"] == "v0.2"
    assert out["unmerge_supported"] is False
    record = db_git.session_context(db_git.get_session(_state(gid)["merge_id"]))["merge_target"]
    assert record["unmerge_supported"] is False and record["defaulted_to_work_base"] is True
    assert proj.base_git("rev-parse", "main") == main_before   # main untouched
    assert "feature.txt" in proj.base_git("ls-tree", "--name-only", "v0.2").split()

    with pytest.raises(GitServiceError) as exc:
        svc.unmerge(gid, out["merge_commit"])
    assert exc.value.code == "unmerge_unsupported_target"
    assert "revert_merge_on_target" in exc.value.details["recovery_options"]


def test_G_prime_preview_counts_work_base_commits_flowing_into_main(proj):
    from modules.flow_gate.services.git import merge_target
    gid = proj.group(19, work_base="v0.2")
    proj.commit(gid, "p.txt", "p\n")
    default = merge_target.preview_target(gid)
    assert default["target_branch"] == "v0.2" and default["warnings"] == [
        "unmerge_unsupported_for_non_base_target"]
    other = merge_target.preview_target(gid, "main")
    assert other["is_work_base"] is False and other["unmerge_supported"] is True
    assert other["incoming_work_base_commits"] == len(V02_ONLY)
    assert "target_differs_from_work_base" in other["warnings"]


def test_M_base_group_default_target_stays_main(proj):
    from modules.flow_gate.services import git_service as svc
    gid = proj.group(20)
    proj.commit(gid, "b.txt", "b\n")
    proj.ready(gid)
    out = svc.finalize(gid, "merge_only")["result"]
    assert out["target_branch"] == "main" and out["unmerge_supported"] is True


# ── I: terminal reopen ───────────────────────────────────────────────────────

def test_I_terminal_reopen_records_a_new_floor_after_the_merged_work(proj):
    from modules.flow_gate.services import git_service as svc
    gid = proj.group(21, work_base="v0.2")
    c1 = proj.commit(gid, "first.txt", "1\n")
    _ledger_commit(gid, proj.pid, 2, c1)
    proj.ready(gid)
    out = svc.finalize(gid, "merge_only")["result"]
    assert out["status"] == "merged"
    v02_tip = proj.base_git("rev-parse", "v0.2")
    svc.reopen_group_git(proj.pid, gid, terminal_commit_sha=c1)
    state = _state(gid)
    assert state["work_base_state"] == "verified", state
    assert state["work_base_sha"] == v02_tip
    assert _scope(proj.pid, gid) == set()
    proj.commit(gid, "second.txt", "2\n")
    assert _scope(proj.pid, gid) == {"second.txt"}


# ── K: remote tool default target ────────────────────────────────────────────

def test_K_remote_tools_default_to_the_work_base(proj):
    from modules.flow_gate.services import remote_tool_service as rts
    gid = proj.group(22, work_base="v0.2")
    assert rts._default_target_ref({"project": proj.pid, "group_id": gid}, proj.wt(gid)) == "v0.2"
    proj.origin_commit("v0.2", "ahead.txt", "a\n")
    proj.base_git("fetch", "origin")
    assert rts._default_target_ref({"project": proj.pid, "group_id": gid}, proj.wt(gid)) == "origin/v0.2"
    assert rts._default_target_ref({"project": proj.pid}, proj.base) == "main"


# ── L: static contract ───────────────────────────────────────────────────────

def test_L_scope_readers_never_measure_from_the_project_base():
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services import review_package_service as rps
    from modules.flow_gate.services.git import finalize

    for fn in (svc.collect_scope_changes, svc._group_diff_context, svc.read_group_file_diff,
               rps.build_review_package):
        source = inspect.getsource(fn)
        assert "resolve_scope_floor" in source or "scope_base_sha" in source, fn.__name__
        assert 'f"refs/heads/{base_branch}"' not in source, fn.__name__
    recover = inspect.getsource(finalize.group_update_untracked_recover)
    assert '"--no-ff", base_branch]' not in recover and "_worktree_start_point" in recover
    archive = inspect.getsource(inbox_routes)
    assert '["merge-base", base_branch, branch]' not in archive
