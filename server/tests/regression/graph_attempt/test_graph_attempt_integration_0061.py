"""0061 T#9 regression specifications. TR compiles; TS/TSR runs behavior.

Rework (rej_01M4VTZNHWR9JHX9): the session close notice (an early close while integrating, a late close of an older
session, the wait for the thread's Git locks), the command / idle integration hold, the Stop that keeps a session it
could not abort, failed integrations requeued only by the drain.

SQLite uses the real additive migrations (151/152/086d/153a through the T#8 fixture). The merge itself runs on a
real linked group worktree; Flow guard, run context, lock and event boundaries are controlled by fixtures.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import shutil
import subprocess

import pytest

from tests.regression.graph_workflow.test_graph_attempt_runs_0061 import db  # noqa: F401 — shared SQLite execution fixture
from tests.test_graph_attempt_results_0061 import result_db, BASE, RESULT  # noqa: F401 — T#8 result fixture
from flowgate.graph_run import result_merge, settle, stop, workspace
from modules.flow_gate.graph_workflow.domain.frontier import Decision, Transition
from modules.flow_gate.graph_workflow.services import node_approval
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services import git_service, tr_commit_service
from modules.flow_gate.services.git import branches
from modules.flow_gate.services.git.credentials import GitServiceError


def approval(node="node"):
    t = Transition(node, "review", "done", "review_approved", "doc", "doc")
    return Decision(node_transitions=[t])


@pytest.fixture
def merge_db(result_db, monkeypatch):
    def guard(_flow, body):
        with result_db.transaction():
            return body()
    monkeypatch.setattr(result_merge, "with_flow_guard", guard)
    monkeypatch.setattr(result_merge, "_note", Mock())
    monkeypatch.setattr(workspace, "_mode", lambda row: {"mode": "worktree"})
    monkeypatch.setattr(result_merge, "_after", Mock())
    return result_db


def status(db):
    row = workspace.attempt(db.attempt)
    return row["integration_status"], row["integration_reason"]


def test_worktree_approval_is_deferred_and_requested_once(merge_db):
    ctx = SimpleNamespace(run_id="run", flow_id="flow", jobs=[])
    assert result_merge.defer_done(ctx, approval())
    assert status(merge_db) == ("pending", "approved") and len(ctx.jobs) == 1
    assert result_merge.defer_done(ctx, approval()) and len(ctx.jobs) == 1     # waiting: no second request
    assert result_merge.integration_waiting("run", "node")
    row = run_store.node_runs("run")[0]
    assert node_approval.refusal("flow", row) == node_approval.INTEGRATION_WAITING


@pytest.mark.parametrize("mode", [None, {"mode": "document_only"}])
def test_document_only_or_unprepared_attempt_is_done_at_once(merge_db, monkeypatch, mode):
    monkeypatch.setattr(workspace, "_mode", lambda row: mode)
    ctx = SimpleNamespace(run_id="run", flow_id="flow", jobs=[])
    assert not result_merge.defer_done(ctx, approval())
    assert status(merge_db) == (None, None) and not ctx.jobs


def test_integrated_attempt_lets_the_approval_through(merge_db):
    merge_db._execute("UPDATE graph_node_attempts SET integration_status='integrated' WHERE attempt_id=?",
                      [merge_db.attempt])
    ctx = SimpleNamespace(run_id="run", flow_id="flow", jobs=[])
    assert not result_merge.defer_done(ctx, approval())
    assert not result_merge.integration_waiting("run", "node")


def pending_with_result(db, result=RESULT):
    db._execute("UPDATE graph_node_attempts SET integration_status='pending', result_commit=? "
                "WHERE attempt_id=?", [result, db.attempt])


def test_no_source_change_finishes_without_merge(merge_db, monkeypatch):
    pending_with_result(merge_db, BASE)
    monkeypatch.setattr(result_merge.result_commit, "commit_result", Mock(return_value={"result_commit": BASE}))
    merge = Mock(side_effect=AssertionError("no merge for an unchanged result"))
    monkeypatch.setattr(result_merge, "_merge", merge)
    finish = Mock(return_value="integrated")
    monkeypatch.setattr(result_merge, "_finish", finish)
    assert result_merge.integrate(merge_db.attempt) == "integrated"
    finish.assert_called_once_with(merge_db.attempt, BASE, ("pending",))


@pytest.mark.parametrize("merged,expected", [
    (("pending", "session_busy"), ("pending", "session_busy")),
    (("pending", "git_busy"), ("pending", "git_busy")),
    (("conflict", 7), ("conflict", "merge_session:7")),
    (("failed", "group_worktree_dirty"), ("failed", "group_worktree_dirty")),
])
def test_merge_outcomes_are_recorded_not_raised(merge_db, monkeypatch, merged, expected):
    pending_with_result(merge_db)
    monkeypatch.setattr(result_merge.result_commit, "commit_result", Mock(return_value={"result_commit": RESULT}))
    monkeypatch.setattr(result_merge, "_merge", Mock(return_value=merged))
    assert result_merge.integrate(merge_db.attempt) == merged[0]
    assert status(merge_db) == expected


def test_stopping_run_starts_no_integration(merge_db, monkeypatch):
    pending_with_result(merge_db)
    merge_db._execute("UPDATE graph_runs SET status='stopping' WHERE run_id='run'")
    commit = Mock(side_effect=AssertionError("a stopping run commits nothing"))
    monkeypatch.setattr(result_merge.result_commit, "commit_result", commit)
    assert result_merge.integrate(merge_db.attempt) == "skipped"
    assert status(merge_db)[0] == "pending"


def test_held_merges_keep_run_idle_as_merge_conflict(merge_db):
    merge_db._execute("UPDATE graph_node_attempts SET integration_status='failed', "
                      "integration_reason='conflict_aborted' WHERE attempt_id=?", [merge_db.attempt])
    assert result_merge.held("run") and not result_merge.in_progress("run")
    merge_db._execute("UPDATE graph_node_attempts SET integration_status='integrating' WHERE attempt_id=?",
                      [merge_db.attempt])
    assert result_merge.in_progress("run") and not result_merge.held("run")
    assert settle.IDLE_MERGE_CONFLICT in settle.IDLE_REASONS


def test_retry_asks_waiting_integrations_in_attempt_order(merge_db, monkeypatch):
    second = run_store.open_attempt("run", "second", 1)
    merge_db._execute("UPDATE graph_node_attempts SET integration_status='failed' WHERE attempt_id=?",
                      [merge_db.attempt])
    merge_db._execute("UPDATE graph_node_attempts SET integration_status='pending' WHERE attempt_id=?", [second])
    monkeypatch.setattr(run_store, "active_run", lambda flow: run_store.run_by_id("run"))
    seen = []
    monkeypatch.setattr(result_merge, "integrate", lambda a: seen.append(a) or "pending")
    result_merge.retry("flow")
    assert seen == [merge_db.attempt, second]
    assert status(merge_db) == ("pending", "retry")


def closed_session(monkeypatch, db, status="aborted", merge_id=7):
    session = {"merge_id": merge_id, "status": status, "group_id": "flow", "kind": result_merge.SESSION_KIND}
    monkeypatch.setattr(result_merge.db_git, "get_session", lambda _id: session)
    monkeypatch.setattr(result_merge.db_git, "session_kind", lambda _s: result_merge.SESSION_KIND)
    monkeypatch.setattr(result_merge.db_git, "session_context",
                        lambda _s: {"attempt_id": db.attempt, "result_commit": RESULT})
    monkeypatch.setattr(result_merge, "_group_root", lambda row: (None, None, None))   # result not merged
    retry = Mock()
    monkeypatch.setattr(result_merge, "retry", retry)
    return retry


def test_aborted_session_fails_its_attempt_and_is_not_retried_at_once(merge_db, monkeypatch):
    merge_db._execute("UPDATE graph_node_attempts SET integration_status='conflict', "
                      "integration_reason='merge_session:7', result_commit=? WHERE attempt_id=?",
                      [RESULT, merge_db.attempt])
    retry = closed_session(monkeypatch, merge_db)
    result_merge.session_closed("flow", 7)
    assert status(merge_db) == ("failed", "conflict_aborted")
    retry.assert_called_once_with("flow", {merge_db.attempt})


def test_session_closed_before_its_conflict_is_recorded_is_not_lost(merge_db, monkeypatch):
    merge_db._execute("UPDATE graph_node_attempts SET integration_status='integrating', result_commit=? "
                      "WHERE attempt_id=?", [RESULT, merge_db.attempt])
    closed_session(monkeypatch, merge_db)
    result_merge._opened[merge_db.attempt] = 7                  # opened by this integration, conflict not recorded
    try:
        result_merge.session_closed("flow", 7)
    finally:
        result_merge._opened.pop(merge_db.attempt, None)
    assert status(merge_db) == ("failed", "conflict_aborted")
    # the integration's own record comes late and changes nothing
    assert not result_merge._set(merge_db.attempt, "conflict", "merge_session:7", ("integrating",))


def test_late_close_of_an_older_session_leaves_a_new_merge_alone(merge_db, monkeypatch):
    merge_db._execute("UPDATE graph_node_attempts SET integration_status='integrating', result_commit=? "
                      "WHERE attempt_id=?", [RESULT, merge_db.attempt])
    closed_session(monkeypatch, merge_db, merge_id=6)
    result_merge.session_closed("flow", 6)
    assert status(merge_db)[0] == "integrating"


def test_session_close_notice_waits_for_the_threads_git_locks(monkeypatch):
    monkeypatch.setattr(result_merge.db_git, "get_session", lambda _id: {"group_id": "flow", "status": "done"})
    monkeypatch.setattr(result_merge.db_git, "is_branch_merge_session", lambda _s: False)
    taken = Mock()
    monkeypatch.setattr(result_merge, "session_closed", taken)
    held = SimpleNamespace(held=["G"])
    monkeypatch.setattr(result_merge.lock_manager, "current_context", lambda: held)
    result_merge.on_session_closed(7, "done")
    taken.assert_not_called()                                   # still under G
    held.held = []
    result_merge.on_lock_released(None)
    taken.assert_called_once_with("flow", 7)


def test_command_holds_while_integrations_wait_and_failed_stay_failed(merge_db):
    merge_db._execute("UPDATE graph_node_attempts SET integration_status='failed', "
                      "integration_reason='conflict_aborted' WHERE attempt_id=?", [merge_db.attempt])
    ctx = SimpleNamespace(run_id="run", flow_id="flow", jobs=[])
    assert result_merge.retry_integrations(ctx) and len(ctx.jobs) == 1
    assert status(merge_db) == ("failed", "conflict_aborted")    # requeued only when the drain takes it
    merge_db._execute("UPDATE graph_node_attempts SET integration_status='conflict' WHERE attempt_id=?",
                      [merge_db.attempt])
    assert not result_merge.retry_integrations(SimpleNamespace(run_id="run", flow_id="flow", jobs=[]))


def event(db, seq, from_status, to_status, cause, reason=None):
    db._execute("INSERT INTO graph_run_events(run_id, event_seq, event_type, node_key, from_status, to_status, "
                "cause, reason, payload_json, created_at) VALUES ('run', ?, 'graph_run_status_changed', NULL, ?, ?, "
                "?, ?, '{}', 'now')", [seq, from_status, to_status, cause, reason])


def test_hold_lasts_until_its_end_or_the_run_leaves_running(merge_db):
    event(merge_db, 901, "idle", "running", result_merge.HOLD_CAUSE, result_merge.HOLD_IDLE)
    event(merge_db, 902, "running", "running", result_merge.CAUSE, "integrating")
    assert result_merge.hold_of("run") == result_merge.HOLD_IDLE
    event(merge_db, 903, "running", "idle", "run_idle", "merge_conflict")
    assert result_merge.hold_of("run") is None
    event(merge_db, 904, "running", "running", result_merge.HOLD_CAUSE, "command:step")
    assert result_merge.hold_of("run") == "command:step"
    event(merge_db, 905, "running", "stopping", "user_stop")
    assert result_merge.hold_of("run") is None


def test_stop_keeps_a_session_it_could_not_abort(merge_db, monkeypatch):
    merge_db._execute("UPDATE graph_node_attempts SET integration_status='conflict', "
                      "integration_reason='merge_session:7', result_commit=? WHERE attempt_id=?",
                      [RESULT, merge_db.attempt])
    monkeypatch.setattr(result_merge.db_git, "get_session", lambda _id: {"merge_id": 7, "status": "open"})
    monkeypatch.setattr(result_merge.conflict, "abort_tr_conflict",
                        Mock(side_effect=GitServiceError(409, "git_busy", "busy")))
    assert result_merge.abort_for_stop("flow", "run") == [merge_db.attempt]
    assert status(merge_db) == ("conflict", "merge_session:7")    # not failed, not invalidated
    assert result_merge.session_pending("run") == [merge_db.attempt]


def test_stop_leaves_an_integrating_attempt_to_its_end(merge_db, monkeypatch):
    merge_db._execute("UPDATE graph_node_attempts SET integration_status='integrating' WHERE attempt_id=?",
                      [merge_db.attempt])
    other = run_store.open_attempt("run", "other", 1)
    monkeypatch.setattr(stop, "_revoke", Mock())
    monkeypatch.setattr(stop, "_context", Mock())
    with merge_db.transaction():
        stop._settle_executions({"run_id": "run", "group_id": "flow"}, None)
    assert workspace.attempt(merge_db.attempt)["status"] == "active"
    assert workspace.attempt(other)["status"] == "invalidated"


def test_conflict_session_kind_reuses_tr_screen():
    kinds = git_service.db_git
    assert kinds.SESSION_KIND_GRAPH_ATTEMPT_MERGE in kinds.TR_SESSION_KINDS
    assert kinds.SESSION_KIND_GRAPH_ATTEMPT_MERGE in kinds.SESSION_KINDS


def test_graph_session_commit_skips_ledger_and_rewind_continuation(monkeypatch):
    outcome = {"result": {"kind": git_service.db_git.SESSION_KIND_GRAPH_ATTEMPT_MERGE, "commit": RESULT,
                          "status": "committed", "ledger_row_id": 3}}
    monkeypatch.setattr(git_service, "commit_tr_conflict", Mock(return_value=outcome))
    monkeypatch.setattr(tr_commit_service, "cancel_retry", Mock(side_effect=AssertionError("no rewind")))
    monkeypatch.setattr(tr_commit_service, "reapply_retry", Mock(side_effect=AssertionError("no reapply")))
    # the Graph integration goes on from the session's close notice (db_git.on_session_closed), not from here
    assert tr_commit_service.commit_conflict_resolution("flow", 7)["ok"]


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


@pytest.fixture
def repo(tmp_path):
    if not shutil.which("git"):
        pytest.skip("Git not installed in TS environment")
    root = tmp_path / "group"
    root.mkdir()
    git(root, "init", "-b", "flow")
    git(root, "config", "user.name", "Merge Test")
    git(root, "config", "user.email", "merge@example.invalid")
    (root / "a.txt").write_text("base\n", encoding="utf-8")
    (root / "b.txt").write_text("base\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "chore: base")
    base = git(root, "rev-parse", "HEAD")

    def branch(name: str, path: str, text: str) -> str:
        tree = tmp_path / name
        git(root, "worktree", "add", "-b", name, str(tree), base)
        (tree / path).write_text(text, encoding="utf-8")
        git(tree, "commit", "-am", f"feat: {name}")
        return git(tree, "rev-parse", "HEAD")
    return SimpleNamespace(root=root, base=base, branch=branch)


def test_two_branches_merge_only_their_own_commit(repo):
    first = repo.branch("first", "a.txt", "first\n")
    second = repo.branch("second", "b.txt", "second\n")
    assert branches.merge_no_ff(repo.root, first, "Merge branch 'first' into flow", "first", "flow") == []
    assert branches.merge_no_ff(repo.root, second, "Merge branch 'second' into flow", "second", "flow") == []
    parents = git(repo.root, "log", "--merges", "--format=%P").splitlines()
    assert any(first in line.split() for line in parents) and any(second in line.split() for line in parents)
    assert result_merge._merge_commit_of(Path(repo.root), first) != git(repo.root, "rev-parse", "HEAD")


def test_conflicting_branch_keeps_merge_in_flight(repo):
    first = repo.branch("first", "a.txt", "first\n")
    other = repo.branch("other", "a.txt", "other\n")
    assert branches.merge_no_ff(repo.root, first, "Merge branch 'first' into flow", "first", "flow") == []
    files = branches.merge_no_ff(repo.root, other, "Merge branch 'other' into flow", "other", "flow")
    assert files == ["a.txt"]
    assert git(repo.root, "rev-parse", "MERGE_HEAD") == other
