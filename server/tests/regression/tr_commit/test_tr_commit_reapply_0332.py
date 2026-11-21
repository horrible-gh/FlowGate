"""Does forward restoration reapply canceled source? ('s remaining third part.)

implemented commits on approval; the earlier implementation implemented reverts on rewind. This
suite checks reapplication when moving forward. the requirement required all three
as one set. With only two, Time Machine moves code backward but not forward, worse
than consistent unavailability: people assume returning forward restores the original
state, but the worktree disagrees.

Exercise three real layers for the same reason as earlier suites. Reapplication is a
**revert of a revert**; substitutes prove only one more revert call. Real repositories
are needed to establish reverse ordering, preservation of manually redone steps,
and restoration of the worktree on conflicts.

Contracts:
* **Forward restoration brings documents and source together, reporting why anything
  cannot be restored**.
* **Never reapply work a person already redid**, the sole destructive failure mode of reapplication.

Also cover the earlier implementation's gap: reapproving a step after blocked rewind left its
**old live row** unreachable through every retry path. Filtering is now by row
rather than document; §7 covers that contract.
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
    "FLOWGATE_STORAGE_DIR", tempfile.mkdtemp(prefix="fg-tr-reapply-0332-")
)

_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import git_integration as db_git  # noqa: E402
from modules.flow_gate.db import tr_commit_ledger as db_ledger  # noqa: E402
from modules.flow_gate.services import git_service as svc  # noqa: E402
from group_lock_stub import stub_group_lock  # noqa: E402
from modules.flow_gate.services import tr_commit_service as trc  # noqa: E402

_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not _GIT, reason="git binary unavailable")

_SCRATCH = session_scratch("tr-reapply-0332")

_PROJECT = "flowgate"
_GROUP = "flowgate.default.0332"
_TR_A = "flowgate.default.0332.0009-TR"
_TR_B = "flowgate.default.0332.0011-TR"
_D_DOC = "flowgate.default.0332.0005-D"

_SEED_SQL = f"""
INSERT OR IGNORE INTO projects(project_id, project_name, is_active, created_at, updated_at)
    VALUES('{_PROJECT}', 'FlowGate', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO groups(group_id, project_id, module, title, status, created_at, updated_at)
    VALUES('{_GROUP}', '{_PROJECT}', 'default', 'TR 커밋 되살리기', 'OPEN',
           datetime('now'), datetime('now'));
INSERT OR IGNORE INTO documents(
        doc_id, project_id, module, group_id, type_code, seq, title, status,
        doc_review_status, created_at, updated_at)
    VALUES('{_D_DOC}', '{_PROJECT}', 'default', '{_GROUP}', 'D', 5,
           'TR 커밋 취소 기본설계', 'open', 'approved', datetime('now'), datetime('now')),
          ('{_TR_A}', '{_PROJECT}', 'default', '{_GROUP}', 'TR', 9,
           '커밋 포인트 생성 작업레포트', 'open', 'approved', datetime('now'), datetime('now')),
          ('{_TR_B}', '{_PROJECT}', 'default', '{_GROUP}', 'TR', 11,
           '되돌리기 취소 작업레포트', 'open', 'approved', datetime('now'), datetime('now'));
INSERT OR IGNORE INTO group_git_state(
        group_id, project_id, branch, worktree_registered, status, created_at, updated_at)
    VALUES('{_GROUP}', '{_PROJECT}', 'work', 1, 'none', datetime('now'), datetime('now'));
"""


class _SqliteStore:
    """The same minimal contract as the preceding two suites. Deliberately omit ``_sql``
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
    db_path = migrated_sqlite_db("tr_commit_reapply_0332.db", seed_sql=_SEED_SQL)
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
        "worktree_registered": 1, "branch": "work", "status": "none",
    })
    monkeypatch.setattr(svc, "_project_name", lambda project_id: "flowgate")
    monkeypatch.setattr(svc, "src_root", lambda project_name, branch: repo)
    stub_group_lock(monkeypatch)
    return repo


def _ledger_commit(doc_id, sha, subject="s"):
    return db_ledger.record_commit(
        group_id=_GROUP, doc_id=doc_id, commit_sha=sha, commit_subject=subject,
    )


def _two_committed_steps(repo):
    """State after the earlier implementation created a.py and the earlier implementation created b.py."""
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: a")
    _ledger_commit(_TR_A, sha_a, "0009-TR: a")
    (repo / "b.py").write_text("b = 1\n", encoding="utf-8")
    sha_b = _commit(repo, "0011-TR: b")
    _ledger_commit(_TR_B, sha_b, "0011-TR: b")
    return sha_a, sha_b


# ── 1. Ledger reapply columns (086) ────────────────────────────────────────

def test_a_reapply_adds_a_new_live_row_and_leaves_the_canceled_one_alone(real_store):
    """K5: reapplication creates a new row, rather than restoring the canceled row.
    Erasing cancellation would erase the fact of rewind from history."""
    row = _ledger_commit(_TR_A, "a" * 40, "0009-TR: a")
    db_ledger.mark_canceled(row["id"], cancel_commit="c" * 40)

    new_row = db_ledger.record_reapply(
        group_id=_GROUP, doc_id=_TR_A, commit_sha="r" * 40,
        commit_subject="0009-TR: a", restored_from_id=row["id"],
    )

    assert new_row["state"] == "live"
    assert new_row["commit_sha"] == "r" * 40
    assert new_row["restored_from_id"] == row["id"]
    # The canceled row remains byte-for-byte unchanged.
    old = db_ledger.get_by_id(row["id"])
    assert (old["state"], old["cancel_commit"]) == ("canceled", "c" * 40)
    # Both rows remain; this is not an eraser.
    assert [r["state"] for r in db_ledger.list_by_group(_GROUP)] == ["live", "canceled"]


def test_the_reappliable_query_orders_the_peel_the_inverse_of_the_cancel(real_store):
    """Cancellation applied newest commits first with ``id DESC``, so the last cancellation
    commit (the smallest row ID) sits atop Git history. Reapplication must remove it first."""
    row_a = _ledger_commit(_TR_A, "a" * 40, "0009-TR: a")
    row_b = _ledger_commit(_TR_B, "b" * 40, "0011-TR: b")
    # Actual cancellation order: B (newest) first, then A.
    db_ledger.mark_canceled(row_b["id"], cancel_commit="cb" + "0" * 38)
    db_ledger.mark_canceled(row_a["id"], cancel_commit="ca" + "0" * 38)

    rows = db_ledger.reappliable_rows(_GROUP, [_TR_A, _TR_B])

    assert [r["id"] for r in rows] == [row_a["id"], row_b["id"]]
    assert all(int(r["newer_live"] or 0) == 0 for r in rows)


def test_a_row_with_a_newer_live_row_is_flagged_not_dropped(real_store):
    """A manually redone step. Silently excluding it leaves the UI unable to explain why it
    did not return; filter it out but return the reason."""
    row = _ledger_commit(_TR_A, "a" * 40, "0009-TR: a")
    db_ledger.mark_canceled(row["id"], cancel_commit="c" * 40)
    _ledger_commit(_TR_A, "n" * 40, "0009-TR: 다시 한 작업")

    rows = db_ledger.reappliable_rows(_GROUP, [_TR_A])

    assert [r["id"] for r in rows] == [row["id"]]
    assert int(rows[0]["newer_live"]) == 1


def test_newest_live_id_by_doc_names_only_the_freshest_row(real_store):
    stale = _ledger_commit(_TR_A, "a" * 40, "옛 커밋")
    fresh = _ledger_commit(_TR_A, "n" * 40, "새 커밋")

    newest = db_ledger.newest_live_id_by_doc([_TR_A, _TR_B])

    assert newest == {_TR_A: fresh["id"]}
    assert newest[_TR_A] != stale["id"]


# ── 2. Round trip (scenarios A/B) ──────────────────────────────────

@needs_git
def test_a_forward_restore_reapplies_the_canceled_commits_newest_cancel_first(
    real_store, git_active, repo,
):
    """Rewinding and then moving forward must restore the source unchanged. Remove reverts
    in exactly the reverse order from their cancellation application."""
    sha_a, sha_b = _two_committed_steps(repo)
    trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])
    assert not (repo / "a.py").exists() and not (repo / "b.py").exists()

    result = trc.restore_for_return(_GROUP, [_TR_A, _TR_B])

    assert result["attempted"] is True
    assert result["blocked_reason"] is None and result["stopped_reason"] is None
    assert result["skipped"] == []
    assert [line["doc_code"] for line in result["reapplied"]] == ["0009-TR", "0011-TR"]
    # Source is restored in the tree: precisely what failed without K11.
    assert (repo / "a.py").read_text(encoding="utf-8") == "a = 1\n"
    assert (repo / "b.py").read_text(encoding="utf-8") == "b = 1\n"
    # Scenario B: C1·C2·revert C2·revert C1·reapply C1·reapply C2 (oldest first).
    subjects = _git(["log", "-6", "--pretty=%s"], repo).strip().split("\n")
    assert subjects == [
        'Reapply "0011-TR: b"',
        'Reapply "0009-TR: a"',
        'Revert "0009-TR: a"',
        'Revert "0011-TR: b"',
        "0011-TR: b",
        "0009-TR: a",
    ]
    # The reapply commit body names both endpoints, linking all three commits with one grep.
    body = _git(["log", "-1", "--pretty=%b"], repo)
    assert sha_b in body                      # Original TR commit.
    assert f"(group {_GROUP})" in body
    assert "reapply" in body.lower()
    assert sha_a not in body                  # Other steps are not mixed in.
    # Ledger: two canceled rows plus two live reapply rows. No row disappears.
    rows = db_ledger.list_by_group(_GROUP)
    assert sorted(r["state"] for r in rows) == ["canceled", "canceled", "live", "live"]
    restored = [r for r in rows if r["state"] == "live"]
    assert all(r["restored_from_id"] for r in restored)


@needs_git
def test_the_strip_marker_comes_back_to_committed_after_a_restore(
    real_store, git_active, repo,
):
    """Restore markers through the design's existing newest-row-wins rule.
    This asserts that no separate reapplication marker path was introduced."""
    _two_committed_steps(repo)
    trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])
    assert trc.slot_commit_states([_TR_A])[_TR_A]["state"] == "canceled"

    trc.restore_for_return(_GROUP, [_TR_A, _TR_B])

    mark = trc.slot_commit_states([_TR_A])[_TR_A]
    assert mark["state"] == "live"
    # The UI must distinguish initial approval commits from reapplied commits.
    assert mark["restored"] is True


# ── 3. No double application (scenario C) ──────────────────────────────────

@needs_git
def test_a_step_redone_by_hand_is_never_double_applied(real_store, git_active, repo):
    """After rewind, a person reworked and reapproved the step. Reapplying the old commit
    during forward restoration would apply the same work twice, the destructive failure mode."""
    sha_a, _sha_b = _two_committed_steps(repo)
    trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])
    # Manually rework and reapprove the earlier implementation, producing a new commit and live row.
    (repo / "a.py").write_text("a = 2   # 다시 한 작업\n", encoding="utf-8")
    redone = _commit(repo, "0009-TR: a (redone)")
    _ledger_commit(_TR_A, redone, "0009-TR: a (redone)")
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    result = trc.restore_for_return(_GROUP, [_TR_A, _TR_B])

    assert [(s["doc_code"], s["reason"]) for s in result["skipped"]] == [
        ("0009-TR", "superseded"),
    ]
    assert [line["doc_code"] for line in result["reapplied"]] == ["0011-TR"]
    # Redone work remains unchanged; old content was not overlaid.
    assert (repo / "a.py").read_text(encoding="utf-8") == "a = 2   # 다시 한 작업\n"
    assert (repo / "b.py").read_text(encoding="utf-8") == "b = 1\n"
    # Only the earlier implementation adds a new commit.
    assert _git(["rev-list", "--count", f"{head_before}..HEAD"], repo).strip() == "1"
    # The rework row is untouched, and the old canceled row remains canceled.
    assert db_ledger.get_by_id(
        [r for r in db_ledger.list_by_group(_GROUP) if r["commit_sha"] == redone][0]["id"]
    )["restored_from_id"] is None


@needs_git
def test_an_empty_cancel_has_no_commit_to_peel_back_off(real_store, git_active, repo):
    """A row canceled by an empty revert has no cancellation commit to revert. Return it
    with a reason instead of silently omitting it: the next action differs from ``superseded``."""
    row = _ledger_commit(_TR_A, "a" * 40, "0009-TR: a")
    db_ledger.mark_canceled(row["id"], cancel_commit=None, reason="empty_revert")

    result = trc.restore_for_return(_GROUP, [_TR_A])

    assert result["attempted"] is True
    assert result["reapplied"] == []
    assert [(s["doc_code"], s["reason"]) for s in result["skipped"]] == [
        ("0009-TR", "no_cancel_commit"),
    ]


@needs_git
def test_a_restore_run_twice_puts_nothing_back_a_second_time(
    real_store, git_active, repo,
):
    """The live row created by reapplication itself prevents a second application (the same role as K6)."""
    _two_committed_steps(repo)
    trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])
    trc.restore_for_return(_GROUP, [_TR_A, _TR_B])
    head_after_first = _git(["rev-parse", "HEAD"], repo).strip()

    second = trc.restore_for_return(_GROUP, [_TR_A, _TR_B])

    assert second["attempted"] is True
    assert second["reapplied"] == []
    assert [s["reason"] for s in second["skipped"]] == ["superseded", "superseded"]
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_after_first


# ── 4. Blocking and conflicts (scenarios D/E) ──────────────────────────────

@needs_git
def test_a_blocked_reapply_leaves_the_rows_canceled_and_says_why(
    real_store, git_active, repo,
):
    """Do nothing in a dirty worktree to avoid mixing reapplication with uncommitted human edits.
    This is the same G10 condition that blocks cancellation. Document restoration stands,
    and ``retryable=True`` keeps the retry path available."""
    _two_committed_steps(repo)
    trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])
    (repo / "f.txt").write_text("hand edit\n", encoding="utf-8")
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    result = trc.restore_for_return(_GROUP, [_TR_A, _TR_B])

    assert result["attempted"] is False
    assert result["blocked_reason"] == "dirty_worktree"
    assert result["retryable"] is True
    assert result["reapplied"] == []
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    # The ledger remains canceled; it does not claim reapplication.
    assert [r["state"] for r in db_ledger.list_by_group(_GROUP)] == ["canceled", "canceled"]
    state = real_store._fetch_one(
        "SELECT * FROM group_git_state WHERE group_id = ?", [_GROUP]
    )
    assert state["last_cancel_block_sub"] == "dirty_worktree"


@needs_git
def test_a_reapply_conflict_is_parked_as_a_session_and_leaves_the_rest_untried(
    real_store, git_active, repo,
):
    """Reapplication conflicts are not a dead end either.

    deferred this to a later group. Now the **same function** as cancellation
    stores a session in the same table, with only the direction changed to ``tr_reapply``.
    Preserve stopping, leaving remaining items ``not_attempted``, and offering no button
    that merely repeats the same request.
    """
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: A")
    row_a = _ledger_commit(_TR_A, sha_a, "0009-TR: A")
    trc.cancel_tr_commits(_GROUP, [_TR_A])
    # A person edited the same line after cancellation; reapplication conflicts here.
    (repo / "f.txt").write_text("C\n", encoding="utf-8")
    _commit(repo, "manual fix")
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    result = trc.restore_for_return(_GROUP, [_TR_A])

    assert result["attempted"] is True
    assert result["reapplied"] == []
    assert result["stopped_reason"] == "conflict"
    assert [(s["doc_code"], s["reason"]) for s in result["skipped"]] == [
        ("0009-TR", "conflict"),
    ]
    assert result["retryable"] is False
    # No commit, but the conflict stays live; resolution requires something left to resolve.
    parked = result["conflict_session"]
    assert parked and parked["kind"] == "tr_reapply"
    assert parked["files"] == ["f.txt"]
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    assert "<<<<<<<" in (repo / "f.txt").read_text(encoding="utf-8")
    assert svc._revert_in_flight(repo) is True
    # The row remains canceled, and the ledger records what failed.
    after = db_ledger.get_by_id(row_a["id"])
    assert after["state"] == "canceled"
    assert json.loads(after["cancel_attempt_log"])[-1]["reason"] == "revert_conflict"


@needs_git
def test_a_conflict_leaves_the_older_cancels_untouched(real_store, git_active, repo):
    """This is ordered work. Removing items below a conflicting reapplication would worsen conflicts."""
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: A")
    _ledger_commit(_TR_A, sha_a, "0009-TR: A")
    (repo / "b.py").write_text("b = 1\n", encoding="utf-8")
    sha_b = _commit(repo, "0011-TR: b")
    _ledger_commit(_TR_B, sha_b, "0011-TR: b")
    trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])
    # The cancellation of the earlier implementation is removed first; a person has obstructed that location.
    (repo / "f.txt").write_text("C\n", encoding="utf-8")
    _commit(repo, "manual fix")

    result = trc.restore_for_return(_GROUP, [_TR_A, _TR_B])

    assert [(s["doc_code"], s["reason"]) for s in result["skipped"]] == [
        ("0009-TR", "conflict"), ("0011-TR", "not_attempted"),
    ]
    assert not (repo / "b.py").exists()


def test_a_group_with_nothing_canceled_ends_quietly_not_on_an_error(real_store):
    """Forward restoration of a group never rewound must not show a Git error screen:
    finish quietly before the gates (same reason as the earlier implementation note 1)."""
    result = trc.restore_for_return(_GROUP, [_TR_A, _TR_B])

    assert result == {
        "attempted": True, "blocked_reason": None,
        "reapplied": [], "skipped": [], "stopped_reason": None, "retryable": False,
        "conflict_session": None,
    }


@pytest.mark.parametrize(
    "state, expect_reason, expect_retryable",
    [
        ({"worktree_registered": 1, "branch": "work", "status": "merged"},
         "already_merged", False),
        ({"worktree_registered": 1, "branch": "work", "status": "merging"},
         "git_busy", True),
        ({"worktree_registered": 0, "branch": "work", "status": "none"},
         "no_worktree", False),
    ],
)
def test_the_reapply_reuses_the_cancel_gate_ladder_verbatim(
    real_store, git_active, repo, monkeypatch, state, expect_reason, expect_retryable,
):
    """Verify that no duplicate gate set was introduced. Merged groups fail closed for both
    cancellation and reapplication (K7-①), using the single ``CANCEL_BLOCK_RETRYABLE`` table."""
    row = _ledger_commit(_TR_A, "a" * 40, "0009-TR: a")
    db_ledger.mark_canceled(row["id"], cancel_commit="c" * 40)
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: state)

    result = trc.restore_for_return(_GROUP, [_TR_A])

    assert result["attempted"] is False
    assert result["blocked_reason"] == expect_reason
    assert result["retryable"] is expect_retryable
    assert result["retryable"] is trc.CANCEL_BLOCK_RETRYABLE[expect_reason]
    # No gate modifies ledger rows.
    assert db_ledger.get_by_id(row["id"])["state"] == "canceled"


def test_every_reapply_skip_reason_stays_inside_the_closed_set(real_store):
    """Sending a code without corresponding UI text would display a blank reason."""
    assert set(trc.REAPPLY_SKIP_REASONS) == {
        "superseded", "no_cancel_commit", "empty_revert", "conflict", "not_attempted",
        "compensated",
    }


@needs_git
def test_the_lock_is_released_even_when_the_reapply_conflicts(
    real_store, git_active, repo, monkeypatch,
):
    """③: the lock is not reentrant. If reapplication returns holding it,
    the next Git operation waits five seconds and then silently fails."""
    held: list[str] = []
    stub_group_lock(monkeypatch, held=held)
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: A")
    _ledger_commit(_TR_A, sha_a, "0009-TR: A")
    trc.cancel_tr_commits(_GROUP, [_TR_A])
    (repo / "f.txt").write_text("C\n", encoding="utf-8")
    _commit(repo, "manual fix")

    trc.restore_for_return(_GROUP, [_TR_A])

    assert held == []


# ── 5. Commit messages ────────────────────────────────────────

def test_the_reapply_subject_names_the_original_tr_not_the_cancel():
    """``Revert "Revert "the earlier implementation: ...""`` is Git's automatic wording and tells readers nothing useful."""
    assert svc.reapply_subject("0009-TR: 제목") == 'Reapply "0009-TR: 제목"'


def test_a_very_long_subject_is_clipped_inside_the_quotes():
    long_title = "x" * (svc.COMMIT_SUBJECT_MAX + 50)

    subject = svc.reapply_subject(long_title)

    assert len(subject) <= svc.COMMIT_SUBJECT_MAX
    assert subject.startswith('Reapply "') and subject.endswith('…"')


def test_the_reapply_body_names_both_ends_of_the_round_trip():
    body = svc.reapply_body("c" * 40, "a" * 40, "0009-TR", _GROUP)

    # The first line follows the format Git's own tools recognize; the cancellation commit is what gets reverted.
    assert body.splitlines()[0] == f"This reverts commit {'c' * 40}."
    # Name the original TR commit separately below, so grepping either hash finds all three commits.
    assert "a" * 40 in body
    assert f"(group {_GROUP})" in body


# ── 6. Routes and retry (latter part of scenario D) ─────────────────────────

def test_the_restore_route_adds_one_key_and_renames_nothing(real_store, monkeypatch):
    """Follow the plan: preserve existing response keys and meanings, adding only one key.
    The assertions in 0142/0381 must continue to hold."""
    from modules.flow_gate.documents.routers import documents as doc_routes

    payload: dict = {"ok": True, "restored": [_TR_A]}
    monkeypatch.setattr(
        trc, "restore_for_return",
        lambda group_id, doc_ids: {**trc.empty_restore_result(), "attempted": True},
    )

    doc_routes._attach_tr_commit_restore(payload, _PROJECT, _GROUP, [_TR_A], "usr_1")

    assert payload["ok"] is True and payload["restored"] == [_TR_A]
    assert payload["tr_commit_restore"]["attempted"] is True


def test_a_reapply_that_blows_up_drops_the_key_instead_of_half_filling_it(
    real_store, monkeypatch,
):
    """a partial object would make the UI claim source was restored. Omit the key
    entirely. Document restoration still returns HTTP 200."""
    from modules.flow_gate.documents.routers import documents as doc_routes

    def _boom(group_id, doc_ids):
        raise RuntimeError("ledger table missing")

    monkeypatch.setattr(trc, "restore_for_return", _boom)
    payload: dict = {"ok": True, "restored": [_TR_A]}

    doc_routes._attach_tr_commit_restore(payload, _PROJECT, _GROUP, [_TR_A], "usr_1")

    assert "tr_commit_restore" not in payload
    assert payload == {"ok": True, "restored": [_TR_A]}


def test_a_restore_outcome_reaches_the_audit_trail(real_store, monkeypatch):
    from modules.flow_gate.documents.routers import documents as doc_routes
    from modules.flow_gate.workflow import event_logger

    logged: list[dict] = []
    monkeypatch.setattr(
        trc, "restore_for_return",
        lambda group_id, doc_ids: {
            **trc.empty_restore_result(), "attempted": True,
            "reapplied": [{"doc_id": _TR_A, "doc_code": "0009-TR",
                           "commit": "a" * 7, "reapply_commit": "r" * 7}],
        },
    )
    monkeypatch.setattr(
        event_logger, "log_event", lambda **kwargs: (logged.append(kwargs), {})[1],
    )

    doc_routes._attach_tr_commit_restore({}, _PROJECT, _GROUP, [_TR_A], "usr_1")

    assert len(logged) == 1
    assert logged[0]["event_type"] == doc_routes.EVT_TR_COMMIT_REAPPLY
    assert logged[0]["metadata"]["reapplied"][0]["reapply_commit"] == "r" * 7


def test_a_restore_with_nothing_to_put_back_writes_no_audit_row(real_store, monkeypatch):
    from modules.flow_gate.documents.routers import documents as doc_routes
    from modules.flow_gate.workflow import event_logger

    logged: list[dict] = []
    monkeypatch.setattr(
        trc, "restore_for_return",
        lambda group_id, doc_ids: {**trc.empty_restore_result(), "attempted": True},
    )
    monkeypatch.setattr(
        event_logger, "log_event", lambda **kwargs: (logged.append(kwargs), {})[1],
    )

    doc_routes._attach_tr_commit_restore({}, _PROJECT, _GROUP, [_TR_A], "usr_1")

    assert logged == []


@needs_git
def test_the_reapply_retry_works_after_the_return_point_is_gone(
    real_store, git_active, repo,
):
    """Completed forward restoration clears the return point (``return_point_cleared``).
    Tying retry to it would answer that nothing remains exactly when documents have
    moved forward, source has not, and the return point is gone: the state needing an answer."""
    _two_committed_steps(repo)
    trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])
    # The return-point table contains no row for this group.
    assert real_store._fetch_one(
        "SELECT id FROM workflow_return_points WHERE group_id = ?", [_GROUP]
    ) is None

    result = trc.reapply_retry(_GROUP)

    assert [line["doc_code"] for line in result["reapplied"]] == ["0009-TR", "0011-TR"]
    assert (repo / "a.py").exists() and (repo / "b.py").exists()


def test_the_reapply_retry_ignores_steps_whose_approval_is_still_revoked(
    real_store, monkeypatch,
):
    """A step still rewound is not a reapplication target: the person has not moved it forward yet."""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        trc, "reapply_tr_commits",
        lambda group_id, doc_ids: (calls.append(sorted(doc_ids)),
                                   trc.empty_restore_result())[1],
    )
    for doc_id in (_TR_A, _TR_B):
        row = _ledger_commit(doc_id, doc_id[-2:] * 20, "s")
        db_ledger.mark_canceled(row["id"], cancel_commit="c" * 40)
    real_store._execute(
        "UPDATE documents SET doc_review_status = 'pending_review' WHERE doc_id = ?",
        [_TR_B],
    )

    trc.reapply_retry(_GROUP)

    assert calls == [[_TR_A]]


def test_the_reapply_retry_route_answers_with_the_restore_object(real_store, monkeypatch):
    from modules.flow_gate.documents.routers import documents as doc_routes

    monkeypatch.setattr(
        trc, "reapply_retry",
        lambda group_id: {**trc.empty_restore_result(), "attempted": True},
    )

    payload = doc_routes.retry_reapply_tr_commits(_TR_A, {"user_id": "u"})

    assert payload["ok"] is True
    assert payload["tr_commit_restore"]["attempted"] is True
    # Neither rewind nor forward restoration: leave document states untouched.
    assert "restored" not in payload


def test_the_reapply_retry_route_404s_on_an_unknown_document(real_store):
    from fastapi import HTTPException

    from modules.flow_gate.documents.routers import documents as doc_routes

    with pytest.raises(HTTPException) as excinfo:
        doc_routes.retry_reapply_tr_commits("flowgate.default.0332.9999-TR", {"user_id": "u"})

    assert excinfo.value.status_code == 404


def test_a_reapply_retry_that_blows_up_reports_nothing_attempted_not_a_500(
    real_store, monkeypatch,
):
    from modules.flow_gate.documents.routers import documents as doc_routes

    def _boom(group_id):
        raise RuntimeError("ledger exploded")

    monkeypatch.setattr(trc, "reapply_retry", _boom)

    payload = doc_routes.retry_reapply_tr_commits(_TR_A, {"user_id": "u"})

    assert payload == {"ok": True, "tr_commit_restore": trc.empty_restore_result()}


# ── 7. Old live rows surviving reapproval (scenario F / the earlier implementation) ─────────

def test_cancel_retry_still_targets_the_old_live_row_after_a_reapproval(
    real_store, monkeypatch,
):
    """Gap 7: reapproving after a ``dirty_worktree`` block leaves two live rows for the document:
    the old uncanceled row and the new reapproval row. Applying the exclusion rule to
    the **document** removed both, leaving the old row permanently live and unreachable
    (hidden from the newest-row strip, visible only in the panel badge count).
    The rule must apply to individual **rows**."""
    captured: list[dict] = []
    monkeypatch.setattr(
        trc, "cancel_tr_commits",
        lambda group_id, doc_ids, exclude_row_ids=None: (
            captured.append({"docs": sorted(doc_ids),
                             "exclude": sorted(exclude_row_ids or [])}),
            trc.empty_cancel_result(),
        )[1],
    )
    stale = _ledger_commit(_TR_A, "a" * 40, "0009-TR: 취소 못 한 커밋")
    fresh = _ledger_commit(_TR_A, "n" * 40, "0009-TR: 재승인이 만든 커밋")
    other = _ledger_commit(_TR_B, "b" * 40, "0011-TR: b")
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
    # has been reapproved, while the earlier implementation remains rewound.
    real_store._execute(
        "UPDATE documents SET doc_review_status = 'pending_review' WHERE doc_id = ?",
        [_TR_B],
    )

    trc.cancel_retry(_GROUP)

    # Both documents remain targets; reapproval does not exclude the entire document.
    assert captured[0]["docs"] == sorted([_TR_A, _TR_B])
    # Exclude only the row just created by reapproval.
    assert captured[0]["exclude"] == [fresh["id"]]
    assert stale["id"] not in captured[0]["exclude"]
    assert other["id"] not in captured[0]["exclude"]


def test_the_freshly_reapproved_commit_is_the_one_row_the_retry_leaves_alone(real_store):
    """What row-level exclusion actually removes, at the real loop's entry without substitutes."""
    stale = _ledger_commit(_TR_A, "a" * 40, "옛 커밋")
    fresh = _ledger_commit(_TR_A, "n" * 40, "새 커밋")

    result = trc.cancel_tr_commits(_GROUP, [_TR_A], exclude_row_ids={fresh["id"]})

    # Regardless of blocking before a gate, the old row remains a target. Git is disabled,
    # so the gate answers here; that alone proves the target set is nonempty.
    assert result["blocked_reason"] is not None
    assert db_ledger.get_by_id(stale["id"])["state"] == "live"
    assert db_ledger.get_by_id(fresh["id"])["state"] == "live"


def test_excluding_every_row_ends_quietly_instead_of_opening_a_session(real_store):
    """If reapproval was the only row, nothing remains to cancel; finish quietly."""
    fresh = _ledger_commit(_TR_A, "n" * 40, "새 커밋")

    result = trc.cancel_tr_commits(_GROUP, [_TR_A], exclude_row_ids=[fresh["id"]])

    assert result["attempted"] is True
    assert result["blocked_reason"] is None
    assert result["canceled"] == [] and result["skipped"] == []


# ── 8. Evidence for the panel's buttons ─────────────────────────

def test_the_panel_summary_says_whether_anything_can_be_restored(real_store):
    row = _ledger_commit(_TR_A, "a" * 40, "0009-TR: a")
    assert trc.group_commit_summary(_GROUP)["reapply_pending"] is False

    db_ledger.mark_canceled(row["id"], cancel_commit="c" * 40)

    assert trc.group_commit_summary(_GROUP)["reapply_pending"] is True


def test_a_step_redone_by_hand_stops_offering_the_restore(real_store):
    """Offering a reapply button for a step already redone by a person would encourage double application."""
    row = _ledger_commit(_TR_A, "a" * 40, "0009-TR: a")
    db_ledger.mark_canceled(row["id"], cancel_commit="c" * 40)
    _ledger_commit(_TR_A, "n" * 40, "0009-TR: 다시 한 작업")

    assert trc.group_commit_summary(_GROUP)["reapply_pending"] is False


def test_the_summary_carries_the_last_gate_refusal_with_its_verdict(real_store):
    """The UI does not independently decide whether to offer retry; it uses one server-side table."""
    row = _ledger_commit(_TR_A, "a" * 40, "0009-TR: a")
    db_ledger.mark_canceled(row["id"], cancel_commit="c" * 40)
    db_ledger.record_block(_GROUP, "dirty_worktree", "dirty_worktree")

    summary = trc.group_commit_summary(_GROUP)

    assert summary["last_block"]["reason"] == "dirty_worktree"
    assert summary["last_block"]["retryable"] is True

    db_ledger.record_block(_GROUP, "already_merged", "already_merged")

    assert trc.group_commit_summary(_GROUP)["last_block"]["retryable"] is False


def test_a_group_that_never_blocked_carries_no_block_object(real_store):
    _ledger_commit(_TR_A, "a" * 40, "0009-TR: a")

    assert trc.group_commit_summary(_GROUP)["last_block"] is None


def test_the_empty_summary_answers_the_new_questions_too(real_store):
    """The caller fills groups without ledger rows with EMPTY_SUMMARY. Omitting the new key
    would leave a place for the UI to interpret undefined as true."""
    assert trc.EMPTY_SUMMARY["reapply_pending"] is False
    assert trc.EMPTY_SUMMARY["last_block"] is None
    assert set(trc.EMPTY_SUMMARY) == set(trc.group_commit_summary("flowgate.default.9999"))
