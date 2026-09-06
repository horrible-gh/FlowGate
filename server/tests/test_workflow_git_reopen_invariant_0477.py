"""Workflow root ↔ Git finalize state invariant (0477 NR0003 R1/R2/R5, T#1).

NR0003 §7-13 재조사에 따르면 최종 승인(root=wf_done → Git=awaiting_choice/waiting)까지
갔다가 Time Machine/rework로 root가 wf_in_progress로 되돌아가도 ``reopen_group_git()``은
merged/pushed 두 terminal 상태만 reset하고 awaiting_choice/waiting은 그대로 남긴다. 그
결과 승인이 취소된 그룹인데도 헤더 Git 메뉴에 Merge 실행 버튼이 남고, POST finalize도
그 stale 상태를 재검증 없이 통과시킬 수 있다(§13).

이 시험이 고정하는 계약:

  R1. invariant — Git status가 awaiting_choice/waiting이면 반드시 root == wf_done.
  R2. Reopen 시 pending Git 상태 초기화 — awaiting_choice/waiting은 rework 후 none으로
      떨어진다. conflict/merging은 실제 git 세션/작업이 있을 수 있으므로 조용히 reset하지
      않고, reopen 요청 자체를 409로 막아 세션을 그대로 보존한다.
  R5. Rework path(SSOT) — 이 정책은 ``git_service.reopen_group_git`` /
      ``git_service.raise_if_git_session_blocks_reopen`` /
      ``workflow_rework_service.reopen_to_target`` 세 지점에서 실제로 구현된다.

Test1-4는 ``git_service`` 함수를 DB 상태만 몽키패치해 status별로 직접 검증한다(0332의
``git_active`` 패턴). Test5는 기존 merged/pushed terminal 경로가 이번 변경으로 깨지지
않았음을 진짜 git 워크트리로 재확인하는 회귀다. Test6은 새 가드 함수의 판정표를 모든
status에 대해 훑는다. Test7-8은 실제 seeded group + workflow_sequence + group_git_state로
``reopen_to_target``을 끝까지 돌려, 차단 시 워크플로/문서/git 세션이 정말 손대지지 않고
그대로인지, 통과 시 root와 git 상태가 함께 정확히 떨어지는지를 본다.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import git_integration as db_git  # noqa: E402
from modules.flow_gate.services import git_service as svc  # noqa: E402
from modules.flow_gate.services import workflow_rework_service as rework  # noqa: E402


# ── Part A — git_service unit level (monkeypatched db_git, no real DB rows) ───────


@pytest.fixture
def git_state(monkeypatch):
    """Fakes ``db_git.get_config`` / ``get_state`` with a mutable status cell, and
    records every ``_set_status`` call so a test can assert both the final value and
    that nothing extra ran (0332's ``git_active`` pattern, without the real worktree)."""
    cell = {"status": "none"}
    calls: list[str] = []

    def _get_state(group_id):
        return {"worktree_registered": 1, "branch": "work", "status": cell["status"]}

    def _set_status(group_id, status, **kwargs):
        cell["status"] = status
        calls.append(status)

    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: {
        "enabled": 1, "base_branch": "main", "author_name": None, "author_email": None,
    })
    monkeypatch.setattr(svc.db_git, "get_state", _get_state)
    monkeypatch.setattr(svc, "_set_status", _set_status)
    monkeypatch.setattr(svc, "ensure_worktree", MagicMock())
    cell["calls"] = calls
    return cell


def test_1_awaiting_choice_resets_to_none_on_reopen(git_state):
    git_state["status"] = "awaiting_choice"
    svc.reopen_group_git("flowgate", "flowgate.default.0477.g1")
    assert git_state["status"] == "none"
    assert git_state["calls"] == ["none"]
    svc.ensure_worktree.assert_not_called()


def test_2_waiting_resets_to_none_on_reopen(git_state):
    git_state["status"] = "waiting"
    svc.reopen_group_git("flowgate", "flowgate.default.0477.g2")
    assert git_state["status"] == "none"
    assert git_state["calls"] == ["none"]
    svc.ensure_worktree.assert_not_called()


def test_3_conflict_is_left_untouched_by_reopen_group_git(git_state):
    git_state["status"] = "conflict"
    svc.reopen_group_git("flowgate", "flowgate.default.0477.g3")
    assert git_state["status"] == "conflict"
    assert git_state["calls"] == []
    svc.ensure_worktree.assert_not_called()


def test_4_merging_is_left_untouched_by_reopen_group_git(git_state):
    git_state["status"] = "merging"
    svc.reopen_group_git("flowgate", "flowgate.default.0477.g4")
    assert git_state["status"] == "merging"
    assert git_state["calls"] == []
    svc.ensure_worktree.assert_not_called()


@pytest.mark.parametrize("terminal_status", ["merged", "pushed"])
def test_5_terminal_statuses_still_reset_and_reprovision(git_state, terminal_status):
    """Regression: the pre-existing merged/pushed re-arm path must survive the R2 change
    (it now shares the function with a new awaiting_choice/waiting branch)."""
    git_state["status"] = terminal_status
    svc.reopen_group_git("flowgate", "flowgate.default.0477.g5")
    assert git_state["status"] == "none"
    assert git_state["calls"] == ["none"]
    svc.ensure_worktree.assert_called_once_with(
        "flowgate", svc._module_of("flowgate.default.0477.g5"),
        "flowgate.default.0477.g5", trigger="timemachine_reopen",
    )


@pytest.mark.parametrize(
    "status,should_block,expected_code",
    [
        ("none", False, None),
        ("awaiting_choice", False, None),
        ("waiting", False, None),
        ("merged", False, None),
        ("pushed", False, None),
        ("conflict", True, "invalid_state"),
        ("merging", True, "git_busy"),
    ],
)
def test_6_raise_if_git_session_blocks_reopen_matrix(git_state, status, should_block, expected_code):
    git_state["status"] = status
    if should_block:
        with pytest.raises(svc.GitServiceError) as excinfo:
            svc.raise_if_git_session_blocks_reopen("flowgate", "flowgate.default.0477.g6")
        assert excinfo.value.status == 409
        assert excinfo.value.code == expected_code
    else:
        svc.raise_if_git_session_blocks_reopen("flowgate", "flowgate.default.0477.g6")
    # Either way the guard itself never mutates the ledger — only reopen_group_git does.
    assert git_state["calls"] == []


# ── Part B — end-to-end through reopen_to_target (real seeded DB) ─────────────────


class _MockDB:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql: str, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params=None):
        return [dict(row) for row in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        self._conn.execute("BEGIN")
        txn = _MockTxn(self._conn)
        try:
            yield txn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self):
        self._conn.close()


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._last_cursor = None

    def execute(self, sql: str, params=None):
        self._last_cursor = self._conn.execute(sql, params or [])

    def fetch_one(self):
        row = self._last_cursor.fetchone() if self._last_cursor is not None else None
        return dict(row) if row else None

    def fetch_all(self):
        if self._last_cursor is None:
            return []
        return [dict(row) for row in self._last_cursor.fetchall()]


@pytest.fixture
def reopen_store(tmp_path, monkeypatch):
    mock_db = _MockDB(str(tmp_path / "reopen-invariant-0477.db"))
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
    mock_db._conn.commit()
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(storage_root))

    from modules.flow_gate.db import connection as conn_mod

    original_store = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

    conn_mod.STORE = _PatchedStore()
    yield storage_root
    conn_mod.STORE = original_store
    mock_db.close()


def _seed_finally_approved_group(storage_root: Path, suffix: str) -> dict:
    """A group that already reached final approval: root=wf_done, AC approved — the exact
    precondition NR0003 §7 walks back from wf_done to wf_in_progress."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.storage import paths as storage_paths

    project_id = f"reopen0477{suffix}"
    group_id = f"reopen0477.default.{suffix}"
    user_id = f"usr_reopen_{suffix}"
    projects.create({"project_id": project_id, "project_name": f"Reopen {suffix}"})
    users.create({
        "user_id": user_id, "username": f"reopen{suffix}",
        "email": f"reopen{suffix}@test.com", "password": "hashed",
    })
    db_groups.create({
        "group_id": group_id, "project_id": project_id, "module": "default",
        "title": f"Reopen invariant {suffix}",
    })

    specs = [
        (1, "R", "Root", "wf_done"),
        (2, "T", "Task", "approved"),
        (3, "TS", "Scenario", "approved"),
        (4, "AC", "Final approval", "approved"),
    ]
    ids = {"project_id": project_id, "group_id": group_id, "user_id": user_id}
    for seq, type_code, title, review_status in specs:
        doc_code = f"{seq:04d}-{type_code}"
        doc_id = f"{group_id}.{doc_code}"
        file_path = None
        if type_code != "AC":
            path = storage_paths.document_path(
                project_id=project_id, group_code=group_id, doc_code=doc_code,
                filename="document.md", module="default",
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"---\ntitle: {title}\n---\n# {title}\n", encoding="utf-8")
            file_path = storage_paths.to_storage_relative(path, project_id)
        db_docs.create({
            "doc_id": doc_id, "project_id": project_id, "module": "default",
            "group_id": group_id, "type_code": type_code, "seq": seq, "title": title,
            "owner_id": user_id, "file_path": file_path,
        })
        db_docs.update(doc_id, {"doc_review_status": review_status})
        ids[type_code] = doc_id

    db_wfseq.insert_sequence(ids["R"])
    sequence = db_wfseq.get_sequence_by_doc_id(ids["R"])
    for order, type_code in enumerate(("T", "TS"), start=1):
        db_wfseq.insert_sequence_item(sequence["id"], order, type_code, type_code, "doc", order)
    for item in db_wfseq.get_sequence_items(sequence["id"]):
        result_doc_id = ids.get(item["type"])
        if result_doc_id:
            db_wfseq.set_item_result_doc_id(item["id"], result_doc_id)

    db_git.upsert_config(project_id, {"repo_url": "https://example.invalid/x.git", "enabled": 1})
    db_git.register_worktree(group_id, project_id, "work")
    return ids


def _reopen(ids: dict, target_seq: int = 2):
    from modules.flow_gate.services.mutation_policy import human_principal

    return rework.reopen_to_target(
        doc_id=ids["T"], target_seq=target_seq, actor={"user_id": ids["user_id"]},
        mutation_context=human_principal({"user_id": ids["user_id"]}),
    )


def test_7_conflict_blocks_the_reopen_and_preserves_workflow_and_git_session(reopen_store):
    ids = _seed_finally_approved_group(reopen_store, "conflict")
    # Seed an actual open merge session (not just a bare status cell) so the assertion
    # below can prove the *session itself* — not merely a NULL column — survives untouched.
    merge_id = db_git.create_session(ids["group_id"], ["a.txt"], finalize_action=None)
    db_git.set_status(ids["group_id"], "conflict", merge_id=merge_id)

    from modules.flow_gate.db import documents as db_docs

    state_before = db_git.get_state(ids["group_id"])
    session_before = db_git.get_session(merge_id)
    ac_before = db_docs.get_by_id(ids["AC"])
    assert ac_before["doc_review_status"] == "approved"

    with pytest.raises(svc.GitServiceError) as excinfo:
        _reopen(ids)
    assert excinfo.value.status == 409
    assert excinfo.value.code == "invalid_state"

    # The rewind transaction must never have started: root/T/AC are exactly as seeded.
    root = db_docs.get_by_id(ids["R"])
    assert root["doc_review_status"] == "wf_done"
    task = db_docs.get_by_id(ids["T"])
    assert task["doc_review_status"] == "approved"
    ac = db_docs.get_by_id(ids["AC"])
    assert ac["status"] != "archived"
    # The approval field itself (doc_review_status), not just the lifecycle `status`,
    # must be byte-identical before/after the blocked call and still 'approved' — a
    # reopen that flips AC's review status while leaving it 'active' would otherwise
    # slip past the `status != archived` check above.
    assert ac["doc_review_status"] == ac_before["doc_review_status"] == "approved"

    # The git session itself is untouched: status/merge_id/merge_commit are byte-identical
    # before and after the blocked call, and the merge_session row is still 'open' (a reset
    # would either null the merge_id or close the session — either is caught here).
    state_after = db_git.get_state(ids["group_id"])
    assert state_after["status"] == state_before["status"] == "conflict"
    assert state_after["merge_id"] == state_before["merge_id"] == merge_id
    assert state_after["merge_commit"] == state_before["merge_commit"]
    session_after = db_git.get_session(merge_id)
    assert session_after["status"] == session_before["status"] == "open"


def test_8_merging_blocks_while_awaiting_choice_reopens_cleanly(reopen_store):
    # 8a — merging blocks exactly like conflict (session preserved, different code).
    ids_busy = _seed_finally_approved_group(reopen_store, "busy")
    merge_id = db_git.create_session(ids_busy["group_id"], ["b.txt"], finalize_action="merge")
    db_git.set_status(ids_busy["group_id"], "merging", merge_id=merge_id)

    from modules.flow_gate.db import documents as db_docs

    state_before = db_git.get_state(ids_busy["group_id"])
    session_before = db_git.get_session(merge_id)
    root_before = db_docs.get_by_id(ids_busy["R"])
    task_before = db_docs.get_by_id(ids_busy["T"])
    ac_before = db_docs.get_by_id(ids_busy["AC"])
    assert ac_before["doc_review_status"] == "approved"

    with pytest.raises(svc.GitServiceError) as excinfo:
        _reopen(ids_busy)
    assert excinfo.value.status == 409
    assert excinfo.value.code == "git_busy"

    # Same invariant as test_7: status/merge_id/merge_commit survive the blocked call
    # byte-for-byte, the merge_session row stays 'open', and T/AC (not just root) are
    # left exactly where they were seeded — the rewind transaction never started.
    state_after = db_git.get_state(ids_busy["group_id"])
    assert state_after["status"] == state_before["status"] == "merging"
    assert state_after["merge_id"] == state_before["merge_id"] == merge_id
    assert state_after["merge_commit"] == state_before["merge_commit"]
    session_after = db_git.get_session(merge_id)
    assert session_after["status"] == session_before["status"] == "open"

    root_busy = db_docs.get_by_id(ids_busy["R"])
    assert root_busy["doc_review_status"] == root_before["doc_review_status"] == "wf_done"
    task_busy = db_docs.get_by_id(ids_busy["T"])
    assert task_busy["doc_review_status"] == task_before["doc_review_status"] == "approved"
    ac_busy = db_docs.get_by_id(ids_busy["AC"])
    assert ac_busy["status"] == ac_before["status"]
    assert ac_busy["status"] != "archived"
    # The approval field itself must survive the blocked call unchanged and pinned to
    # 'approved' — `status` alone (checked above) can't catch a reopen that silently
    # flips doc_review_status while leaving the lifecycle status 'active'.
    assert ac_busy["doc_review_status"] == ac_before["doc_review_status"] == "approved"

    # 8b — awaiting_choice is the actual stale-state bug from NR0003 §9: the reopen must
    # go through AND the invariant (Git pending ⇒ root wf_done) must hold again afterward.
    ids_ok = _seed_finally_approved_group(reopen_store, "ok")
    db_git.set_status(ids_ok["group_id"], "awaiting_choice")

    result = _reopen(ids_ok)
    assert result["ok"] is True

    root_ok = db_docs.get_by_id(ids_ok["R"])
    assert root_ok["doc_review_status"] == "wf_in_progress"
    task_ok = db_docs.get_by_id(ids_ok["T"])
    assert task_ok["doc_review_status"] == "pending_review"
    assert db_git.get_state(ids_ok["group_id"])["status"] == "none"


# ── Part C — finalize() stale pending defense (T#2) ──────────────────────────────


@pytest.fixture
def finalize_pending_state(tmp_path, monkeypatch):
    """A deterministic finalize context that exposes every ledger field T#2 protects."""
    state = {
        "status": "awaiting_choice",
        "merge_id": "merge-stays-put",
        "merge_commit": "commit-stays-put",
        "worktree_registered": 1,
        "branch": "work-stays-put",
    }
    calls: list[str] = []
    root_check = MagicMock(return_value=False)
    lock = MagicMock(return_value=True)

    monkeypatch.setattr(
        svc,
        "_finalize_context",
        lambda group_id: (
            {"enabled": 1, "base_branch": "main"},
            state.copy(),
            "flowgate",
            tmp_path / "base",
            tmp_path / "work",
        ),
    )
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: state.copy())
    monkeypatch.setattr(svc.db_git, "get_open_session_by_group", lambda group_id: None)
    monkeypatch.setattr(svc, "_group_root_wf_done", root_check)
    monkeypatch.setattr(svc, "_acquire_lock", lock)

    def set_status(group_id, status, **kwargs):
        state["status"] = status
        calls.append(status)

    monkeypatch.setattr(svc, "_set_status", set_status)
    state["calls"] = calls
    state["root_check"] = root_check
    state["lock"] = lock
    return state


@pytest.mark.parametrize("pending_status", ["awaiting_choice", "waiting"])
def test_9_stale_pending_finalize_is_409_and_preserves_ledger(
    finalize_pending_state, pending_status,
):
    """Tests A-C: both stale pending states reject even `wait`, before mutation/lock."""
    finalize_pending_state["status"] = pending_status
    protected = {
        key: finalize_pending_state[key]
        for key in (
            "status", "merge_id", "merge_commit", "worktree_registered", "branch"
        )
    }

    with pytest.raises(svc.GitServiceError) as excinfo:
        svc.finalize("flowgate.default.0477.stale", "wait")

    assert excinfo.value.status == 409
    assert excinfo.value.code == "invalid_state"
    assert "workflow approval" in excinfo.value.message
    assert {
        key: finalize_pending_state[key] for key in protected
    } == protected
    assert finalize_pending_state["calls"] == []
    finalize_pending_state["lock"].assert_not_called()
    finalize_pending_state["root_check"].assert_called_once_with(
        "flowgate.default.0477.stale"
    )


@pytest.mark.parametrize("pending_status", ["awaiting_choice", "waiting"])
def test_10_wf_done_pending_finalize_wait_remains_allowed(
    finalize_pending_state, pending_status,
):
    """Test D: a genuinely approved root keeps the existing fast wait path."""
    finalize_pending_state["status"] = pending_status
    finalize_pending_state["root_check"].return_value = True

    out = svc.finalize("flowgate.default.0477.approved", "wait")

    assert out["result"]["status"] == "waiting"
    assert finalize_pending_state["status"] == "waiting"
    assert finalize_pending_state["calls"] == ["waiting"]
    finalize_pending_state["lock"].assert_not_called()
    finalize_pending_state["root_check"].assert_called_once_with(
        "flowgate.default.0477.approved"
    )


def test_11_wf_done_none_lazy_transition_still_finalizes(finalize_pending_state):
    """Test E: status=none still lazily enters awaiting_choice, then handles wait."""
    finalize_pending_state["status"] = "none"
    finalize_pending_state["root_check"].return_value = True

    out = svc.finalize("flowgate.default.0477.lazy", "wait")

    assert out["result"]["status"] == "waiting"
    assert finalize_pending_state["status"] == "waiting"
    assert finalize_pending_state["calls"] == ["awaiting_choice", "waiting"]
    finalize_pending_state["root_check"].assert_called_once_with(
        "flowgate.default.0477.lazy"
    )


# ── Part E — project status stale-pending recovery (T#3) ─────────────────────────


def test_14_project_status_recovers_stale_pending_before_header_aggregation(monkeypatch):
    """T0009: status polling repairs only stale awaiting_choice/waiting rows.

    The test supplies root-missing/non-wf_done stale rows, approved pending
    controls, a conflict control, and a normal lazy-none row. It pins the
    single batch root lookup and proves recovery has no Git/worktree action.
    """
    project_id = "flowgate"
    rows = [
        {"group_id": "stale-awaiting", "status": "awaiting_choice", "worktree_registered": 1, "branch": "a"},
        {"group_id": "stale-waiting", "status": "waiting", "worktree_registered": 1, "branch": "b"},
        {"group_id": "approved-awaiting", "status": "awaiting_choice", "worktree_registered": 1, "branch": "c"},
        {"group_id": "approved-waiting", "status": "waiting", "worktree_registered": 1, "branch": "d"},
        {"group_id": "active-conflict", "status": "conflict", "worktree_registered": 1, "branch": "e"},
        {"group_id": "lazy-none", "status": "none", "worktree_registered": 1, "branch": "f"},
    ]
    root_lookup = MagicMock(return_value={
        "approved-awaiting", "approved-waiting", "lazy-none",
    })
    repaired: list[tuple[str, str]] = []
    lazy = MagicMock(return_value="awaiting_choice")

    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda value: {"project_id": value})
    monkeypatch.setattr(svc.db_git, "get_config", lambda value: {
        "enabled": 1, "base_branch": "main", "default_finalize_action": "wait",
    })
    monkeypatch.setattr(svc.db_git, "list_states_of_project_any", lambda value: rows)
    monkeypatch.setattr(svc, "_groups_root_wf_done", root_lookup)
    monkeypatch.setattr(
        svc, "_set_status",
        lambda group_id, status, **kwargs: repaired.append((group_id, status)),
    )
    monkeypatch.setattr(svc, "_decide_pending_transition", lazy)
    monkeypatch.setattr(svc, "_project_name", lambda value: None)
    monkeypatch.setattr(svc, "group_worktree_writable", lambda *args: True)
    monkeypatch.setattr(svc, "_group_ac_doc_ids", lambda group_ids: {})
    monkeypatch.setattr(svc, "_base_ahead_behind", lambda *args: (0, 0))
    monkeypatch.setattr(svc, "_build_unpushed", lambda *args: [])
    monkeypatch.setattr(svc.db_terminal_cleanup, "get", lambda value: None)

    status = svc.project_git_status(project_id)["status"]

    root_lookup.assert_called_once_with([
        "stale-awaiting", "stale-waiting", "approved-awaiting",
        "approved-waiting", "lazy-none",
    ])
    assert repaired == [("stale-awaiting", "none"), ("stale-waiting", "none")]
    lazy.assert_called_once_with(
        project_id, svc.db_git.get_config(project_id), rows[5], "lazy-none",
    )

    pending_ids = [item["group_id"] for item in status["pending"]]
    slot_ids = [item["group_id"] for item in status["slots"]]
    assert pending_ids == ["approved-awaiting", "approved-waiting", "active-conflict", "lazy-none"]
    # Recovery is a pending-only demotion: the row's status is now "none", which
    # SLOT_STATUSES already keeps in `slots` (matching every other "none" group) —
    # a recovered group must stay selectable/visible, not vanish from the header.
    assert "stale-awaiting" not in pending_ids
    assert "stale-waiting" not in pending_ids
    assert "stale-awaiting" in slot_ids
    assert "stale-waiting" in slot_ids
    slots_by_id = {item["group_id"]: item for item in status["slots"]}
    assert slots_by_id["stale-awaiting"]["status"] == "none"
    assert slots_by_id["stale-awaiting"]["branch"] == "a"
    assert slots_by_id["stale-waiting"]["status"] == "none"
    assert slots_by_id["stale-waiting"]["branch"] == "b"
    assert status["pending_count"] == len(status["pending"]) == 4
    assert rows[0]["status"] == rows[1]["status"] == "none"
    assert rows[0]["branch"] == "a"
    assert rows[1]["branch"] == "b"
    assert rows[4]["status"] == "conflict"


# ── Part D — router/HTTP envelope for the stale-pending 409 (0007-T 완료 기준 3) ──


def test_12_stale_pending_finalize_409_reaches_router_envelope(
    finalize_pending_state, monkeypatch,
):
    """test_9 already pins svc.finalize()'s 409/invalid_state at the service layer.
    That alone doesn't prove the *production* router exposes it correctly. This test
    drives the real POST /groups/{group_id}/git/finalize route — the real
    git_routes.router, unmodified — through a FastAPI TestClient and asserts the
    stale-pending GitServiceError surfaces as HTTP 409 {"ok": false, "error":
    {code, message}}, never a bare 500 or an unconverted exception.

    What this proves and what it doesn't: post_group_finalize (git_routes.py:450-465)
    wraps the git_service.finalize() call in its own local
    `except GitServiceError as exc: return _guard(exc)`, so the exception never
    reaches FastAPI's exception-dispatch layer here — this route's 409 envelope comes
    from that local `_guard`, not from routers/main.py's global
    `git_service_exception_handler`. That is a real, separate mechanism and this test
    intentionally targets it: it is the actual HTTP contract a client of the real
    finalize endpoint observes. Coverage of the *global* handler itself — for
    endpoints that reuse git_service without a local guard, per flowgate.default.0233
    B0001 — is test_13 below.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api.v1 import git_routes
    from modules.flow_gate.auth.middleware import get_current_user

    app = FastAPI()
    app.include_router(git_routes.router)

    app.dependency_overrides[get_current_user] = lambda: {"user_id": "usr_admin"}
    monkeypatch.setattr(git_routes, "_has_permission", lambda *a, **k: True)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/api/v1/groups/flowgate.default.0477.stale/git/finalize",
        json={"action": "wait"},
    )

    assert resp.status_code == 409
    assert resp.json() == {
        "ok": False,
        "error": {
            "code": "invalid_state",
            "message": "final workflow approval is required before Git finalize",
        },
    }
    # the router call reused the exact same finalize() path as test_9 — nothing was
    # mutated or locked before the 409 was raised.
    assert finalize_pending_state["calls"] == []
    finalize_pending_state["lock"].assert_not_called()
    finalize_pending_state["root_check"].assert_called_once_with(
        "flowgate.default.0477.stale"
    )


def test_13_stale_pending_finalize_409_reaches_global_exception_handler(
    finalize_pending_state,
):
    """0007-T 완료 기준 3 targets routers/main.py's global
    `git_service_exception_handler` specifically — the handler installed for
    endpoints that call into git_service *without* a local `except GitServiceError`
    guard (flowgate.default.0233 B0001: token_routes' /token/issue and
    ai_invoke_routes' /ai-invoke/start build their conflict mention via
    git_service.list_conflicts with no such guard, so an uncaught GitServiceError
    reaches FastAPI's exception dispatch and is converted there). The real
    git/finalize route can never exercise that path: post_group_finalize catches
    GitServiceError itself before FastAPI's dispatch ever runs (see test_12's
    docstring), so mounting git_routes.router — as a prior revision of this test did
    — only re-tests the route's own local `_guard` under a handler that is never
    actually invoked.

    To exercise the real mechanism, this test defines a bare, deliberately
    *un-guarded* endpoint that calls svc.finalize() directly and lets GitServiceError
    propagate — reproducing the exact un-guarded-reuse shape of the 0233 endpoints,
    just with finalize()'s new stale-pending error instead of list_conflicts'. Per
    server/routers/main.py's own comment on `git_service_exception_handler`
    ("converts it once for every route — present and future"), any endpoint that
    forgets a local guard around git_service is exactly the case that handler exists
    to protect, and this reproduces that shape faithfully.

    routers/main.py itself is intentionally not imported (heavy side-effecting
    imports; see test_cors_default_0371.py / test_git_service_error_envelope_0233.py
    for the same house convention): the handler body below is a byte-for-byte replica
    of routers/main.py's `git_service_exception_handler`, registered on a minimal app
    exactly as test_git_service_error_envelope_0233.py already does for the 0233
    endpoints. A regression that changes or removes the production handler is not
    caught by this replica — that limitation is inherent to the house pattern, not
    specific to this test — but unlike the previous revision, the handler under test
    here is actually invoked: it is what converts the 409, not dead code sitting next
    to a route that never reaches it.
    """
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.post("/api/v1/_test-only/git/finalize/{group_id}")
    async def _unguarded_finalize(group_id: str):  # noqa: ANN202
        # No try/except here — matches token_routes.post_token_issue /
        # ai_invoke_routes' start_run mention_builder, which call into git_service
        # with nothing standing between them and FastAPI's exception dispatch.
        return svc.finalize(group_id, "wait")

    @app.exception_handler(svc.GitServiceError)
    async def _handler(request: Request, exc: svc.GitServiceError):  # noqa: ANN202
        error: dict = {"code": exc.code, "message": exc.message}
        if exc.details:
            error["details"] = exc.details
        return JSONResponse(status_code=exc.status, content={"ok": False, "error": error})

    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post("/api/v1/_test-only/git/finalize/flowgate.default.0477.stale")

    assert resp.status_code == 409
    assert resp.json() == {
        "ok": False,
        "error": {
            "code": "invalid_state",
            "message": "final workflow approval is required before Git finalize",
        },
    }
    assert finalize_pending_state["calls"] == []
    finalize_pending_state["lock"].assert_not_called()
    finalize_pending_state["root_check"].assert_called_once_with(
        "flowgate.default.0477.stale"
    )
