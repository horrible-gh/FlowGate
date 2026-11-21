"""Does TR approval commit to the group worktree? (first part of the requirement.)

Exercise each of three real layers. Substituting any layer would prove only that
the feature was called.

1. **Ledger (085)**: execute production INSERT/SELECT statements unchanged on fully
   migrated SQLite, including CHECK constraints rejecting invalid combinations (DB0008 §2-1).
2. **Commit (Git)**: run ``create_tr_commit`` on a real worktree outside the repository,
   checking actual commits, exclusion of tool artifacts (shared 0382 rules), and which
   closed the plan code each failure condition produces.
3. **Hook (approval)**: do nothing for non-TRs; for TRs, leave a ledger row and a response
   matching the plan

The governing contract: **approval stands regardless of what Git does**.
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

from scratch_support import remove_tree, session_scratch

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault(
    "FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode()
)
os.environ.setdefault(
    "FLOWGATE_STORAGE_DIR", tempfile.mkdtemp(prefix="fg-tr-commit-0332-")
)

_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import tr_commit_ledger as db_ledger  # noqa: E402
from modules.flow_gate.services import git_service as svc  # noqa: E402
from group_lock_stub import stub_group_lock  # noqa: E402
from modules.flow_gate.services import tr_commit_service as trc  # noqa: E402

_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not _GIT, reason="git binary unavailable")

_SCRATCH = session_scratch("tr-commit-0332")

_PROJECT = "flowgate"
_GROUP = "flowgate.default.0332"
_TR_DOC = "flowgate.default.0332.0009-TR"
_TR_DOC_2 = "flowgate.default.0332.0011-TR"
_D_DOC = "flowgate.default.0332.0005-D"

_SEED_SQL = f"""
INSERT OR IGNORE INTO projects(project_id, project_name, is_active, created_at, updated_at)
    VALUES('{_PROJECT}', 'FlowGate', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO groups(group_id, project_id, module, title, status, created_at, updated_at)
    VALUES('{_GROUP}', '{_PROJECT}', 'default', 'TR 커밋 포인트', 'OPEN',
           datetime('now'), datetime('now'));
INSERT OR IGNORE INTO documents(
        doc_id, project_id, module, group_id, type_code, seq, title, status,
        created_at, updated_at)
    VALUES('{_D_DOC}', '{_PROJECT}', 'default', '{_GROUP}', 'D', 5,
           'TR 커밋 포인트 기본설계', 'open', datetime('now'), datetime('now')),
          ('{_TR_DOC}', '{_PROJECT}', 'default', '{_GROUP}', 'TR', 9,
           '커밋 포인트 생성 작업레포트', 'open', datetime('now'), datetime('now')),
          ('{_TR_DOC_2}', '{_PROJECT}', 'default', '{_GROUP}', 'TR', 11,
           '되돌리기 확인 창 결과 화면', 'open', datetime('now'), datetime('now'));
"""


class _SqliteStore:
    """The same minimal contract as production FlowGateStore. Deliberately omit ``_sql``
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
    """A real store on SQLite with every migration applied, including 085.

    Replace the connection's STORE object, not the ``get_store`` function, so modules
    that already imported ``get_store`` see the same object.
    """
    db_path = migrated_sqlite_db("tr_commit_ledger_0332.db", seed_sql=_SEED_SQL)
    store = _SqliteStore(db_path)
    previous = db_connection.STORE
    db_connection.STORE = store
    try:
        yield store
    finally:
        db_connection.STORE = previous
        store._conn.close()


# ── 1. Ledger (migration 085) ───────────────────────────────────────────────

def test_a_commit_round_writes_one_live_row(real_store):
    row = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_DOC,
        commit_sha="a" * 40, commit_subject="0009-TR: 커밋 포인트 생성",
    )

    assert row["state"] == "live"
    assert row["commit_sha"] == "a" * 40
    assert row["cancel_attempt_log"] == "[]"
    assert row["skip_reason"] is None


def test_a_skipped_round_writes_one_no_commit_row_carrying_why(real_store):
    """Reason for K3: distinguish no source changes from an inability even to attempt a commit."""
    row = db_ledger.record_no_commit(
        group_id=_GROUP, doc_id=_TR_DOC, skip_reason="git_busy",
    )

    assert row["state"] == "no_commit"
    assert row["commit_sha"] is None
    assert row["skip_reason"] == "git_busy"


def test_the_schema_refuses_a_live_row_without_a_commit(real_store):
    """CHECK ck_trl_commit_sha_matches_state: the first invariant in DB0008 §5-1."""
    with pytest.raises(sqlite3.IntegrityError):
        real_store._execute(
            "INSERT INTO tr_commit_ledger"
            "(group_id, doc_id, state, cancel_attempt_log, created_at, updated_at) "
            "VALUES (?, ?, 'live', '[]', 'now', 'now')",
            [_GROUP, _TR_DOC],
        )


def test_the_schema_refuses_a_skip_reason_outside_the_closed_set(real_store):
    """The six closed-set codes in the plan; the DB rejects a seventh."""
    with pytest.raises(sqlite3.IntegrityError):
        real_store._execute(
            "INSERT INTO tr_commit_ledger"
            "(group_id, doc_id, state, skip_reason, cancel_attempt_log, created_at, updated_at) "
            "VALUES (?, ?, 'no_commit', 'because_i_said_so', '[]', 'now', 'now')",
            [_GROUP, _TR_DOC],
        )


def test_re_approval_adds_a_row_and_the_newest_one_wins(real_store):
    """Reapproval after rewind creates a new row without deleting the earlier row."""
    first = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_DOC, commit_sha="1" * 40, commit_subject="first",
    )
    second = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_DOC, commit_sha="2" * 40, commit_subject="second",
    )

    assert second["id"] > first["id"]
    latest = db_ledger.latest_by_doc([_TR_DOC])
    assert latest[_TR_DOC]["commit_sha"] == "2" * 40
    assert len(db_ledger.list_by_group(_GROUP)) == 2


def test_live_rows_are_the_cancel_targets_newest_first(real_store):
    """sort by ledger row ID (insertion order), not seq."""
    db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_DOC_2, commit_sha="b" * 40, commit_subject="seq 11",
    )
    db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_DOC, commit_sha="c" * 40, commit_subject="seq 9, 나중 커밋",
    )
    db_ledger.record_no_commit(
        group_id=_GROUP, doc_id=_D_DOC, skip_reason="no_changes",
    )

    targets = db_ledger.live_rows(_GROUP, [_TR_DOC, _TR_DOC_2, _D_DOC])

    # seq 9 was committed later, so it must be canceled before seq 11.
    assert [t["doc_id"] for t in targets] == [_TR_DOC, _TR_DOC_2]
    # no_commit rows are not cancellation targets.
    assert _D_DOC not in [t["doc_id"] for t in targets]


# ── 2. Shape consumed by the UI ────────────────────────────────────────────

def test_the_strip_marks_only_steps_that_actually_committed(real_store):
    db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_DOC, commit_sha="d" * 40, commit_subject="0009-TR: 제목",
    )
    db_ledger.record_no_commit(
        group_id=_GROUP, doc_id=_TR_DOC_2, skip_reason="no_changes",
    )

    marks = trc.slot_commit_states([_TR_DOC, _TR_DOC_2, _D_DOC])

    assert marks[_TR_DOC] == {
        "state": "live", "commit": "d" * 7,
        "subject": "0009-TR: 제목", "cancel_commit": None,
        # K11: a commit from initial approval, not a reapplied commit.
        "restored": False,
    }
    # A step that did not change source stays quiet: no marker at all.
    assert _TR_DOC_2 not in marks
    assert _D_DOC not in marks


def test_a_failed_re_approval_does_not_blank_out_an_existing_commit(real_store):
    """A live commit's marker must remain even if reapproval fails to commit.

    Reading only the newest row would let a later ``no_commit`` clear the field,
    making the UI deny a commit that still exists intact on the branch.
    """
    db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_DOC, commit_sha="f" * 40, commit_subject="살아 있는 커밋",
    )
    db_ledger.record_no_commit(
        group_id=_GROUP, doc_id=_TR_DOC, skip_reason="git_busy",
    )

    marks = trc.slot_commit_states([_TR_DOC])

    assert marks[_TR_DOC]["commit"] == "f" * 7
    assert marks[_TR_DOC]["state"] == "live"


def test_the_panel_summary_counts_every_row_and_never_truncates_silently(real_store):
    for index in range(trc.PANEL_COMMIT_LIST_MAX + 3):
        db_ledger.record_commit(
            group_id=_GROUP, doc_id=_TR_DOC,
            commit_sha=f"{index:040d}", commit_subject=f"commit {index}",
        )
    db_ledger.record_no_commit(
        group_id=_GROUP, doc_id=_TR_DOC_2, skip_reason="artifacts_only",
    )

    summary = trc.group_commit_summary(_GROUP)

    assert summary["live"] == trc.PANEL_COMMIT_LIST_MAX + 3
    assert summary["no_commit"] == 1
    assert len(summary["commits"]) == trc.PANEL_COMMIT_LIST_MAX
    assert summary["more"] == 4          # The UI reports the number of collapsed rows as "N개 더".
    # Newest first: the top row is the no_commit row written last.
    assert summary["commits"][0]["doc_code"] == "0011-TR"
    # Assemble doc_code from current documents.seq/type_code, not stored values.
    assert {c["doc_code"] for c in summary["commits"] if c["state"] == "live"} == {"0009-TR"}


def test_summaries_answer_for_several_groups_in_one_call(real_store):
    db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_DOC, commit_sha="e" * 40, commit_subject="x",
    )

    summaries = trc.group_commit_summaries([_GROUP, "flowgate.default.9999"])

    assert summaries[_GROUP]["live"] == 1
    # Omit groups without ledger rows; the caller fills their empty summaries.
    assert "flowgate.default.9999" not in summaries


# ── 3. Approval hook ──────────────────────────────────────────────────────

def test_a_non_tr_approval_is_not_touched_at_all(real_store, monkeypatch):
    """non-TR approval responses remain byte-for-byte identical to before this feature."""
    def _never(*args, **kwargs):
        raise AssertionError("non-TR 승인이 git 을 건드렸다")

    monkeypatch.setattr(svc, "create_tr_commit", _never)

    assert trc.on_document_approved(_D_DOC) is None
    assert db_ledger.list_by_group(_GROUP) == []


def test_a_tr_approval_commits_and_leaves_one_live_row(real_store, monkeypatch):
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": True, "commit": "a1b2c3d", "commit_sha": "a1b2c3d" + "0" * 33,
        "subject": subject, "skipped_reason": None,
        "excluded_artifacts": [], "committed_paths": ["server/x.py"],
    })

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["committed"] is True
    assert payload["commit"] == "a1b2c3d"
    # replaces the earlier implementation: without a draft (this seeded document's
    # commit_message is NULL), use the ASCII fallback; still perform no translation.
    assert payload["subject"] == "chore: approve 0009-TR"
    rows = db_ledger.list_by_group(_GROUP)
    assert [r["state"] for r in rows] == ["live"]
    assert rows[0]["commit_sha"] == "a1b2c3d" + "0" * 33


def test_a_tr_approval_that_could_not_commit_still_leaves_the_reason(real_store, monkeypatch):
    """Approval stands. Record the failure reason in both the ledger and response."""
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": False, "commit": None, "commit_sha": None, "subject": None,
        "skipped_reason": "git_busy", "excluded_artifacts": [], "committed_paths": [],
    })

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["committed"] is False
    assert payload["skipped_reason"] == "git_busy"
    rows = db_ledger.list_by_group(_GROUP)
    assert [(r["state"], r["skip_reason"]) for r in rows] == [("no_commit", "git_busy")]


def test_the_hook_never_lets_a_git_failure_reach_the_approval(real_store, monkeypatch):
    def _boom(group_id, subject):
        raise RuntimeError("git exploded")

    monkeypatch.setattr(svc, "create_tr_commit", _boom)

    # Return None, not an exception: the caller omits tr_commit and returns approval unchanged.
    assert trc.on_document_approved(_TR_DOC) is None


def test_the_reported_list_is_compared_never_used_as_a_filter(real_store, monkeypatch, tmp_path):
    """K2: use the reported list only for comparison, never to narrow commit scope."""
    body = tmp_path / "0009-TR_document.md"
    body.write_text(
        "# 작업레포트\n\n## 변경 파일\n\n- server/reported.py\n- client/forgotten.ts\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        trc.storage_paths, "resolve_storage_path",
        lambda *args, **kwargs: body,
    )
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": True, "commit": "9f8e7d6", "commit_sha": "9f8e7d6" + "0" * 33,
        "subject": subject, "skipped_reason": None, "excluded_artifacts": [],
        "committed_paths": ["server/reported.py", "server/never_declared.py"],
    })
    real_store._execute(
        "UPDATE documents SET file_path = ? WHERE doc_id = ?",
        ["documents/x/0009-TR_document.md", _TR_DOC],
    )

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["reported_diff"] == {
        "unreported": ["server/never_declared.py"],   # Included without being reported.
        "missing": ["client/forgotten.ts"],           # Reported but not included.
    }
    # The commit still succeeds; this is a warning, not a blocker.
    assert payload["committed"] is True


# ── 3a. Approval hook's quiet decision ─────────

def _tr_body_doc(tmp_path, real_store, monkeypatch, body_text):
    """The seed TR's file_path pointed at a real temp file carrying body_text."""
    body = tmp_path / "0009-TR_document.md"
    body.write_text(body_text, encoding="utf-8")
    monkeypatch.setattr(
        trc.storage_paths, "resolve_storage_path", lambda *a, **k: body,
    )
    real_store._execute(
        "UPDATE documents SET file_path = ? WHERE doc_id = ?",
        ["documents/x/0009-TR_document.md", _TR_DOC],
    )
    return body


def test_no_changes_skip_is_always_quiet_even_with_no_declaration(real_store, monkeypatch):
    """git_service already proved this from the real worktree diff - no doc to consult."""
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": False, "commit": None, "commit_sha": None, "subject": None,
        "skipped_reason": "no_changes", "excluded_artifacts": [], "committed_paths": [],
    })

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["skipped_reason"] == "no_changes"
    assert payload["quiet"] is True


def test_artifacts_only_skip_is_always_quiet(real_store, monkeypatch):
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": False, "commit": None, "commit_sha": None, "subject": None,
        "skipped_reason": "artifacts_only", "excluded_artifacts": ["server/.tmp/x"],
        "committed_paths": [],
    })

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["skipped_reason"] == "artifacts_only"
    assert payload["quiet"] is True


def test_git_inactive_skip_is_quiet_when_the_tr_declared_no_changes(
    real_store, monkeypatch, tmp_path,
):
    """2/3 R1 - a no-work group's git_inactive ends quiet, not a warning."""
    _tr_body_doc(tmp_path, real_store, monkeypatch, "# 작업레포트\n\n## 변경 파일\n\n- 없음\n")
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": False, "commit": None, "commit_sha": None, "subject": None,
        "skipped_reason": "git_inactive", "excluded_artifacts": [], "committed_paths": [],
    })

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["skipped_reason"] == "git_inactive"
    assert payload["quiet"] is True


def test_no_worktree_skip_is_quiet_when_the_tr_declared_no_changes(
    real_store, monkeypatch, tmp_path,
):
    """2/3 R2 - same rule, the other git-could-not-be-asked reason."""
    _tr_body_doc(tmp_path, real_store, monkeypatch, "# 작업레포트\n\n## Changed Files\n\n- none\n")
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": False, "commit": None, "commit_sha": None, "subject": None,
        "skipped_reason": "no_worktree", "excluded_artifacts": [], "committed_paths": [],
    })

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["skipped_reason"] == "no_worktree"
    assert payload["quiet"] is True


def test_no_worktree_skip_still_warns_when_worktree_registered_is_off_but_the_stale_dir_disagrees(
    real_store, monkeypatch, tmp_path,
):
    """Automated review rejection of the earlier implementation - `no_worktree`
    fires both when `worktree_registered` is off and when the directory is simply gone;
    `unregister_worktree` clears only the flag, never `branch`, so a slot whose flag was
    dropped but whose branch worktree directory is still on disk with real, undeclared
    edits must not have that probe skipped just because the flag reads 0."""
    _tr_body_doc(tmp_path, real_store, monkeypatch, "# 작업레포트\n\n## 변경 파일\n\n- 없음\n")
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": False, "commit": None, "commit_sha": None, "subject": None,
        "skipped_reason": "no_worktree", "excluded_artifacts": [], "committed_paths": [],
    })
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: {
        "worktree_registered": 0, "branch": "work",
    })
    monkeypatch.setattr(svc, "_project_name", lambda project_id: "flowgate")
    monkeypatch.setattr(svc, "src_root", lambda project_name, branch: tmp_path)
    monkeypatch.setattr(svc, "probe_worktree_pending_changes", lambda wt_path: True)

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["skipped_reason"] == "no_worktree"
    assert payload["quiet"] is False


def test_no_worktree_skip_stays_quiet_when_worktree_registered_is_off_and_no_dir_remains(
    real_store, monkeypatch, tmp_path,
):
    """The other side of the same fix - `worktree_registered` off AND the directory truly
    gone still falls through to None (nothing to probe), so the declaration alone governs,
    exactly as the plain no_worktree quiet case behaved before this probe existed."""
    _tr_body_doc(tmp_path, real_store, monkeypatch, "# 작업레포트\n\n## 변경 파일\n\n- 없음\n")
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": False, "commit": None, "commit_sha": None, "subject": None,
        "skipped_reason": "no_worktree", "excluded_artifacts": [], "committed_paths": [],
    })
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: {
        "worktree_registered": 0, "branch": "work",
    })
    monkeypatch.setattr(svc, "_project_name", lambda project_id: "flowgate")
    monkeypatch.setattr(svc, "src_root", lambda project_name, branch: tmp_path / "gone")

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["skipped_reason"] == "no_worktree"
    assert payload["quiet"] is True


def test_git_inactive_skip_is_quiet_when_no_worktree_ever_existed_even_if_the_tr_declared_real_changes(
    real_store, monkeypatch, tmp_path,
):
    """Automated review rejection of the earlier implementation - a TR's
    prose declaration of a real change is not, on its own, a confirmed source change:
    a worktree is the only place an edit can physically exist, so a group whose git
    state was never registered at all (nothing for ``_worktree_shows_pending_changes``
    to even resolve) has no possible commit target regardless of what the TR body
    claims. Only an on-disk worktree that a probe actually confirms as dirty may keep
    the warning (see the sibling test below, which mocks a real, resolvable worktree
    and gets exactly that)."""
    _tr_body_doc(tmp_path, real_store, monkeypatch, "# 작업레포트\n\n## 변경 파일\n\n- server/real.py\n")
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": False, "commit": None, "commit_sha": None, "subject": None,
        "skipped_reason": "git_inactive", "excluded_artifacts": [], "committed_paths": [],
    })

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["skipped_reason"] == "git_inactive"
    assert payload["quiet"] is True


def test_git_inactive_skip_still_warns_when_the_worktree_disagrees_with_the_declaration(
    real_store, monkeypatch, tmp_path,
):
    """Automated review rejection of the earlier implementation - the TR's own
    "없음" declaration is not the only word: when git_inactive means the project's git
    INTEGRATION is off rather than the worktree being gone, the worktree itself is asked
    directly, and a real (undeclared) edit there must keep the warning."""
    _tr_body_doc(tmp_path, real_store, monkeypatch, "# 작업레포트\n\n## 변경 파일\n\n- 없음\n")
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": False, "commit": None, "commit_sha": None, "subject": None,
        "skipped_reason": "git_inactive", "excluded_artifacts": [], "committed_paths": [],
    })
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: {
        "worktree_registered": 1, "branch": "work",
    })
    monkeypatch.setattr(svc, "_project_name", lambda project_id: "flowgate")
    monkeypatch.setattr(svc, "src_root", lambda project_name, branch: tmp_path)
    monkeypatch.setattr(svc, "probe_worktree_pending_changes", lambda wt_path: True)

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["skipped_reason"] == "git_inactive"
    assert payload["quiet"] is False


def test_git_inactive_skip_stays_quiet_when_the_worktree_probe_agrees(
    real_store, monkeypatch, tmp_path,
):
    """The other side of the same probe - a clean worktree corroborating the declaration
    changes nothing about the existing quiet outcome."""
    _tr_body_doc(tmp_path, real_store, monkeypatch, "# 작업레포트\n\n## 변경 파일\n\n- 없음\n")
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": False, "commit": None, "commit_sha": None, "subject": None,
        "skipped_reason": "git_inactive", "excluded_artifacts": [], "committed_paths": [],
    })
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: {
        "worktree_registered": 1, "branch": "work",
    })
    monkeypatch.setattr(svc, "_project_name", lambda project_id: "flowgate")
    monkeypatch.setattr(svc, "src_root", lambda project_name, branch: tmp_path)
    monkeypatch.setattr(svc, "probe_worktree_pending_changes", lambda wt_path: False)

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["skipped_reason"] == "git_inactive"
    assert payload["quiet"] is True


def test_git_inactive_skip_is_quiet_when_no_registered_state_exists_regardless_of_the_declaration(
    real_store, monkeypatch, tmp_path,
):
    """No registered worktree state to resolve a path from - there is provably no
    worktree for this group, so ``_worktree_shows_pending_changes`` now answers
    ``False`` outright instead of ``None``; the declaration is consulted only when a
    worktree directory exists but git itself cannot answer for it."""
    _tr_body_doc(tmp_path, real_store, monkeypatch, "# 작업레포트\n\n## 변경 파일\n\n- 없음\n")
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": False, "commit": None, "commit_sha": None, "subject": None,
        "skipped_reason": "git_inactive", "excluded_artifacts": [], "committed_paths": [],
    })
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: None)

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["skipped_reason"] == "git_inactive"
    assert payload["quiet"] is True


def test_git_inactive_skip_is_quiet_when_the_section_is_missing_and_no_worktree_exists(
    real_store, monkeypatch,
):
    """Automated review rejection of the earlier implementation - a normal
    no-work group (no git state row registered at all, no ``## 변경 파일`` section to
    even parse) must not be turned into a warning just because the TR's own prose has
    nothing to say. "Git cannot say whether there is a change" is not the same claim
    as "there is a change": with no worktree state to resolve, there is no possible
    commit target, so this converges to quiet exactly like the explicit "없음"
    declaration case above it. A warning is reserved for a directory that a probe can
    actually confirm is dirty (see the ``..._registered_is_off_but_the_stale_dir_disagrees``
    and ``..._worktree_disagrees_with_the_declaration`` tests)."""
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": False, "commit": None, "commit_sha": None, "subject": None,
        "skipped_reason": "git_inactive", "excluded_artifacts": [], "committed_paths": [],
    })

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["skipped_reason"] == "git_inactive"
    assert payload["quiet"] is True


def test_git_busy_skip_is_never_quiet_even_when_the_tr_declared_no_changes(
    real_store, monkeypatch, tmp_path,
):
    """4/7 R7 - a real commit failure keeps its warning regardless of the doc."""
    _tr_body_doc(tmp_path, real_store, monkeypatch, "# 작업레포트\n\n## 변경 파일\n\n- 없음\n")
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": False, "commit": None, "commit_sha": None, "subject": None,
        "skipped_reason": "git_busy", "excluded_artifacts": [], "committed_paths": [],
    })

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["skipped_reason"] == "git_busy"
    assert payload["quiet"] is False


def test_missing_group_id_is_quiet_when_the_tr_declared_no_changes(
    real_store, monkeypatch, tmp_path,
):
    """The early no-group branch follows the same rule as create_tr_commit's
    own git_inactive."""
    body = _tr_body_doc(tmp_path, real_store, monkeypatch, "# 작업레포트\n\n## 변경 파일\n\n- 없음\n")
    monkeypatch.setattr(
        trc.storage_paths, "resolve_storage_path", lambda *a, **k: body,
    )
    partial = {
        "doc_id": _TR_DOC, "type_code": "TR", "group_id": None,
        "title": "커밋 포인트 생성 작업레포트", "seq": 9, "commit_message": None,
        "file_path": "documents/x/0009-TR_document.md", "project_id": _PROJECT,
        "branch": "main",
    }

    payload = trc.on_document_approved(_TR_DOC, partial)

    assert payload["skipped_reason"] == "git_inactive"
    assert payload["quiet"] is True


def test_a_committed_approval_is_never_marked_quiet(real_store, monkeypatch):
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": True, "commit": "a1b2c3d", "commit_sha": "a1b2c3d" + "0" * 33,
        "subject": subject, "skipped_reason": None,
        "excluded_artifacts": [], "committed_paths": ["server/x.py"],
    })

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["committed"] is True
    assert payload["quiet"] is False


# ── 3b. Approval commit subject rules ─────────

def _draft_doc(commit_message=None, seq=9, title="커밋 포인트 생성 작업레포트"):
    """``commit_subject()`` reads only a dict, so its decision logic can be tested without a DB."""
    return {
        "doc_id": _TR_DOC, "seq": seq, "type_code": "TR", "title": title,
        "commit_message": commit_message, "group_id": _GROUP,
    }


def test_a_conventional_ascii_draft_is_used_verbatim():
    doc = _draft_doc(commit_message="fix(git): preserve finalized commit subject")
    assert trc.commit_subject(doc) == "fix(git): preserve finalized commit subject"


def test_a_non_conventional_ascii_draft_gets_the_derived_type_prefixed(real_store):
    """Use the type actually read and returned from the DB by `derive_commit_type()`, without a substitute."""
    real_store._execute(
        "INSERT INTO documents(doc_id, project_id, module, group_id, type_code, seq, "
        "title, status, created_at, updated_at) VALUES (?, ?, 'default', ?, 'B', 1, "
        "'버그', 'open', datetime('now'), datetime('now'))",
        [f"{_GROUP}.0001-B", _PROJECT, _GROUP],
    )
    doc = _draft_doc(commit_message="preserve finalized commit subject")
    assert trc.commit_subject(doc) == "fix: preserve finalized commit subject"


def test_a_korean_draft_is_ignored_and_the_ascii_fallback_is_used():
    doc = _draft_doc(commit_message="복구 API 오류 코드 정리")
    subject = trc.commit_subject(doc)
    assert subject == "chore: approve 0009-TR"
    assert subject.isascii()


def test_no_draft_falls_back_to_the_ascii_chore_subject():
    doc = _draft_doc(commit_message=None)
    assert trc.commit_subject(doc) == "chore: approve 0009-TR"


def test_an_oversized_draft_falls_back_instead_of_being_clipped():
    doc = _draft_doc(commit_message="x" * (svc.COMMIT_SUBJECT_MAX + 1))
    assert trc.commit_subject(doc) == "chore: approve 0009-TR"


def test_missing_commit_message_key_triggers_a_refetch_that_finds_the_draft(
    real_store, monkeypatch,
):
    """the RPC approval dict omits commit_message entirely.
    Verify that re-querying actually happens."""
    real_store._execute(
        "UPDATE documents SET commit_message = ? WHERE doc_id = ?",
        ["fix(git): preserve finalized commit subject", _TR_DOC],
    )
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": True, "commit": "abc1234", "commit_sha": "abc1234" + "0" * 33,
        "subject": subject, "skipped_reason": None,
        "excluded_artifacts": [], "committed_paths": [],
    })
    partial = {
        "doc_id": _TR_DOC, "type_code": "TR", "group_id": _GROUP,
        "title": "커밋 포인트 생성 작업레포트", "seq": 9,
        # No commit_message key: reproduce the result dict from the RPC path (workflow.py:766).
    }

    payload = trc.on_document_approved(_TR_DOC, partial)

    assert payload["subject"] == "fix(git): preserve finalized commit subject"


def test_a_commit_message_key_present_as_none_skips_the_refetch(real_store, monkeypatch):
    """A present key with value None conclusively means no draft; do not re-query."""
    real_store._execute(
        "UPDATE documents SET commit_message = ? WHERE doc_id = ?",
        ["fix(git): preserve finalized commit subject", _TR_DOC],
    )
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": True, "commit": "abc1234", "commit_sha": "abc1234" + "0" * 33,
        "subject": subject, "skipped_reason": None,
        "excluded_artifacts": [], "committed_paths": [],
    })
    partial = {
        "doc_id": _TR_DOC, "type_code": "TR", "group_id": _GROUP,
        "title": "커밋 포인트 생성 작업레포트", "seq": 9,
        "commit_message": None,
    }

    payload = trc.on_document_approved(_TR_DOC, partial)

    # Even if the DB has an English draft, trust the supplied dict's present key with value None.
    assert payload["subject"] == "chore: approve 0009-TR"


def test_cancel_and_reapply_subjects_quote_the_new_style_subject():
    original = "fix(git): preserve finalized commit subject"
    assert svc.cancel_subject(original) == (
        'Revert "fix(git): preserve finalized commit subject"'
    )
    assert svc.reapply_subject(original) == (
        'Reapply "fix(git): preserve finalized commit subject"'
    )


# ── 4. Real Git worktree ───────────────────────────────────────────────────

def _git(args, cwd):
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t",
    })
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout


@pytest.fixture
def repo():
    """One real Git worktree **outside** the repository, following the 0382 recurrence-prevention rule itself."""
    path = _SCRATCH / f"wt-{os.urandom(6).hex()}"
    path.mkdir(parents=True)
    _git(["init", "-b", "work"], path)
    (path / "kept.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "-A"], path)
    _git(["-c", "user.name=T", "-c", "user.email=t@t", "commit", "-m", "base"], path)
    yield path
    remove_tree(path)


@pytest.fixture
def git_active(monkeypatch, repo, real_store):
    """Tell the server the group has Git enabled and this repo is its worktree."""
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


@needs_git
def test_the_commit_really_lands_and_debris_stays_out(git_active, repo):
    (repo / "server").mkdir()
    (repo / "server" / "real.py").write_text("y = 2\n", encoding="utf-8")
    debris = repo / "server" / ".test-tmp-0332"
    debris.mkdir(parents=True)
    (debris / "junk.txt").write_text("junk\n", encoding="utf-8")
    before = _git(["rev-parse", "HEAD"], repo).strip()

    result = svc.create_tr_commit(_GROUP, "0009-TR: 커밋 포인트 생성")

    head = _git(["rev-parse", "HEAD"], repo).strip()
    assert result["committed"] is True
    assert head != before
    assert result["commit_sha"] == head
    assert result["commit"] == head[:7]
    assert _git(["log", "-1", "--pretty=%s"], repo).strip() == "0009-TR: 커밋 포인트 생성"
    assert result["committed_paths"] == ["server/real.py"]
    assert result["excluded_artifacts"] == ["server/.test-tmp-0332/junk.txt"]
    # Exclusion is not deletion: artifact files remain in the worktree (Time Machine principle).
    assert (debris / "junk.txt").exists()


@needs_git
def test_a_tr_that_changed_nothing_gets_no_commit_and_says_so(git_active, repo):
    before = _git(["rev-parse", "HEAD"], repo).strip()

    result = svc.create_tr_commit(_GROUP, "0009-TR: 문서만 쓴 작업")

    assert result["committed"] is False
    assert result["skipped_reason"] == "no_changes"
    # No empty anchor commit; it would defeat finalize's automatic-discard decision.
    assert _git(["rev-parse", "HEAD"], repo).strip() == before


@needs_git
def test_a_worktree_dirty_only_with_debris_is_not_no_changes(git_active, repo):
    """different UI text requires different codes."""
    debris = repo / "server" / ".test-tmp-0332"
    debris.mkdir(parents=True)
    (debris / "junk.txt").write_text("junk\n", encoding="utf-8")

    result = svc.create_tr_commit(_GROUP, "0009-TR: 흔적만 남은 작업")

    assert result["committed"] is False
    assert result["skipped_reason"] == "artifacts_only"
    assert result["excluded_artifacts"] == ["server/.test-tmp-0332/junk.txt"]


@needs_git
def test_a_held_lock_gives_up_immediately_instead_of_stalling_the_approval(
    git_active, repo, monkeypatch,
):
    """tr_commit_lock_wait_sec = 0: one finalization must not block all approvals."""
    stub_group_lock(monkeypatch, grant=False)
    (repo / "server").mkdir()
    (repo / "server" / "real.py").write_text("y = 2\n", encoding="utf-8")

    result = svc.create_tr_commit(_GROUP, "0009-TR: 잠금 경합")

    assert result["committed"] is False
    assert result["skipped_reason"] == "git_busy"
    # Nothing is lost: edits remain in the worktree for the next commit.
    assert (repo / "server" / "real.py").exists()


@needs_git
def test_a_group_without_a_worktree_reports_no_worktree(git_active, monkeypatch):
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: {
        "worktree_registered": 0, "branch": "work", "status": "merged",
    })

    result = svc.create_tr_commit(_GROUP, "0009-TR: 이미 병합된 그룹")

    assert result["committed"] is False
    assert result["skipped_reason"] == "no_worktree"


@needs_git
def test_probe_worktree_pending_changes_sees_an_untracked_edit_without_staging(repo):
    """R6 - the read-only probe behind
    ``_worktree_shows_pending_changes`` must find a real edit without ``git add``ing it
    (unlike ``_stage_worker_edits``, which this must not reuse or trigger)."""
    (repo / "server").mkdir()
    (repo / "server" / "untracked.py").write_text("z = 1\n", encoding="utf-8")

    assert svc.probe_worktree_pending_changes(repo) is True
    # Read-only: nothing got staged, the file is still plainly untracked.
    status = _git(["status", "--porcelain", "--untracked-files=all"], repo).strip()
    assert status == "?? server/untracked.py"


@needs_git
def test_probe_worktree_pending_changes_is_false_on_a_clean_tree(repo):
    assert svc.probe_worktree_pending_changes(repo) is False


@needs_git
def test_probe_worktree_pending_changes_ignores_tool_debris(repo):
    """Same exclusion rule as the real commit path (0382) - debris alone must not read
    as a real pending change."""
    debris = repo / "server" / ".test-tmp-0548"
    debris.mkdir(parents=True)
    (debris / "junk.txt").write_text("junk\n", encoding="utf-8")

    assert svc.probe_worktree_pending_changes(repo) is False


@needs_git
def test_git_disabled_project_is_reported_not_raised(git_active, monkeypatch):
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: {"enabled": 0})

    result = svc.create_tr_commit(_GROUP, "0009-TR: git 꺼진 프로젝트")

    assert result["committed"] is False
    assert result["skipped_reason"] == "git_inactive"


@needs_git
@pytest.mark.parametrize("with_pending_change, expected_quiet", [
    (False, True),
    (True, False),
])
def test_real_git_inactive_approval_uses_the_worktree_as_quiet_ssot(
    real_store, repo, monkeypatch, with_pending_change, expected_quiet,
):
    """The production git-inactive gate and a real worktree cover both sides of the earlier implementation R6.

    No document-body declaration is installed: a clean tree must still be quiet, while
    an undeclared source edit must retain the actionable warning.
    """
    svc.db_git.upsert_config(_PROJECT, {
        "repo_url": "https://example.invalid/flowgate.git",
        "provider": "generic", "secret_enc": None, "base_branch": "main",
        "default_finalize_action": "wait", "enabled": False,
        "translate_url": None, "author_name": None, "author_email": None,
        "tr_scope_stage": "observe",
    })
    svc.db_git.register_worktree(_GROUP, _PROJECT, "work")
    monkeypatch.setattr(svc, "_project_name", lambda project_id: "flowgate")
    monkeypatch.setattr(svc, "src_root", lambda project_name, branch: repo)
    if with_pending_change:
        (repo / "server").mkdir()
        (repo / "server" / "undeclared.py").write_text("changed = True\n", encoding="utf-8")

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["skipped_reason"] == "git_inactive"
    assert payload["quiet"] is expected_quiet


def test_real_no_worktree_ever_registered_stays_quiet_even_with_no_declaration(
    real_store,
):
    """Automated review rejection of the earlier implementation - the exact
    scenario the rejection describes: a group whose git worktree state was never
    registered at all (``db_git.get_state`` genuinely returns no row - nothing here is
    mocked), and a TR body with no ``## 변경 파일`` section to parse either. Fired
    through the unmocked, real approval hook (``on_document_approved``) end to end -
    real sqlite project/group/document rows, a real (disabled) git config row, real
    ``create_tr_commit`` - to prove the "Git is not active" warning this rejection
    reported in the dev server is actually gone for a genuine no-work group, not just
    for one that remembered to write "없음"."""
    svc.db_git.upsert_config(_PROJECT, {
        "repo_url": "https://example.invalid/flowgate.git",
        "provider": "generic", "secret_enc": None, "base_branch": "main",
        "default_finalize_action": "wait", "enabled": False,
        "translate_url": None, "author_name": None, "author_email": None,
        "tr_scope_stage": "observe",
    })
    # No register_worktree call - this group's git state row never existed, and the
    # seeded TR document's file_path is NULL, so there is no Changed Files section.
    assert svc.db_git.get_state(_GROUP) is None

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["committed"] is False
    assert payload["skipped_reason"] == "git_inactive"
    assert payload["quiet"] is True


@needs_git
def test_the_approval_hook_produces_a_conventional_english_commit_in_real_git(
    real_store, git_active, repo,
):
    """acceptance criterion 3: measure the commit subject through
    the real approval hook when a draft exists."""
    real_store._execute(
        "UPDATE documents SET commit_message = ? WHERE doc_id = ?",
        ["fix(git): preserve finalized commit subject", _TR_DOC],
    )
    (repo / "server").mkdir()
    (repo / "server" / "real.py").write_text("y = 2\n", encoding="utf-8")

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["committed"] is True
    subject = _git(["log", "-1", "--pretty=%s"], repo).strip()
    assert subject == "fix(git): preserve finalized commit subject"
    assert subject.isascii()


@needs_git
def test_the_approval_hook_falls_back_to_ascii_chore_when_no_draft(
    real_store, git_active, repo,
):
    """The second measurement for acceptance criterion 3: no draft exists."""
    (repo / "server").mkdir()
    (repo / "server" / "real.py").write_text("y = 3\n", encoding="utf-8")

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["committed"] is True
    subject = _git(["log", "-1", "--pretty=%s"], repo).strip()
    assert subject == "chore: approve 0009-TR"
    assert subject.isascii()


@needs_git
def test_the_finalize_absorb_still_behaves_exactly_as_before(repo):
    """Only the staging step is shared; preserve 0382's absorb-commit contract."""
    (repo / "server").mkdir()
    (repo / "server" / "real.py").write_text("y = 2\n", encoding="utf-8")
    debris = repo / "server" / ".test-tmp-0332"
    debris.mkdir(parents=True)
    (debris / "junk.txt").write_text("junk\n", encoding="utf-8")

    excluded = svc._absorb_worker_edits(repo, "feat: real work", None)

    tracked = {line for line in _git(["ls-files"], repo).splitlines() if line}
    assert "server/real.py" in tracked
    assert not [p for p in tracked if ".test-tmp-0332" in p]
    assert excluded == ["server/.test-tmp-0332/junk.txt"]
