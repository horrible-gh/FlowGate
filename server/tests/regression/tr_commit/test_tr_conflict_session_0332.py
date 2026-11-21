"""Do revert/reapply conflicts avoid dead ends? (the final deferred part.)

The preceding three suites cover commits on approval, reverts on rewind, and reapplication
on moving forward. All passed, but **a revert conflict still ended the path**: the loop
erased the conflicted index with ``revert --quit`` + ``reset --hard HEAD``, leaving only
an instruction to resolve manually. Offering [다시 시도] would repeat the same answer,
while the one useful thing to inspect, the conflicted file, had just been erased.

FlowGate already resolves conflicts. A conflicting final merge leaves a session row,
the Git panel opens an inline editor on it, and an AI bound to its merge_id can resolve
it automatically. Everything depends on **one session row**, so this change makes TR
conflicts use that row too (088: the added ``kind`` and ``context`` columns).

Contracts:

* **Preserve conflicts.** No commit exists, but markers and the sequencer remain live;
  session/group state points to them exactly like a merge conflict.
* **Resolution is not a commit.** Even with no markers, stop at ``resolved_pending_review``.
  Keeping both sides often works for merges, but a revert asks to remove one TR's work
  against later edits. Marker-free content is not necessarily a correct revert. Committing
  an incorrect resolution could display "취소 완료" while leaving a tree that matches
  neither the old nor the new state.
* **Abort stays unchanged.** [되돌리기 중단] performs the existing destructive cleanup;
  this path still contains no ``git clean`` (0382).

Exercise all three real layers: create an actual conflict in Git, apply migrations to
real SQLite, and write the session row. Substitutes prove only that a function was called.
"""
from __future__ import annotations

import base64
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

from scratch_support import remove_tree, session_scratch

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault(
    "FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode()
)
os.environ.setdefault(
    "FLOWGATE_STORAGE_DIR", tempfile.mkdtemp(prefix="fg-tr-conflict-0332-")
)

_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import git_integration as db_git  # noqa: E402
from modules.flow_gate.db import tr_commit_ledger as db_ledger  # noqa: E402
from modules.flow_gate.services import git_service as svc  # noqa: E402
from group_lock_stub import stub_group_lock  # noqa: E402
from modules.flow_gate.services import tr_commit_service as trc  # noqa: E402
from modules.flow_gate.services.git_service import GitServiceError  # noqa: E402

_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not _GIT, reason="git binary unavailable")

_SCRATCH = session_scratch("tr-conflict-0332")

_PROJECT = "flowgate"
_GROUP = "flowgate.default.0332"
_TR_A = "flowgate.default.0332.0009-TR"
_TR_B = "flowgate.default.0332.0011-TR"

_SEED_SQL = f"""
INSERT OR IGNORE INTO projects(project_id, project_name, is_active, created_at, updated_at)
    VALUES('{_PROJECT}', 'FlowGate', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO groups(group_id, project_id, module, title, status, created_at, updated_at)
    VALUES('{_GROUP}', '{_PROJECT}', 'default', 'TR 커밋 충돌', 'OPEN',
           datetime('now'), datetime('now'));
INSERT OR IGNORE INTO documents(
        doc_id, project_id, module, group_id, type_code, seq, title, status,
        doc_review_status, created_at, updated_at)
    VALUES('{_TR_A}', '{_PROJECT}', 'default', '{_GROUP}', 'TR', 9,
           '커밋 포인트 생성 작업레포트', 'open', 'approved', datetime('now'), datetime('now')),
          ('{_TR_B}', '{_PROJECT}', 'default', '{_GROUP}', 'TR', 11,
           '되돌리기 취소 작업레포트', 'open', 'approved', datetime('now'), datetime('now'));
INSERT OR IGNORE INTO group_git_state(
        group_id, project_id, branch, worktree_registered, status, created_at, updated_at)
    VALUES('{_GROUP}', '{_PROJECT}', 'work', 1, 'waiting', datetime('now'), datetime('now'));
"""


class _SqliteStore:
    """The same minimal contract as the preceding three suites. Deliberately omit ``_sql``
    so the registered real SQL body is used (the lesson from 0393)."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def _execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def _execute_affected(self, sql, params=None):
        cur = self._conn.execute(sql, params or [])
        self._conn.commit()
        return cur.rowcount

    def _fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def transaction(self):
        yield self


@pytest.fixture
def real_store(migrated_sqlite_db):
    db_path = migrated_sqlite_db("tr_conflict_session_0332.db", seed_sql=_SEED_SQL)
    store = _SqliteStore(db_path)
    previous = db_connection.STORE
    db_connection.STORE = store
    try:
        yield store
    finally:
        db_connection.STORE = previous
        store._conn.close()


def _git(args, cwd):
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t",
    })
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout


def _commit(repo: Path, message: str) -> str:
    _git(["add", "-A"], repo)
    _git(["-c", "user.name=T", "-c", "user.email=t@t", "commit", "-m", message], repo)
    return _git(["rev-parse", "HEAD"], repo).strip()


@pytest.fixture
def repo():
    """A real Git worktree **outside** the repository, following the 0382 recurrence-prevention rule itself."""
    path = _SCRATCH / f"wt-{os.urandom(6).hex()}"
    path.mkdir(parents=True)
    _git(["init", "-b", "work"], path)
    (path / "f.txt").write_text("base\n", encoding="utf-8")
    _commit(path, "base")
    yield path
    remove_tree(path)


@pytest.fixture
def git_active(monkeypatch, repo):
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: {
        "enabled": 1, "base_branch": "main", "author_name": None, "author_email": None,
    })
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: {
        "worktree_registered": 1, "branch": "work", "status": "waiting",
    })
    monkeypatch.setattr(svc, "_project_name", lambda project_id: "flowgate")
    monkeypatch.setattr(svc, "src_root", lambda project_name, branch: repo)
    stub_group_lock(monkeypatch)
    return repo


def _ledger_commit(doc_id, sha, subject="s"):
    return db_ledger.record_commit(
        group_id=_GROUP, doc_id=doc_id, commit_sha=sha, commit_subject=subject,
    )


def _seed_return_point(store, docs):
    """One return-point set left by rewind: the snapshot defining the range ``cancel_retry`` resumes."""
    front = max(seq for _doc_id, seq in docs)
    store._execute(
        "INSERT INTO workflow_return_points(group_id, front_seq, created_at, updated_at) "
        "VALUES(?, ?, datetime('now'), datetime('now'))", [_GROUP, front],
    )
    rp_id = store._fetch_one(
        "SELECT id FROM workflow_return_points WHERE group_id = ?", [_GROUP]
    )["id"]
    for doc_id, seq in docs:
        store._execute(
            "INSERT INTO workflow_return_point_docs"
            "(return_point_id, doc_id, seq, prev_status, fingerprint) "
            "VALUES(?, ?, ?, 'approved', 'fp')",
            [rp_id, doc_id, seq],
        )
    return rp_id


def _stored_status(store):
    return store._fetch_one(
        "SELECT status, merge_id FROM group_git_state WHERE group_id = ?", [_GROUP]
    )


def _parked_cancel(repo):
    """changed f.txt to A, then a person overlaid C. Cancellation conflicts."""
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: A")
    row = _ledger_commit(_TR_A, sha_a, "0009-TR: A")
    (repo / "f.txt").write_text("C\n", encoding="utf-8")
    _commit(repo, "manual fix")
    result = trc.cancel_tr_commits(_GROUP, [_TR_A])
    parked = result["conflict_session"]
    assert parked, "이 시나리오는 반드시 충돌로 끝나야 한다"
    return row, parked


def _parked_reapply(repo):
    """After cancellation completed, a person edited the same line. Reapplication conflicts."""
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: A")
    row = _ledger_commit(_TR_A, sha_a, "0009-TR: A")
    assert trc.cancel_tr_commits(_GROUP, [_TR_A])["canceled"]
    (repo / "f.txt").write_text("C\n", encoding="utf-8")
    _commit(repo, "manual fix")
    result = trc.restore_for_return(_GROUP, [_TR_A])
    parked = result["conflict_session"]
    assert parked, "이 시나리오는 반드시 충돌로 끝나야 한다"
    return row, parked


# ── 1. Preserve conflicts ──────────────────────────────────────────────────

@needs_git
def test_a_parked_conflict_serves_the_payload_the_merge_editor_already_reads(
    real_store, git_active, repo,
):
    """The existing merge-conflict query answers unchanged, bringing UI, tools, and AI along.

    Only two additive differences: the conflict kind (``kind``), and what is being
    reverted (``tr_conflict``). The latter is None for merge sessions.
    """
    _row, parked = _parked_cancel(repo)

    payload = svc.list_conflicts(_GROUP, parked["merge_id"])

    assert payload["kind"] == "tr_revert"
    assert [f["path"] for f in payload["files"]] == ["f.txt"]
    assert payload["files"][0]["conflict_count"] == 1
    assert "<<<<<<<" in payload["files"][0]["content"]
    assert payload["tr_conflict"]["doc_code"] == "0009-TR"
    assert payload["tr_conflict"]["review_state"] == "open"
    # Revert conflicts live in the group worktree; the base checkout would not contain this file.
    assert payload["files"][0]["content"] == (repo / "f.txt").read_text(encoding="utf-8")


@needs_git
def test_the_group_points_at_the_parked_conflict_the_same_way_a_merge_does(
    real_store, git_active, repo,
):
    """``status='conflict'`` plus ``merge_id``. The panel opens the editor from those two values;
    inventing a new state would have required a new UI."""
    _row, parked = _parked_cancel(repo)

    state = _stored_status(real_store)
    assert (state["status"], state["merge_id"]) == ("conflict", parked["merge_id"])
    session = db_git.get_session(parked["merge_id"])
    assert session["status"] == "open"
    assert db_git.session_kind(session) == "tr_revert"
    # The session retains where to return: this was a rewind, not a merge.
    assert db_git.session_context(session)["prev_status"] == "waiting"


@needs_git
def test_the_panel_summary_carries_the_parked_conflict(real_store, git_active, repo):
    """Do not make the panel guess why the group is blocked. ``git_busy`` cannot distinguish
    another Git operation from this group's conflict, so return the session itself."""
    _row, parked = _parked_cancel(repo)

    summary = trc.group_commit_summary(_GROUP)

    assert summary["conflict_session"]["merge_id"] == parked["merge_id"]
    assert summary["conflict_session"]["kind"] == "tr_revert"
    assert summary["conflict_session"]["remaining"] == ["f.txt"]
    assert summary["conflict_session"]["review_state"] == "open"


# ── 2. Resolution is not a commit ──────────────────────────────────────────

@needs_git
def test_resolving_every_file_stops_at_review_and_makes_no_commit(
    real_store, git_active, repo,
):
    """The central assertion of this suite.

    Removing all markers does not establish that the revert is correct. A person presses
    [병합] at the end of a merge, but a revert could previously commit immediately here.
    """
    row, parked = _parked_cancel(repo)
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    out = svc.resolve_conflicts(
        _GROUP, parked["merge_id"],
        [{"path": "f.txt", "content": "resolved\n"}], True,
    )

    assert out["result"]["status"] == "resolved_pending_review"
    assert out["result"]["merge_commit"] is None
    # No commit; the session remains live.
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    session = db_git.get_session(parked["merge_id"])
    assert session["status"] == "open"
    assert db_git.session_context(session)["review_state"] == "resolved"
    # The ledger makes no claim yet: cancellation requires a commit.
    assert db_ledger.get_by_id(row["id"])["state"] == "live"


@needs_git
def test_a_marker_left_behind_is_refused_before_anything_is_written(
    real_store, git_active, repo,
):
    """E12's all-or-nothing rule applies to this kind as well."""
    _row, parked = _parked_cancel(repo)
    before = (repo / "f.txt").read_text(encoding="utf-8")

    with pytest.raises(GitServiceError) as exc:
        svc.resolve_conflicts(
            _GROUP, parked["merge_id"],
            [{"path": "f.txt", "content": "<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> z\n"}],
            True,
        )

    assert exc.value.code == "conflict_markers_remain"
    assert (repo / "f.txt").read_text(encoding="utf-8") == before


@needs_git
def test_the_commit_is_refused_while_the_session_is_still_open(
    real_store, git_active, repo,
):
    """Reject a commit button press unless the session awaits review: ordering is the contract."""
    _row, parked = _parked_cancel(repo)

    with pytest.raises(GitServiceError) as exc:
        trc.commit_conflict_resolution(_GROUP, parked["merge_id"])

    assert exc.value.code == "conflict_markers_remain"
    assert db_git.get_session(parked["merge_id"])["status"] == "open"


# ── 3. A person's button press completes it ────────────────────────────────

@needs_git
def test_the_commit_button_finishes_the_cancel_and_writes_the_ledger(
    real_store, git_active, repo,
):
    """Resolve → review → commit. Use the commit subject chosen before the conflict;
    only then does the ledger become canceled."""
    row, parked = _parked_cancel(repo)
    svc.resolve_conflicts(
        _GROUP, parked["merge_id"], [{"path": "f.txt", "content": "resolved\n"}], True,
    )

    out = trc.commit_conflict_resolution(_GROUP, parked["merge_id"])

    assert out["result"]["status"] == "committed"
    assert out["result"]["ledger_written"] is True
    sha = out["result"]["commit"]
    assert _git(["log", "-1", "--format=%s"], repo).strip() == 'Revert "0009-TR: A"'
    assert _git(["rev-parse", "HEAD"], repo).strip() == sha
    # The resolved content was committed unchanged, and the sequencer closed.
    assert (repo / "f.txt").read_text(encoding="utf-8") == "resolved\n"
    assert svc._revert_in_flight(repo) is False
    assert _git(["status", "--porcelain"], repo).strip() == ""
    # The ledger holds this as the cancellation commit, preserving the original commit hash.
    after = db_ledger.get_by_id(row["id"])
    assert (after["state"], after["cancel_commit"]) == ("canceled", sha)
    assert after["commit_sha"] == row["commit_sha"]
    # The session closes and the group returns to its previous state.
    assert db_git.get_session(parked["merge_id"])["status"] == "done"
    assert _stored_status(real_store)["status"] == "waiting"


@needs_git
def test_the_reapply_direction_takes_the_same_road_and_ends_in_a_new_live_row(
    real_store, git_active, repo,
):
    """Reapplication uses the same session, editing, and button. Only the final ledger row differs:
    it adds a new live row instead of restoring the canceled row."""
    row, parked = _parked_reapply(repo)
    assert parked["kind"] == "tr_reapply"
    svc.resolve_conflicts(
        _GROUP, parked["merge_id"], [{"path": "f.txt", "content": "restored\n"}], True,
    )

    out = trc.commit_conflict_resolution(_GROUP, parked["merge_id"])

    assert out["result"]["ledger_written"] is True
    assert _git(["log", "-1", "--format=%s"], repo).strip() == 'Reapply "0009-TR: A"'
    assert (repo / "f.txt").read_text(encoding="utf-8") == "restored\n"
    # The canceled row stays canceled, with a new reapplication row above it.
    rows = db_ledger.list_by_group(_GROUP)
    assert [r["state"] for r in rows] == ["live", "canceled"]
    assert rows[0]["restored_from_id"] == row["id"]
    assert rows[0]["commit_sha"] == out["result"]["commit"]


@needs_git
def test_a_resolution_that_changes_nothing_is_recorded_not_treated_as_a_git_error(
    real_store, git_active, repo,
):
    """Keeping our side unchanged is a valid resolution, and AI often chooses it.

    There is then nothing to stage. An empty commit is history noise,
    but this is not a Git error: the desired result already exists in the tree.
    Record it exactly like an empty revert in the cancellation loop: canceled without a commit.
    """
    row, parked = _parked_cancel(repo)
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    svc.resolve_conflicts(
        _GROUP, parked["merge_id"], [{"path": "f.txt", "content": "C\n"}], True,
    )
    out = trc.commit_conflict_resolution(_GROUP, parked["merge_id"])

    assert out["result"]["status"] == "empty"
    assert out["result"]["commit"] is None
    assert out["result"]["ledger_written"] is True
    # No commit and no tree changes; the sequencer closed.
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    assert svc._revert_in_flight(repo) is False
    assert _git(["status", "--porcelain"], repo).strip() == ""
    after = db_ledger.get_by_id(row["id"])
    assert (after["state"], after["cancel_commit"], after["cancel_reason"]) == (
        "canceled", None, "empty_revert",
    )
    assert db_git.get_session(parked["merge_id"])["status"] == "done"


@needs_git
def test_committing_the_resolution_carries_on_with_the_rest_of_the_run(
    real_store, git_active, repo,
):
    """Resolving one conflict should not require the person to request the remaining work again.

    Rewind requests removal of a **sequence** of commits. A conflict stops at that point,
    leaving lower items ``not_attempted``, as ordered work requires. But ``retryable=False``
    meant no UI offered a second press: after resolution, no button explained the half-rewound
    tree. Therefore continue the remaining sequence here once the commit completes.
    """
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: a")
    row_a = _ledger_commit(_TR_A, sha_a, "0009-TR: a")
    (repo / "f.txt").write_text("X\n", encoding="utf-8")
    sha_b = _commit(repo, "0011-TR: X")
    row_b = _ledger_commit(_TR_B, sha_b, "0011-TR: X")
    # A person edited the line touched by the latest commit; canceling the earlier implementation conflicts.
    (repo / "f.txt").write_text("Y\n", encoding="utf-8")
    _commit(repo, "manual fix")
    # The exact state left by rewind: one return-point set and two rewound documents in pending_review.
    # Resumption touches only the range this snapshot defines; removing live commits outside that range
    # would rewind work the person never requested. Skip steps reapproved in the meantime.
    _seed_return_point(real_store, ((_TR_A, 9), (_TR_B, 11)))
    real_store._execute(
        "UPDATE documents SET doc_review_status = 'pending_review' "
        "WHERE doc_id IN (?, ?)", [_TR_A, _TR_B],
    )

    result = trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])
    parked = result["conflict_session"]
    assert parked and parked["doc_code"] == "0011-TR"
    # The lower row remains untouched and is the target to resume.
    assert [(s["doc_code"], s["reason"]) for s in result["skipped"]] == [
        ("0011-TR", "conflict"), ("0009-TR", "not_attempted"),
    ]
    assert db_ledger.get_by_id(row_a["id"])["state"] == "live"

    svc.resolve_conflicts(
        _GROUP, parked["merge_id"], [{"path": "f.txt", "content": "Y resolved\n"}], True,
    )
    out = trc.commit_conflict_resolution(_GROUP, parked["merge_id"])

    # The conflicting row was committed as confirmed by the person,
    assert db_ledger.get_by_id(row_b["id"])["state"] == "canceled"
    # and the lower row was then canceled within the same call.
    continued = out["result"]["continued"]
    assert [c["doc_code"] for c in continued["canceled"]] == ["0009-TR"]
    after_a = db_ledger.get_by_id(row_a["id"])
    assert after_a["state"] == "canceled"
    assert after_a["cancel_commit"]
    # It was actually removed from the tree, not merely claimed by the ledger.
    assert not (repo / "a.py").exists()
    assert svc._revert_in_flight(repo) is False


@needs_git
def test_giving_up_on_a_reapply_leaves_the_row_canceled_and_the_tree_clean(
    real_store, git_active, repo,
):
    """Aborting must leave things as though nothing happened: abandoning reapplication
    keeps that step canceled."""
    row, parked = _parked_reapply(repo)
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    trc.abort_conflict_resolution(_GROUP, parked["merge_id"])

    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    assert svc._revert_in_flight(repo) is False
    assert _git(["status", "--porcelain"], repo).strip() == ""
    after = db_ledger.get_by_id(row["id"])
    assert after["state"] == "canceled"
    assert json.loads(after["cancel_attempt_log"])[-1]["reason"] == "conflict_abandoned"
    assert db_git.get_session(parked["merge_id"])["status"] == "aborted"
    assert _stored_status(real_store)["status"] == "waiting"


# ── 4. Cleanup reclaims abandoned sessions ─────────────────────────────────

@needs_git
def test_the_sweep_closes_a_session_whose_revert_is_no_longer_in_flight(
    real_store, git_active, repo,
):
    """Cleanup previously checked MERGE_HEAD in the base checkout. TR conflicts have neither
    that marker nor the same worktree; without a separate check every open session
    would have been closed as orphaned.

    Also assert the converse: leave an ongoing revert untouched.
    """
    _row, parked = _parked_cancel(repo)

    svc.merge_session_sweep()
    assert db_git.get_session(parked["merge_id"])["status"] == "open"

    # A person finished the revert outside FlowGate; nothing remains to reference.
    _git(["revert", "--quit"], repo)
    _git(["reset", "--hard", "HEAD"], repo)

    svc.merge_session_sweep()

    assert db_git.get_session(parked["merge_id"])["status"] == "aborted"
    assert _stored_status(real_store)["status"] == "waiting"


# ── 5. Tell the AI which question it must answer ───────────────────────────

def test_the_ai_mention_tells_a_revert_apart_from_a_merge(monkeypatch):
    """Identical conflict markers can pose different questions.

    A merge asks to combine branches, so retaining both sides often works. Applying
    that habit to a revert silently restores the commit meant to be removed while
    claiming resolution. Only this prompt paragraph varies by kind; chunk payloads,
    endpoints, and completion criteria are shared.
    """
    # Deferred import: this module loads app settings (ALLOWED_ORIGIN/CONTEXT), so importing
    # it during collection would fail the whole suite with a collection error.
    # Self-check Windows worker may have no USERNAME; PyMySQL imports getpass.
    monkeypatch.setenv("USERNAME", "FlowGateTest")
    from modules.flow_gate.api import token_routes

    merge = token_routes._conflict_task_section("merge", {})
    revert = token_routes._conflict_task_section(
        "tr_revert", {"doc_code": "0009-TR", "subject": "0009-TR: A"}
    )
    reapply = token_routes._conflict_task_section(
        "tr_reapply", {"doc_code": "0009-TR", "subject": "0009-TR: A"}
    )

    assert "NOT a branch merge" not in merge
    for text in (revert, reapply):
        assert "NOT a branch merge" in text
        assert "Do not resolve it by keeping both sides." in text
        assert "0009-TR" in text
        # The AI must know that a person commits; otherwise its completion report is wrong.
        assert "resolved_pending_review" in text
    assert "UNDOING" in revert
    assert "PUTTING BACK" in reapply


# ── 6. the review the earlier implementation(b): execution evidence for recommendation 2-B's seam ──

def test_open_tr_conflict_session_reads_unmerged_paths_through_the_facade_seam(
    monkeypatch, tmp_path
):
    """`_unmerged_paths` must resolve through `_gs.` at call time, not an early-bound
    copy imported at conflict.py's top (removed in §3.2). The sentinel here is "no
    conflict files" ([]) -- before the fix the bare name ran the REAL `_unmerged_paths`,
    which calls `_run_git` once (``diff --name-only --diff-filter=U``); the return value
    alone cannot tell the two states apart (both are None), so this test asserts the
    `_run_git` call log instead.
    """
    calls: list[list[str]] = []

    class _FakeProc:
        def __init__(self):
            self.returncode = 0
            self.stdout = ""

    def fake_run_git(args, cwd=None, timeout=None):
        calls.append(list(args))
        return _FakeProc()

    monkeypatch.setattr(svc, "_unmerged_paths", lambda _p: [])
    monkeypatch.setattr(svc, "_run_git", fake_run_git)

    result = svc.open_tr_conflict_session(
        {"wt_path": tmp_path},
        kind="tr_revert",
        group_id="flowgate.default.0332",
        ledger_row_id=1,
        doc_id="flowgate.default.0332.0009-TR",
        doc_code="0009-TR",
        target_sha="a" * 40,
        original_sha=None,
        subject="dummy",
        body="dummy",
    )

    assert result is None
    assert calls == [], (
        f"expected the facade-patched empty _unmerged_paths to short-circuit before any "
        f"git call, but _run_git was invoked: {calls!r} -- this means "
        "open_tr_conflict_session is reading an early-bound _unmerged_paths instead of "
        "_gs._unmerged_paths"
    )
