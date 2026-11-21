"""Does rewind revert TR commits with cancellation commits? (latter part of the requirement.)

Following the earlier implementation, exercise all three layers for real. Reverting is **actual Git behavior**:
substitutes prove only that revert was called, not whether conflicts remain conflicts
or empty reverts avoid empty commits. Those require a real repository.

1. **Ledger (085 cancellation columns)**: run ``mark_canceled``, ``record_cancel_attempt``,
   and ``record_block`` on fully migrated SQLite. Rows are never deleted.
2. **Revert (Git)**: cancel in a real worktree and check cancellation commits, restoration
   and stopping on conflicts, and tool artifacts not blocking cancellation.
3. **Decision table **: check each G1–G11 code against the closed set and
   `retryable` table in the plan

The governing contract: **rewind always stands; cancellation is best-effort but must
report everything it could not do**.
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
os.environ.setdefault(
    "FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode()
)
os.environ.setdefault(
    "FLOWGATE_STORAGE_DIR", tempfile.mkdtemp(prefix="fg-tr-cancel-0332-")
)

_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import git_integration as db_git  # noqa: E402
from modules.flow_gate.db import tr_commit_ledger as db_ledger  # noqa: E402
from modules.flow_gate.services import git_service as svc  # noqa: E402
from group_lock_stub import stub_group_lock  # noqa: E402
from modules.flow_gate.services import tr_commit_service as trc  # noqa: E402
from modules.flow_gate.services import workflow_rework_service as rework  # noqa: E402

_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not _GIT, reason="git binary unavailable")

_SCRATCH = session_scratch("tr-cancel-0332")

_PROJECT = "flowgate"
_GROUP = "flowgate.default.0332"
_TR_A = "flowgate.default.0332.0009-TR"
_TR_B = "flowgate.default.0332.0011-TR"
_D_DOC = "flowgate.default.0332.0005-D"

_SEED_SQL = f"""
INSERT OR IGNORE INTO projects(project_id, project_name, is_active, created_at, updated_at)
    VALUES('{_PROJECT}', 'FlowGate', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO groups(group_id, project_id, module, title, status, created_at, updated_at)
    VALUES('{_GROUP}', '{_PROJECT}', 'default', 'TR 커밋 취소', 'OPEN',
           datetime('now'), datetime('now'));
INSERT OR IGNORE INTO documents(
        doc_id, project_id, module, group_id, type_code, seq, title, status,
        created_at, updated_at)
    VALUES('{_D_DOC}', '{_PROJECT}', 'default', '{_GROUP}', 'D', 5,
           'TR 커밋 취소 기본설계', 'open', datetime('now'), datetime('now')),
          ('{_TR_A}', '{_PROJECT}', 'default', '{_GROUP}', 'TR', 9,
           '커밋 포인트 생성 작업레포트', 'open', datetime('now'), datetime('now')),
          ('{_TR_B}', '{_PROJECT}', 'default', '{_GROUP}', 'TR', 11,
           '되돌리기 취소 작업레포트', 'open', datetime('now'), datetime('now'));
INSERT OR IGNORE INTO group_git_state(
        group_id, project_id, branch, worktree_registered, status, created_at, updated_at)
    VALUES('{_GROUP}', '{_PROJECT}', 'work', 1, 'none', datetime('now'), datetime('now'));
"""


class _SqliteStore:
    """The same minimal contract as the earlier implementation. Deliberately omit ``_sql`` so the registered real SQL
    body is used (the lesson from 0393)."""

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
    db_path = migrated_sqlite_db("tr_commit_cancel_0332.db", seed_sql=_SEED_SQL)
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
    """Tell the server that this group has Git enabled and uses this repo as its worktree."""
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: {
        "enabled": 1, "base_branch": "main", "author_name": None, "author_email": None,
    })
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: {
        "worktree_registered": 1, "branch": "work", "status": "none",
    })
    monkeypatch.setattr(svc, "_project_name", lambda project_id: "flowgate")
    monkeypatch.setattr(svc, "src_root", lambda project_name, branch: repo)
    stub_group_lock(monkeypatch)
    return repo


# ── 1. Ledger cancellation columns (DB0008 §4.3–§4.5) ─────────────────────────

def test_a_cancel_writes_onto_the_row_it_cancels_and_never_deletes_it(real_store):
    row = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_A, commit_sha="a" * 40, commit_subject="0009-TR: 제목",
    )

    assert db_ledger.mark_canceled(row["id"], cancel_commit="c" * 40) is True

    after = db_ledger.get_by_id(row["id"])
    assert after["state"] == "canceled"
    assert after["cancel_commit"] == "c" * 40
    assert after["canceled_at"]
    # Preserve the original commit hash: the record says this commit existed and was canceled.
    assert after["commit_sha"] == "a" * 40


def test_a_second_cancel_of_the_same_row_reports_it_did_not_win(real_store):
    """DB0008 §4-3's ``WHERE state='live'``. ``_execute`` provides no rowcount, so the service
    reads back to decide. False lets the caller reclassify as ``already_canceled``
    and prevents reverting a revert."""
    row = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_A, commit_sha="a" * 40, commit_subject="x",
    )
    db_ledger.mark_canceled(row["id"], cancel_commit="c" * 40)

    assert db_ledger.mark_canceled(row["id"], cancel_commit="d" * 40) is False
    assert db_ledger.get_by_id(row["id"])["cancel_commit"] == "c" * 40
    assert db_ledger.is_canceled(row["id"]) is True


def test_an_empty_revert_is_canceled_without_a_cancel_commit(real_store):
    row = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_A, commit_sha="a" * 40, commit_subject="x",
    )

    assert db_ledger.mark_canceled(
        row["id"], cancel_commit=None, reason="empty_revert"
    ) is True

    after = db_ledger.get_by_id(row["id"])
    assert (after["state"], after["cancel_commit"], after["cancel_reason"]) == (
        "canceled", None, "empty_revert",
    )


def test_a_failed_attempt_is_logged_and_the_row_stays_live(real_store):
    """The response groups failures under ``conflict`` ('s closed set).
    Record what actually failed here so it remains available for later inspection."""
    row = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_A, commit_sha="a" * 40, commit_subject="x",
    )

    db_ledger.record_cancel_attempt(row["id"], failed_reason="revert_conflict")
    db_ledger.record_cancel_attempt(row["id"], failed_reason="timeout")

    after = db_ledger.get_by_id(row["id"])
    assert after["state"] == "live"
    log = json.loads(after["cancel_attempt_log"])
    assert [entry["reason"] for entry in log] == ["revert_conflict", "timeout"]
    assert all(entry["at"] for entry in log)


def test_a_group_level_block_writes_only_the_three_diagnostic_columns(real_store):
    db_ledger.record_block(_GROUP, "dirty_worktree", "dirty_worktree")

    state = real_store._fetch_one(
        "SELECT * FROM group_git_state WHERE group_id = ?", [_GROUP]
    )
    assert state["last_cancel_block_reason"] == "dirty_worktree"
    assert state["last_cancel_block_sub"] == "dirty_worktree"
    assert state["last_cancel_block_at"]
    # Recording cancellation diagnostics does not change the group's Git state itself.
    assert state["status"] == "none"


def test_a_block_on_a_group_without_git_state_is_silent(real_store):
    """A group that never enabled Git has nowhere to store diagnostics (DB0008 §5-2). Quietly affect zero rows."""
    db_ledger.record_block("flowgate.default.9999", "git_inactive", "integration_disabled")


def test_the_preview_query_carries_commits_only_in_workflow_order(real_store):
    db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_B, commit_sha="b" * 40, commit_subject="11번",
    )
    db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_A, commit_sha="a" * 40, commit_subject="9번",
    )
    db_ledger.record_no_commit(group_id=_GROUP, doc_id=_TR_A, skip_reason="no_changes")

    rows = db_ledger.commit_rows_by_group(_GROUP)

    # Only rows with commits, in ascending document seq (the dialog draws steps in order).
    assert [(r["seq"], r["commit_sha"][:1]) for r in rows] == [(9, "a"), (11, "b")]


# ── 2. Reverting in a real worktree ─────────────────────────────────────────

def _ledger_commit(group_id, doc_id, sha, subject="s"):
    return db_ledger.record_commit(
        group_id=group_id, doc_id=doc_id, commit_sha=sha, commit_subject=subject,
    )


@needs_git
def test_the_cancel_lays_one_revert_commit_per_tr_newest_first(real_store, git_active, repo):
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: a")
    _ledger_commit(_GROUP, _TR_A, sha_a, "0009-TR: a")
    (repo / "b.py").write_text("b = 1\n", encoding="utf-8")
    sha_b = _commit(repo, "0011-TR: b")
    _ledger_commit(_GROUP, _TR_B, sha_b, "0011-TR: b")

    result = trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])

    assert result["attempted"] is True
    assert result["blocked_reason"] is None
    assert result["stopped_reason"] is None
    assert result["skipped"] == []
    # Newest first. No batching: each TR also receives its own cancellation commit.
    assert [line["doc_code"] for line in result["canceled"]] == ["0011-TR", "0009-TR"]
    assert not (repo / "a.py").exists() and not (repo / "b.py").exists()
    subjects = _git(["log", "-2", "--pretty=%s"], repo).split("\n")
    assert subjects[0] == 'Revert "0009-TR: a"'
    assert subjects[1] == 'Revert "0011-TR: b"'
    # The body identifies the reverted commit by its full hash, allowing lookup from either side.
    assert sha_a in _git(["log", "-1", "--pretty=%b"], repo)
    assert f"(group {_GROUP})" in _git(["log", "-1", "--pretty=%b"], repo)
    # Both ledger rows become canceled, and neither row disappears.
    rows = db_ledger.list_by_group(_GROUP)
    assert [r["state"] for r in rows] == ["canceled", "canceled"]
    assert all(r["cancel_commit"] for r in rows)


@needs_git
def test_a_conflict_is_parked_as_a_session_and_leaves_the_rest_untried(
    real_store, git_active, repo,
):
    """changed the outcome here.

    Previously a conflict immediately erased evidence with ``revert --quit`` + ``reset --hard``,
    leaving only an instruction to resolve it manually. Now it leaves one conflict-session row:
    the group has ``status='conflict'`` and ``merge_id``, and the existing merge-conflict inline
    editor and AI invocation open that same row. Stopping and leaving remaining work untouched
    are unchanged.
    """
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: A")
    _ledger_commit(_GROUP, _TR_A, sha_a, "0009-TR: A")
    (repo / "f.txt").write_text("B\n", encoding="utf-8")
    sha_b = _commit(repo, "0011-TR: B")
    row_b = _ledger_commit(_GROUP, _TR_B, sha_b, "0011-TR: B")
    # An edit outside the ledger touches the same line again; reverting B conflicts here.
    (repo / "f.txt").write_text("C\n", encoding="utf-8")
    _commit(repo, "manual fix")
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    result = trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])

    assert result["attempted"] is True
    assert result["canceled"] == []
    assert result["stopped_reason"] == "conflict"
    assert [(s["doc_code"], s["reason"]) for s in result["skipped"]] == [
        ("0011-TR", "conflict"), ("0009-TR", "not_attempted"),
    ]
    # Still offer no [다시 시도] that resends the same request, because the answer would be unchanged.
    # A separate path can now produce a different answer: commit after resolution.
    assert result["retryable"] is False

    # The conflict remains live: no commit, but markers remain in the tree and the sequencer is open.
    parked = result["conflict_session"]
    assert parked and parked["kind"] == "tr_revert"
    assert parked["files"] == ["f.txt"]
    assert parked["doc_code"] == "0011-TR"
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    assert "<<<<<<<" in (repo / "f.txt").read_text(encoding="utf-8")
    assert svc._revert_in_flight(repo) is True

    # The session uses the same table as merge conflicts, and the group points to conflict in the same way.
    # Read the saved group_git_state row directly: ``git_active`` replaced ``db_git.get_state``
    # with a fixed dict, so that function cannot show the value just written.
    session = db_git.get_session(parked["merge_id"])
    assert (session["status"], db_git.session_kind(session)) == ("open", "tr_revert")
    state = real_store._fetch_one(
        "SELECT status, merge_id FROM group_git_state WHERE group_id = ?", [_GROUP]
    )
    assert (state["status"], state["merge_id"]) == ("conflict", parked["merge_id"])

    # The failed row remains live, and the ledger records what failed.
    after = db_ledger.get_by_id(row_b["id"])
    assert after["state"] == "live"
    assert json.loads(after["cancel_attempt_log"])[0]["reason"] == "revert_conflict"


@needs_git
def test_giving_up_on_a_parked_conflict_restores_the_worktree_exactly_as_before(
    real_store, git_active, repo,
):
    """The previous destructive cleanup moved behind a button rather than disappearing.

    [되돌리기 중단] still performs ``revert --quit`` + ``reset --hard HEAD``;
    this path still never runs ``git clean`` (0382). Ledger rows remain unchanged:
    abandoning cancellation leaves the commit live.
    """
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: A")
    row_a = _ledger_commit(_GROUP, _TR_A, sha_a, "0009-TR: A")
    (repo / "f.txt").write_text("C\n", encoding="utf-8")
    _commit(repo, "manual fix")
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    parked = trc.cancel_tr_commits(_GROUP, [_TR_A])["conflict_session"]
    assert parked
    # An untracked file placed in the worktree after the session opened. Deleting it on abort repeats 0382.
    (repo / "debris.tmp").write_text("x\n", encoding="utf-8")

    trc.abort_conflict_resolution(_GROUP, parked["merge_id"])

    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    assert (repo / "f.txt").read_text(encoding="utf-8") == "C\n"
    assert svc._revert_in_flight(repo) is False
    assert (repo / "debris.tmp").exists()
    assert db_git.get_session(parked["merge_id"])["status"] == "aborted"
    state = real_store._fetch_one(
        "SELECT status FROM group_git_state WHERE group_id = ?", [_GROUP]
    )
    assert state["status"] != "conflict"
    after = db_ledger.get_by_id(row_a["id"])
    assert after["state"] == "live"
    assert json.loads(after["cancel_attempt_log"])[-1]["reason"] == "conflict_abandoned"


@needs_git
def test_an_empty_revert_is_reported_as_already_canceled_without_a_new_commit(
    real_store, git_active, repo,
):
    """The same content was already reverted through another path. Do not add an empty commit (same reason as the design K3)."""
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: A")
    row = _ledger_commit(_GROUP, _TR_A, sha_a, "0009-TR: A")
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    _commit(repo, "manual undo")
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    result = trc.cancel_tr_commits(_GROUP, [_TR_A])

    assert result["canceled"] == []
    assert [(s["doc_code"], s["reason"]) for s in result["skipped"]] == [
        ("0009-TR", "already_canceled"),
    ]
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    after = db_ledger.get_by_id(row["id"])
    assert (after["state"], after["cancel_commit"], after["cancel_reason"]) == (
        "canceled", None, "empty_revert",
    )


@needs_git
def test_an_already_canceled_tr_is_skipped_not_reverted_again(real_store, git_active, repo):
    """K6: pressing rewind twice must not revert the revert."""
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: a")
    _ledger_commit(_GROUP, _TR_A, sha_a, "0009-TR: a")

    first = trc.cancel_tr_commits(_GROUP, [_TR_A])
    head_after_first = _git(["rev-parse", "HEAD"], repo).strip()
    second = trc.cancel_tr_commits(_GROUP, [_TR_A])

    assert len(first["canceled"]) == 1
    # On the second call, the ledger is already canceled, leaving no targets; G1 finishes quietly.
    assert second["attempted"] is True
    assert second["canceled"] == [] and second["skipped"] == []
    assert second["blocked_reason"] is None
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_after_first


@needs_git
def test_tool_debris_does_not_block_a_cancel(real_store, git_active, repo):
    """tool artifacts are files deliberately left behind by TR commits and normally exist.
    Reusing finalize's ``_dirty()`` unchanged would make this group impossible to cancel."""
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: a")
    _ledger_commit(_GROUP, _TR_A, sha_a, "0009-TR: a")
    debris = repo / "server" / ".test-tmp-0332"
    debris.mkdir(parents=True)
    (debris / "junk.txt").write_text("junk\n", encoding="utf-8")

    result = trc.cancel_tr_commits(_GROUP, [_TR_A])

    assert len(result["canceled"]) == 1
    # Cancellation neither deletes nor stages tool artifacts.
    assert (debris / "junk.txt").exists()


@needs_git
def test_an_uncommitted_edit_blocks_the_cancel_and_asks_for_a_retry(
    real_store, git_active, repo,
):
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: a")
    _ledger_commit(_GROUP, _TR_A, sha_a, "0009-TR: a")
    (repo / "f.txt").write_text("hand edit\n", encoding="utf-8")   # Uncommitted changes in a tracked file.
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    result = trc.cancel_tr_commits(_GROUP, [_TR_A])

    assert result["attempted"] is False
    assert result["blocked_reason"] == "dirty_worktree"
    assert result["retryable"] is True          # Clean up, then [다시 시도].
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    state = real_store._fetch_one(
        "SELECT * FROM group_git_state WHERE group_id = ?", [_GROUP]
    )
    assert state["last_cancel_block_sub"] == "dirty_worktree"


@needs_git
def test_commits_missing_from_this_worktree_cancel_nothing_at_all(
    real_store, git_active, repo,
):
    """G11 fails closed: no cancellation is safer than partial cancellation."""
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: a")
    _ledger_commit(_GROUP, _TR_A, sha_a, "0009-TR: a")
    _ledger_commit(_GROUP, _TR_B, "b" * 40, "0011-TR: 이 트리에 없는 커밋")
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    result = trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])

    assert result["attempted"] is False
    assert result["blocked_reason"] == "no_worktree"
    assert result["retryable"] is False
    # Even commit a in this tree remains untouched.
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    assert [r["state"] for r in db_ledger.list_by_group(_GROUP)] == ["live", "live"]


# ── 3. Decision table ──────────────────────────────

def test_a_group_that_never_committed_ends_quietly_not_on_an_error(real_store):
    """G1 precedes the gates. Rewinding a documents-only group must end with "취소할 커밋이 없었습니다",
    not a ``git_inactive`` result screen (note 1)."""
    result = trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])

    assert result == {
        "attempted": True, "blocked_reason": None,
        "canceled": [], "skipped": [], "terminal_reopened": [],
        "stopped_reason": None, "retryable": False,
        "conflict_session": None,
    }


@pytest.mark.parametrize(
    "state, expect_reason, expect_sub, expect_retryable",
    [
        ({"worktree_registered": 1, "branch": "work", "status": "merged"},
         "already_merged", "already_merged", False),
        ({"worktree_registered": 1, "branch": "work", "status": "pushed"},
         "already_merged", "already_merged", False),
        ({"worktree_registered": 1, "branch": "work", "status": "merging"},
         "git_busy", "merge_in_flight", True),
        ({"worktree_registered": 0, "branch": "work", "status": "none"},
         "no_worktree", "worktree_unregistered", False),
    ],
)
def test_the_group_state_gates_answer_in_the_documented_order(
    real_store, git_active, repo, monkeypatch,
    state, expect_reason, expect_sub, expect_retryable,
):
    """G5 must precede G7 so a merged group is not collapsed into ``no_worktree``,
    denying the UI its explanation that only [병합 되돌리기] can cancel it."""
    _ledger_commit(_GROUP, _TR_A, "a" * 40, "0009-TR: a")
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: state)

    result = trc.cancel_tr_commits(_GROUP, [_TR_A])

    assert result["attempted"] is False
    assert result["blocked_reason"] == expect_reason
    assert result["retryable"] is expect_retryable
    row = real_store._fetch_one(
        "SELECT * FROM group_git_state WHERE group_id = ?", [_GROUP]
    )
    assert row["last_cancel_block_sub"] == expect_sub
    # No gate modifies ledger rows.
    assert [r["state"] for r in db_ledger.list_by_group(_GROUP)] == ["live"]


def test_a_git_disabled_project_reports_git_inactive(real_store, git_active, monkeypatch):
    _ledger_commit(_GROUP, _TR_A, "a" * 40, "0009-TR: a")
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: {"enabled": 0})

    result = trc.cancel_tr_commits(_GROUP, [_TR_A])

    assert (result["blocked_reason"], result["retryable"]) == ("git_inactive", False)


def test_a_busy_project_lock_is_retryable_and_never_waits_forever(
    real_store, git_active, monkeypatch,
):
    _ledger_commit(_GROUP, _TR_A, "a" * 40, "0009-TR: a")
    stub_group_lock(monkeypatch, grant=False)
    monkeypatch.setattr(svc, "CANCEL_LOCK_WAIT_SEC", 0)

    result = trc.cancel_tr_commits(_GROUP, [_TR_A])

    assert (result["blocked_reason"], result["retryable"]) == ("git_busy", True)


def test_every_block_code_stays_inside_the_closed_protocol_set(real_store):
    """The five codes in the plan's closed set. Adding another code would leave the UI
    without text to render it."""
    assert set(trc.CANCEL_BLOCK_RETRYABLE) == {
        "already_merged", "no_worktree", "git_inactive", "dirty_worktree", "git_busy",
        "history_recovery_required",  # added by 0648 (TR history recovery)
    }


@needs_git
def test_the_lock_is_released_before_the_rearm_can_ask_for_it(
    real_store, git_active, repo, monkeypatch,
):
    """③: the lock is not reentrant. If cancellation returns holding it, rearming
    waits five seconds and then silently fails."""
    held: list[str] = []
    stub_group_lock(monkeypatch, held=held)
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: a")
    _ledger_commit(_GROUP, _TR_A, sha_a, "0009-TR: a")

    trc.cancel_tr_commits(_GROUP, [_TR_A])

    assert held == []


# ── 4. Preview ──────────────────────────────────────────────────

def test_the_preview_shows_live_and_canceled_rows_with_the_group_status(
    real_store, git_active,
):
    row_a = _ledger_commit(_GROUP, _TR_A, "a" * 40, "0009-TR: a")
    _ledger_commit(_GROUP, _TR_B, "b" * 40, "0011-TR: b")
    db_ledger.mark_canceled(row_a["id"], cancel_commit="c" * 40)

    preview = trc.commit_preview(_GROUP)

    assert preview["group_status"] == "active"
    assert preview["commits"] == [
        {"seq": 9, "doc_id": _TR_A, "doc_code": "0009-TR", "commit": "a" * 7,
         "subject": "0009-TR: a", "status": "canceled", "cancel_commit": "c" * 7},
        {"seq": 11, "doc_id": _TR_B, "doc_code": "0011-TR", "commit": "b" * 7,
         "subject": "0011-TR: b", "status": "live", "cancel_commit": None},
    ]


def test_the_preview_calls_a_merged_group_merged_not_worktree_less(
    real_store, git_active, monkeypatch,
):
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: {
        "worktree_registered": 0, "branch": "work", "status": "merged",
    })

    assert trc.commit_preview(_GROUP)["group_status"] == "already_merged"


def test_a_merge_in_flight_previews_as_active(real_store, git_active, monkeypatch):
    """preview omits conditions that may become stale seconds after opening the dialog.
    At confirmation, G6 answers ``git_busy`` with retry allowed."""
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: {
        "worktree_registered": 1, "branch": "work", "status": "merging",
    })

    assert trc.commit_preview(_GROUP)["group_status"] == "active"


def test_a_git_inactive_group_previews_empty(real_store, monkeypatch):
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: None)

    assert trc.commit_preview(_GROUP) == {"group_status": "git_inactive", "commits": []}


# ── 5. Attachment to the rewind path ───────────────────────────

def test_the_rearm_cancels_first_and_re_arms_second(monkeypatch):
    """Order changes the result. Rearming recreates the worktree from base HEAD;
    canceling afterward would find that the target had already disappeared."""
    order: list[str] = []
    monkeypatch.setattr(
        rework.tr_commit_service, "cancel_for_reopen",
        lambda group_id, doc_ids: (order.append(f"cancel:{sorted(doc_ids)}"), {"ok": 1})[1],
    )
    monkeypatch.setattr(
        rework.git_service, "reopen_group_git",
        lambda project_id, group_id: order.append("rearm"),
    )

    out = rework._rearm_git(_PROJECT, _GROUP, [_TR_B, _TR_A])

    assert order == [f"cancel:{sorted([_TR_B, _TR_A])}", "rearm"]
    assert out == {"ok": 1}


def test_a_cancel_that_blows_up_drops_the_key_but_still_re_arms(monkeypatch):
    """rewind succeeds with HTTP 200 and omits the key entirely instead of returning a partial object."""
    def _boom(group_id, doc_ids):
        raise RuntimeError("ledger table missing")

    rearmed: list[str] = []
    monkeypatch.setattr(rework.tr_commit_service, "cancel_for_reopen", _boom)
    monkeypatch.setattr(
        rework.git_service, "reopen_group_git",
        lambda project_id, group_id: rearmed.append(group_id),
    )

    assert rework._rearm_git(_PROJECT, _GROUP, [_TR_A]) is None
    assert rearmed == [_GROUP]


def test_a_rearm_failure_does_not_swallow_the_cancel_result(monkeypatch):
    """Cancellation and rearming do not depend on each other's success."""
    def _boom(project_id, group_id):
        raise RuntimeError("worktree gone")

    monkeypatch.setattr(
        rework.tr_commit_service, "cancel_for_reopen",
        lambda group_id, doc_ids: {"attempted": True},
    )
    monkeypatch.setattr(rework.git_service, "reopen_group_git", _boom)

    assert rework._rearm_git(_PROJECT, _GROUP, [_TR_A]) == {"attempted": True}


def test_the_reopen_result_carries_the_cancel_object(monkeypatch):
    """preserve the existing response (``ok``/``reopened``/``return_point``), adding only
    one key. The assertions in 0142/0381 must continue to hold."""
    payload = {"ok": True, "reopened": [_TR_A], "return_point": {"exists": False}}
    monkeypatch.setattr(
        rework, "_rearm_git",
        lambda project_id, group_id, doc_ids: {"attempted": True, "canceled": []},
    )

    result = dict(payload)
    cancel = rework._rearm_git(_PROJECT, _GROUP, result["reopened"])
    if cancel is not None:
        result["tr_commit_cancel"] = cancel

    assert result["ok"] is True and result["reopened"] == [_TR_A]
    assert result["return_point"] == {"exists": False}
    assert result["tr_commit_cancel"]["attempted"] is True


# ── 6. Retry cancellation only ───────────────────────

def test_the_retry_spares_the_reapproved_commit_but_not_the_document(
    real_store, git_active, monkeypatch,
):
    """A step approved again after rewind produced a new commit. Reverting it would erase work
    the person just redid, so exclude that **row**.

    changed this contract. Previously the entire reapproved **document** was
    excluded, also hiding old rows still live after failed cancellation from every retry path.
    Keep the document as a target and exclude only the row just created by reapproval.
    (test_tr_commit_reapply_0332.py §7 verifies that the old row remains a target.)"""
    calls: list[dict] = []
    monkeypatch.setattr(
        trc, "cancel_tr_commits",
        lambda group_id, doc_ids, exclude_row_ids=None: (
            calls.append({"docs": sorted(doc_ids),
                          "exclude": sorted(exclude_row_ids or [])}),
            trc.empty_cancel_result(),
        )[1],
    )
    fresh = _ledger_commit(_GROUP, _TR_A, "n" * 40, "재승인이 만든 커밋")
    real_store._execute(
        "INSERT INTO workflow_return_points(group_id, front_seq, created_at, updated_at) "
        "VALUES(?, 11, datetime('now'), datetime('now'))", [_GROUP],
    )
    rp_id = real_store._fetch_one(
        "SELECT id FROM workflow_return_points WHERE group_id = ?", [_GROUP]
    )["id"]
    for doc_id, seq in ((_TR_A, 9), (_TR_B, 11)):
        real_store._execute(
            "INSERT INTO workflow_return_point_docs"
            "(return_point_id, doc_id, seq, prev_status, fingerprint) "
            "VALUES(?, ?, ?, 'approved', 'fp')",
            [rp_id, doc_id, seq],
        )
    # was approved again in the meantime.
    real_store._execute(
        "UPDATE documents SET doc_review_status = 'approved' WHERE doc_id = ?", [_TR_A]
    )

    trc.cancel_retry(_GROUP)

    assert calls == [{"docs": sorted([_TR_A, _TR_B]), "exclude": [fresh["id"]]}]


def test_a_retry_without_a_return_point_is_a_quiet_no_op(real_store):
    result = trc.cancel_retry(_GROUP)

    assert result["attempted"] is True
    assert result["blocked_reason"] is None
    assert result["canceled"] == [] and result["skipped"] == []


# ── 7. Both routes ────────────────────────────────────────────

def test_the_return_point_read_carries_the_preview(real_store, git_active):
    """Preview extends the query the dialog already makes, rather than adding an endpoint (introduction)."""
    from modules.flow_gate.documents.routers import documents as doc_routes

    _ledger_commit(_GROUP, _TR_A, "a" * 40, "0009-TR: a")

    payload = doc_routes.get_workflow_return_point(_TR_A, {"user_id": "u"})

    assert payload["ok"] is True
    # Existing fields stay unchanged; only one key is added.
    assert payload["return_point"]["exists"] is False
    assert payload["tr_commit_preview"]["group_status"] == "active"
    assert [c["doc_code"] for c in payload["tr_commit_preview"]["commits"]] == ["0009-TR"]


def test_a_broken_preview_is_omitted_and_the_read_still_answers(
    real_store, git_active, monkeypatch,
):
    """Preview failure must not break the return-point body. Preserve HTTP 200."""
    from modules.flow_gate.documents.routers import documents as doc_routes

    def _boom(group_id):
        raise RuntimeError("ledger table missing")

    monkeypatch.setattr(trc, "commit_preview", _boom)

    payload = doc_routes.get_workflow_return_point(_TR_A, {"user_id": "u"})

    assert payload["ok"] is True
    assert "tr_commit_preview" not in payload


def test_the_retry_route_answers_with_the_same_cancel_object(real_store, monkeypatch):
    from modules.flow_gate.documents.routers import documents as doc_routes

    monkeypatch.setattr(
        trc, "cancel_retry",
        lambda group_id: {**trc.empty_cancel_result(), "attempted": True},
    )

    payload = doc_routes.retry_cancel_tr_commits(_TR_A, {"user_id": "u"})

    assert payload["ok"] is True
    assert payload["tr_commit_cancel"]["attempted"] is True
    # This is not the rewind path; leave document states untouched.
    assert "reopened" not in payload


def test_the_retry_route_404s_on_an_unknown_document(real_store):
    from fastapi import HTTPException

    from modules.flow_gate.documents.routers import documents as doc_routes

    with pytest.raises(HTTPException) as excinfo:
        doc_routes.retry_cancel_tr_commits("flowgate.default.0332.9999-TR", {"user_id": "u"})

    assert excinfo.value.status_code == 404


def test_a_retry_that_blows_up_reports_nothing_attempted_not_a_500(real_store, monkeypatch):
    from modules.flow_gate.documents.routers import documents as doc_routes

    def _boom(group_id):
        raise RuntimeError("ledger exploded")

    monkeypatch.setattr(trc, "cancel_retry", _boom)

    payload = doc_routes.retry_cancel_tr_commits(_TR_A, {"user_id": "u"})

    assert payload == {"ok": True, "tr_commit_cancel": trc.empty_cancel_result()}


# ── 8. Audit event ──────────────────────────────────────────────

def test_a_cancel_outcome_reaches_the_audit_trail(monkeypatch):
    """Record cancellation results in rewind auditing. Add a row rather than rewriting the rewind event:
    painting over a committed audit row later is precisely the history editing this group rejects."""
    logged: list[dict] = []
    monkeypatch.setattr(
        rework.tr_commit_service, "cancel_for_reopen",
        lambda group_id, doc_ids: {
            "attempted": True, "blocked_reason": None, "stopped_reason": None,
            "retryable": False,
            "canceled": [{"doc_id": _TR_A, "doc_code": "0009-TR",
                          "commit": "a" * 7, "cancel_commit": "c" * 7}],
            "skipped": [],
        },
    )
    monkeypatch.setattr(rework.git_service, "reopen_group_git", lambda *_: None)
    monkeypatch.setattr(
        rework.event_logger, "log_event",
        lambda **kwargs: (logged.append(kwargs), {})[1],
    )

    rework._rearm_git(_PROJECT, _GROUP, [_TR_A], "usr_1")

    assert len(logged) == 1
    assert logged[0]["event_type"] == rework.EVT_TR_COMMIT_CANCEL
    # Not a rewind event type; its readers (such as duplicate-rewind detection) remain unchanged.
    assert logged[0]["event_type"] != "workflow_reopen"
    assert logged[0]["group_id"] == _GROUP
    assert logged[0]["metadata"]["canceled"][0]["cancel_commit"] == "c" * 7


def test_a_rewind_with_nothing_to_cancel_writes_no_audit_row(monkeypatch):
    """Recording a nothing-happened event on every rewind would bury meaningful events."""
    logged: list[dict] = []
    monkeypatch.setattr(
        rework.tr_commit_service, "cancel_for_reopen",
        lambda group_id, doc_ids: trc.empty_cancel_result() | {"attempted": True},
    )
    monkeypatch.setattr(rework.git_service, "reopen_group_git", lambda *_: None)
    monkeypatch.setattr(
        rework.event_logger, "log_event",
        lambda **kwargs: (logged.append(kwargs), {})[1],
    )

    rework._rearm_git(_PROJECT, _GROUP, [_TR_A], "usr_1")

    assert logged == []


def test_terminal_dirty_cancel_never_reprovisions_the_worktree(monkeypatch):
    """A late terminal-dirty result must not let best-effort re-arm overwrite it."""
    reprovisioned: list[tuple] = []
    monkeypatch.setattr(
        rework.tr_commit_service, "cancel_for_reopen",
        lambda *_: trc.empty_cancel_result() | {
            "attempted": False, "blocked_reason": "dirty_worktree",
        },
    )
    monkeypatch.setattr(
        rework.git_service, "reopen_group_git",
        lambda *args: reprovisioned.append(args),
    )

    result = rework._rearm_git(_PROJECT, _GROUP, [_TR_A], "usr_1")

    assert result["blocked_reason"] == "dirty_worktree"
    assert reprovisioned == []


# ── 9. live rewind uncommit semantics ───────────────────────────

@needs_git
def test_live_rewind_uncommits_single_tr_without_deleting_content_or_revert(
    real_store, git_active, repo, monkeypatch,
):
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    before_target = _git(["rev-parse", "HEAD"], repo).strip()
    old_sha = _commit(repo, "feat(git): keep content")
    row = _ledger_commit(_GROUP, _TR_A, old_sha, "feat(git): keep content")
    content_before = (repo / "a.py").read_text(encoding="utf-8")
    revert_calls = []
    monkeypatch.setattr(
        svc, "revert_tr_commit",
        lambda *args, **kwargs: revert_calls.append((args, kwargs)),
    )

    result = trc.cancel_for_reopen(_GROUP, [_TR_A])

    assert result["blocked_reason"] is None
    assert _git(["rev-parse", "HEAD"], repo).strip() == before_target
    assert (repo / "a.py").read_text(encoding="utf-8") == content_before
    assert _git(["status", "--porcelain"], repo).strip() == "?? a.py"
    assert revert_calls == []
    after = db_ledger.get_by_id(row["id"])
    assert after["state"] == "canceled"
    assert after["cancel_commit"] is None


@needs_git
def test_live_rewind_uncommits_tr3_only_and_multi_tr_suffix(real_store, git_active, repo):
    base_sha = _git(["rev-parse", "HEAD"], repo).strip()
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha_a = _commit(repo, "TR1")
    _ledger_commit(_GROUP, _TR_A, sha_a, "TR1")
    (repo / "b.py").write_text("b = 1\n", encoding="utf-8")
    sha_b = _commit(repo, "TR2")
    _ledger_commit(_GROUP, _TR_B, sha_b, "TR2")

    one = trc.cancel_for_reopen(_GROUP, [_TR_B])
    assert one["blocked_reason"] is None
    assert _git(["rev-parse", "HEAD"], repo).strip() == sha_a
    assert (repo / "b.py").read_text(encoding="utf-8") == "b = 1\n"

    # Recommit the retained delta as a new live TR2, then rewind the complete suffix.
    sha_b2 = _commit(repo, "TR2")
    _ledger_commit(_GROUP, _TR_B, sha_b2, "TR2")
    both = trc.cancel_for_reopen(_GROUP, [_TR_A, _TR_B])
    assert both["blocked_reason"] is None
    assert _git(["rev-parse", "HEAD"], repo).strip() == base_sha
    assert (repo / "a.py").read_text(encoding="utf-8") == "a = 1\n"
    assert (repo / "b.py").read_text(encoding="utf-8") == "b = 1\n"
    assert [r["state"] for r in db_ledger.list_by_group(_GROUP)] == [
        "canceled", "canceled", "canceled",
    ]


@needs_git
def test_live_rewind_fails_closed_on_interleaved_commit(real_store, git_active, repo):
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha_a = _commit(repo, "TR1")
    _ledger_commit(_GROUP, _TR_A, sha_a, "TR1")
    (repo / "manual.py").write_text("manual = 1\n", encoding="utf-8")
    _commit(repo, "manual")
    (repo / "b.py").write_text("b = 1\n", encoding="utf-8")
    sha_b = _commit(repo, "TR2")
    _ledger_commit(_GROUP, _TR_B, sha_b, "TR2")
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    result = trc.cancel_for_reopen(_GROUP, [_TR_A, _TR_B])

    assert result["blocked_reason"] is None
    assert result["stopped_reason"] == "unsafe_suffix"
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    assert [r["state"] for r in db_ledger.list_by_group(_GROUP)] == ["live", "live"]


@needs_git
def test_live_rewind_reports_reset_failure_without_preflight_block(
    real_store, git_active, repo, monkeypatch,
):
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha = _commit(repo, "TR1")
    _ledger_commit(_GROUP, _TR_A, sha, "TR1")

    monkeypatch.setattr(
        trc.git_service,
        "uncommit_tr_suffix",
        lambda _session, _target_shas: {"kind": "blocked", "sub": "reset_failed"},
    )
    result = trc.cancel_for_reopen(_GROUP, [_TR_A])

    assert result["attempted"] is True
    assert result["blocked_reason"] is None
    assert result["stopped_reason"] == "reset_failed"
    assert _git(["rev-parse", "HEAD"], repo).strip() == sha
    assert [r["state"] for r in db_ledger.list_by_group(_GROUP)] == ["live"]


@needs_git
def test_live_rewind_dirty_guard_preserves_history_and_edit(real_store, git_active, repo):
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha = _commit(repo, "TR1")
    _ledger_commit(_GROUP, _TR_A, sha, "TR1")
    (repo / "f.txt").write_text("dirty\n", encoding="utf-8")

    result = trc.cancel_for_reopen(_GROUP, [_TR_A])

    assert result["blocked_reason"] == "dirty_worktree"
    assert _git(["rev-parse", "HEAD"], repo).strip() == sha
    assert (repo / "f.txt").read_text(encoding="utf-8") == "dirty\n"


@needs_git
def test_reapproval_creates_new_sha_with_preserved_message_and_content(
    real_store, git_active, repo,
):
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    original_subject = "feat(git): preserve rewind content"
    old_sha = _commit(repo, original_subject)
    _ledger_commit(_GROUP, _TR_A, old_sha, original_subject)
    trc.cancel_for_reopen(_GROUP, [_TR_A])

    payload = trc.on_document_approved(_TR_A)
    new_sha = payload["commit"]

    assert payload["committed"] is True
    assert new_sha != old_sha[:7]
    assert _git(["log", "-1", "--pretty=%s"], repo).strip() == original_subject
    assert _git(["show", "HEAD:a.py"], repo) == "a = 1\n"
    rows = db_ledger.list_by_group(_GROUP)
    assert [r["state"] for r in rows] == ["live", "canceled"]

# ── 5. merged/pushed terminal reopen ──────────

@needs_git
@pytest.mark.parametrize("status", ["merged", "pushed"])
def test_terminal_reopen_marks_the_row_terminal_without_touching_git(
    real_store, git_active, repo, monkeypatch, status,
):
    """merged/pushed rows are not revert/reset targets. Even when the gate answers
    ``already_merged``, record only ``reopened_terminal_at`` rather than falsely marking
    the ledger ``canceled``, and never touch Git."""
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha = _commit(repo, "feat(git): merged content")
    row = _ledger_commit(_GROUP, _TR_A, sha, "feat(git): merged content")
    head_before = _git(["rev-parse", "HEAD"], repo).strip()
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: {
        "worktree_registered": 0, "branch": "work", "status": status,
    })

    result = trc.cancel_for_reopen(_GROUP, [_TR_A])

    assert result["attempted"] is True
    assert result["blocked_reason"] is None
    assert result["stopped_reason"] is None
    assert result["canceled"] == []
    assert result["skipped"] == []
    assert result["terminal_reopened"] == [
        {"doc_id": _TR_A, "doc_code": "0009-TR", "history_track": "tr", "commit": sha[:7]}
    ]
    # base content and history are exactly as they were — no reset, no revert.
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    assert (repo / "a.py").read_text(encoding="utf-8") == "a = 1\n"
    after = db_ledger.get_by_id(row["id"])
    assert after["state"] == "live"
    assert after["commit_sha"] == sha
    assert after["cancel_commit"] is None
    assert after["reopened_terminal_at"] is not None


def test_a_terminal_row_stops_being_a_live_cancel_target(real_store):
    """a row marked terminal is excluded from subsequent ordinary rewinds. Otherwise
    the next rewind selects a commit buried below a merge that no reset can reach,
    then pointlessly blocks on ``unsafe_suffix``."""
    row = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_A, commit_sha="a" * 40, commit_subject="x",
    )

    assert db_ledger.live_rows(_GROUP, [_TR_A]) != []
    assert db_ledger.mark_terminal_reopened(row["id"]) is True
    assert db_ledger.live_rows(_GROUP, [_TR_A]) == []
    # state itself is untouched — a terminal row is still, truthfully, `live` history.
    assert db_ledger.get_by_id(row["id"])["state"] == "live"


def test_terminal_history_is_not_a_marker_or_preview_until_reapproval(real_store):
    """C1 is permanent history, not a current live commit.

    All three read models must agree immediately after terminal reopen: cancel targets,
    strip marker, and Time Machine preview are empty. A subsequent approval's C2 is
    then the only current row exposed by each marker/preview query.
    """
    c1 = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_A, commit_sha="a" * 40, commit_subject="C1",
    )
    assert db_ledger.latest_by_doc([_TR_A])[_TR_A]["id"] == c1["id"]
    assert [r["id"] for r in db_ledger.commit_rows_by_group(_GROUP)] == [c1["id"]]

    assert db_ledger.mark_terminal_reopened(c1["id"]) is True
    assert db_ledger.live_rows(_GROUP, [_TR_A]) == []
    assert db_ledger.latest_by_doc([_TR_A]) == {}
    assert db_ledger.commit_rows_by_group(_GROUP) == []

    c2 = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_A, commit_sha="b" * 40, commit_subject="C2",
    )
    assert db_ledger.latest_by_doc([_TR_A])[_TR_A]["id"] == c2["id"]
    assert [r["id"] for r in db_ledger.commit_rows_by_group(_GROUP)] == [c2["id"]]


def test_group_commit_summary_does_not_count_a_terminal_row_as_live(real_store):
    """``list_by_group``/``list_by_groups`` used to omit
    ``reopened_terminal_at``, so a terminal-reopened C1 was counted as ``live`` and sent
    to the status panel as a plain live row, which then rendered it as today's active,
    cancelable commit. The row must stay in the summary (§5 keeps it as history) but stop
    being counted/marked as live; the panel is what tells them apart on ``terminal_reopened``."""
    row = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_A, commit_sha="a" * 40, commit_subject="C1",
    )
    assert db_ledger.mark_terminal_reopened(row["id"]) is True

    summary = trc.group_commit_summary(_GROUP)
    assert summary["live"] == 0
    assert summary["terminal_reopened"] == 1
    [commit_row] = summary["commits"]
    # `state` never lies about history — it stays 'live' — but the panel
    # needs the separate flag to avoid rendering this as the active step.
    assert commit_row["state"] == "live"
    assert commit_row["terminal_reopened"] is True

    summaries = trc.group_commit_summaries([_GROUP])
    assert summaries[_GROUP]["live"] == 0
    assert summaries[_GROUP]["terminal_reopened"] == 1


def test_terminal_reopen_session_checks_existing_worktree_for_dirt(
    real_store, git_active, tmp_path, monkeypatch,
):
    """The terminal-only session holds the lock while applying the G10 equivalent."""
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: {
        "worktree_registered": 1, "branch": "work", "status": "merged",
    })
    monkeypatch.setattr(svc, "src_root", lambda *_: tmp_path)
    monkeypatch.setattr(svc, "cancel_blocking_dirty", lambda _: True)

    opened = svc.open_terminal_reopen_session(_GROUP)

    assert opened == {
        "ok": False, "blocked_reason": "dirty_worktree",
        "block_sub": "dirty_worktree", "session": None,
    }


def test_terminal_reopen_preflight_refuses_dirty_worktree_before_workflow_rewind(
    real_store, git_active, monkeypatch,
):
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: {
        "worktree_registered": 1, "branch": "work", "status": "pushed",
    })
    monkeypatch.setattr(
        svc, "open_terminal_reopen_session",
        lambda *_: {
            "ok": False, "blocked_reason": "dirty_worktree",
            "block_sub": "dirty_worktree", "session": None,
        },
    )

    with pytest.raises(svc.GitServiceError) as excinfo:
        svc.raise_if_git_session_blocks_reopen(_PROJECT, _GROUP)

    assert excinfo.value.code == "dirty_worktree"


def test_terminal_reopen_dirty_guard_stops_before_ledger_mark(real_store, monkeypatch):
    """terminal status cannot bypass the dirty-worktree guard."""
    row = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_A, commit_sha="a" * 40, commit_subject="C1",
    )
    monkeypatch.setattr(
        trc.git_service,
        "open_cancel_session",
        lambda *_: {
            "ok": False, "blocked_reason": "already_merged",
            "block_sub": "already_merged", "session": None,
        },
    )
    monkeypatch.setattr(
        trc.git_service,
        "open_terminal_reopen_session",
        lambda *_: {
            "ok": False, "blocked_reason": "dirty_worktree",
            "block_sub": "dirty_worktree", "session": None,
        },
    )

    result = trc.cancel_for_reopen(_GROUP, [_TR_A])

    assert result["attempted"] is False
    assert result["blocked_reason"] == "dirty_worktree"
    assert result["terminal_reopened"] == []
    assert db_ledger.get_by_id(row["id"])["reopened_terminal_at"] is None


def test_marking_terminal_twice_is_idempotent(real_store):
    row = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_A, commit_sha="a" * 40, commit_subject="x",
    )

    assert db_ledger.mark_terminal_reopened(row["id"]) is True
    first_at = db_ledger.get_by_id(row["id"])["reopened_terminal_at"]
    assert db_ledger.mark_terminal_reopened(row["id"]) is True
    assert db_ledger.get_by_id(row["id"])["reopened_terminal_at"] == first_at


@needs_git
def test_terminal_reopen_reapproval_commits_delta_only_with_reused_message(
    real_store, git_active, repo, monkeypatch,
):
    """reapproval in a worktree whose base (B1) already contains C1 must produce
    a new commit containing only the delta, not C1 again. Reuse C1's message unless an
    explicit draft exists, and always generate a new SHA."""
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    original_subject = "feat(git): merged content"
    old_sha = _commit(repo, original_subject)
    _ledger_commit(_GROUP, _TR_A, old_sha, original_subject)

    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: {
        "worktree_registered": 0, "branch": "work", "status": "merged",
    })
    result = trc.cancel_for_reopen(_GROUP, [_TR_A])
    assert result["blocked_reason"] is None
    assert result["terminal_reopened"] != []

    # 0477's reopen_group_git re-arms the group (status back to 'none') and
    # reprovisions the worktree from base HEAD — which, for a merged group, already
    # IS this exact tree. This fixture's `repo` already models that reprovisioned
    # worktree, so only the state needs to flip back before the user edits Δ.
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: {
        "worktree_registered": 1, "branch": "work", "status": "none",
    })
    (repo / "a.py").write_text("a = 2\n", encoding="utf-8")

    payload = trc.on_document_approved(_TR_A)

    assert payload["committed"] is True
    new_sha = _git(["rev-parse", "HEAD"], repo).strip()
    assert new_sha != old_sha
    # message reused from the terminal C1 row, not the generic fallback subject.
    assert _git(["log", "-1", "--pretty=%s"], repo).strip() == original_subject
    assert _git(["show", "HEAD:a.py"], repo) == "a = 2\n"
    # the new commit's own diff is the delta only — C1's content does not arrive again.
    diff = _git(["diff", old_sha, new_sha, "--", "a.py"], repo)
    assert "-a = 1" in diff
    assert "+a = 2" in diff

    rows = db_ledger.list_by_group(_GROUP)
    assert [r["state"] for r in rows] == ["live", "live"]
    new_row, old_row = rows[0], rows[1]
    assert new_row["commit_sha"] == new_sha
    assert db_ledger.get_by_id(new_row["id"])["reopened_terminal_at"] is None
    assert old_row["commit_sha"] == old_sha
    assert db_ledger.get_by_id(old_row["id"])["reopened_terminal_at"] is not None
    # the terminal C1 row is out of the cancel-target set; only C2 is live-for-cancel.
    live = db_ledger.live_rows(_GROUP, [_TR_A])
    assert len(live) == 1
    assert live[0]["commit_sha"] == new_sha


def test_terminal_reopen_result_never_carries_a_blocked_reason(real_store):
    """The screen reads ``blocked_reason`` as \"nothing was attempted\".
    A terminal reopen DID attempt something (it marked history) and must never be
    reported through that field, or the UI shows [Git 상태 패널 열기] for a reopen that
    already succeeded."""
    result = trc.empty_cancel_result()
    row = {"id": 1, "doc_id": _TR_A, "commit_sha": "a" * 40}
    out = trc._terminal_reopen(result, [row], {_TR_A: "0009-TR"})

    assert out["attempted"] is True
    assert out["blocked_reason"] is None
    assert out["retryable"] is False

