"""flowgate.v02test.0042 regression: a card result's merge conflict is handled on the card.

1. A conflict found by the merge is deferred: the merge is undone, no group session opens (the group stays free for
   the other cards), the attempt waits in ``conflict`` / ``conflict_deferred`` with its overlapping files.
2. A person's choice asks the merge again keeping the conflict as the group's session (``resolve_requested``).
3. The card's merge state (``card_merge.service.view``) and the person's choices (``act``): states, ``blocked_by``,
   pick sides (group session: the group is ours / HEAD, the card result theirs / MERGE_HEAD), merge, discard & rerun,
   409 on a moved state.

SQLite and the real linked worktrees come from the 0061 fixtures (``merge_db``, ``repo``).
"""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.regression.graph_workflow.test_graph_attempt_runs_0061 import db  # noqa: F401 — shared SQLite fixture
from tests.regression.graph_attempt.test_graph_attempt_results_0061 import result_db, BASE, RESULT  # noqa: F401
from tests.regression.graph_attempt.test_graph_attempt_integration_0061 import (  # noqa: F401
    git, merge_db, pending_with_result, repo, status)
from flowgate.card_merge import service
from flowgate.graph_run import result_merge, workspace
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.store import run_store


# ── 1. the merge defers a conflict ────────────────────────────────────────────

@pytest.fixture
def real_merge(monkeypatch, repo):
    """``_merge`` on the real group worktree with the lock / session / claim boundaries stubbed."""
    monkeypatch.setattr(result_merge, "_group_root", lambda row: ("proj", Path(repo.root), "flow"))
    monkeypatch.setattr(result_merge.git_commit, "_acquire_group_lock", lambda *a, **k: object())
    monkeypatch.setattr(result_merge.git_commit, "_release_group_lock", lambda lock: None)
    monkeypatch.setattr(result_merge.git.db_git, "get_open_session_by_group", lambda flow: None)
    monkeypatch.setattr(result_merge.git, "get_branch_merge_group_claim", lambda flow: None)
    monkeypatch.setattr(result_merge, "_gate_snapshot_reserved", lambda flow: False)
    monkeypatch.setattr(result_merge.workspace, "location", lambda row, project: (None, None, "card"))
    monkeypatch.setattr(result_merge.workspace, "attempt_head", lambda row, project: None)
    opened = Mock(return_value={"merge_id": 9, "files": ["a.txt"]})
    monkeypatch.setattr(result_merge.conflict, "open_tr_conflict_session", opened)
    monkeypatch.setattr(result_merge.documents, "get_by_id", lambda doc_id: None)
    monkeypatch.setattr(result_merge.tr_commit_ledger, "by_attempt", lambda attempt_id: None)
    monkeypatch.setattr(result_merge, "_node", lambda row: {"result_doc_id": None})
    return opened


def row_of(result):
    return {"group_id": "flow", "result_commit": result, "attempt_id": "att", "node_key": "node", "run_id": "run"}


def test_conflict_is_undone_and_deferred_with_no_group_session(repo, real_merge):
    first = repo.branch("first", "a.txt", "first\n")
    other = repo.branch("other", "a.txt", "other\n")
    third = repo.branch("third", "b.txt", "third\n")
    assert result_merge._merge(row_of(first))[0] == result_merge.INTEGRATED
    head = git(repo.root, "rev-parse", "HEAD")

    assert result_merge._merge(row_of(other)) == (result_merge.DEFERRED, ["a.txt"])
    real_merge.assert_not_called()                                      # no session: the group stays free
    assert git(repo.root, "rev-parse", "HEAD") == head
    assert git(repo.root, "status", "--porcelain", "--untracked-files=no") == ""
    with pytest.raises(Exception):
        git(repo.root, "rev-parse", "-q", "--verify", "MERGE_HEAD")

    # a card that does not overlap merges meanwhile
    status_, merged = result_merge._merge(row_of(third))
    assert status_ == result_merge.INTEGRATED and git(repo.root, "rev-parse", "HEAD") == merged


def test_a_person_asked_resolution_keeps_the_conflict_as_the_group_session(repo, real_merge):
    first = repo.branch("first", "a.txt", "first\n")
    other = repo.branch("other", "a.txt", "other\n")
    result_merge._merge(row_of(first))
    try:
        assert result_merge._merge(row_of(other), keep_session=True) == (result_merge.CONFLICT, 9)
        real_merge.assert_called_once()
        assert git(repo.root, "rev-parse", "MERGE_HEAD") == other
    finally:
        result_merge._opened.pop("att", None)


def test_clean_merge_is_unchanged(repo, real_merge):
    first = repo.branch("first", "a.txt", "first\n")
    status_, merged = result_merge._merge(row_of(first))
    assert status_ == result_merge.INTEGRATED
    assert first in git(repo.root, "log", "-1", "--format=%P", merged).split()
    real_merge.assert_not_called()


def test_integrate_records_the_deferred_conflict_and_its_files(merge_db, monkeypatch):
    pending_with_result(merge_db)
    monkeypatch.setattr(result_merge.result_commit, "commit_result", Mock(return_value={"result_commit": RESULT}))
    merge = Mock(return_value=(result_merge.DEFERRED, ["a.txt", "b.txt"]))
    monkeypatch.setattr(result_merge, "_merge", merge)
    assert result_merge.integrate(merge_db.attempt) == result_merge.CONFLICT
    assert status(merge_db) == ("conflict", result_merge.CONFLICT_DEFERRED)
    assert merge.call_args.args[1] is False                             # no session kept
    note = result_merge._note.call_args_list[-1]
    assert note.args[1:] == ("conflict", result_merge.CONFLICT_DEFERRED) and note.kwargs["files"] == ["a.txt", "b.txt"]


def deferred(db, files=("a.txt",)):
    db._execute("UPDATE graph_node_attempts SET integration_status='conflict', integration_reason=?, "
                "result_commit=? WHERE attempt_id=?", [result_merge.CONFLICT_DEFERRED, RESULT, db.attempt])
    payload = {"attempt_id": db.attempt, "integration_reason": result_merge.CONFLICT_DEFERRED, "files": list(files)}
    db._execute("CREATE TABLE IF NOT EXISTS graph_run_events(run_id TEXT, event_seq INTEGER, event_type TEXT, "
                "node_key TEXT, from_status TEXT, to_status TEXT, cause TEXT, result_doc_id TEXT, canvas_state TEXT, "
                "actor_user_id TEXT, reason TEXT, payload_json TEXT, created_at TEXT)")
    db._execute("INSERT INTO graph_run_events(run_id, event_seq, event_type, node_key, from_status, to_status, cause, "
                "reason, payload_json, created_at) VALUES ('run', 950, 'graph_run_status_changed', NULL, 'running', "
                "'running', ?, 'conflict', ?, 'now')", [result_merge.CAUSE, json.dumps(payload)])


def test_resolution_request_merges_again_keeping_the_session(merge_db, monkeypatch):
    deferred(merge_db)
    monkeypatch.setattr(result_merge.result_commit, "commit_result", Mock(return_value={"result_commit": RESULT}))
    merge = Mock(return_value=(result_merge.CONFLICT, 9))
    monkeypatch.setattr(result_merge, "_merge", merge)
    monkeypatch.setattr(result_merge, "_session_open", lambda merge_id: True)
    monkeypatch.setattr(result_merge, "request", lambda flow, failed=False: result_merge.integrate(merge_db.attempt))
    assert result_merge.deferred_files(merge_db.attempt) == ["a.txt"]
    try:
        assert result_merge.request_resolution(merge_db.attempt) == "conflict"
    finally:
        result_merge._opened.pop(merge_db.attempt, None)
    assert merge.call_args.args[1] is True
    assert status(merge_db) == ("conflict", "merge_session:9")
    # a session conflict is not asked again
    assert result_merge.request_resolution(merge_db.attempt) == "conflict" and merge.call_count == 1


def test_the_drain_and_lock_releases_leave_a_deferred_conflict_alone(merge_db, monkeypatch):
    deferred(merge_db)
    monkeypatch.setattr(run_store, "active_run", lambda flow: run_store.run_by_id("run"))
    monkeypatch.setattr(result_merge, "_settle_closed", Mock(side_effect=AssertionError("no session to settle")))
    assert result_merge._closed_conflicts("flow") == set()
    assert status(merge_db) == ("conflict", result_merge.CONFLICT_DEFERRED)
    assert result_merge.session_pending("run") == []                   # a Stop waits for no session


def test_blocked_by_names_the_session_card_or_the_merging_card(merge_db, monkeypatch):
    merge_db._execute("UPDATE graph_node_attempts SET integration_status='pending', integration_reason='session_busy' "
                      "WHERE attempt_id=?", [merge_db.attempt])
    session = {"merge_id": 7, "status": "open"}
    monkeypatch.setattr(result_merge.db_git, "get_open_session_by_group", lambda flow: session)
    monkeypatch.setattr(result_merge.db_git, "session_kind", lambda s: result_merge.SESSION_KIND)
    monkeypatch.setattr(result_merge.db_git, "session_context", lambda s: {"node_key": "four"})
    wire = result_merge.integration_wire(merge_db.attempt)
    assert wire == {"integration_status": "pending", "integration_reason": "session_busy",
                    "integration_blocked_by": "four"}
    monkeypatch.setattr(result_merge.db_git, "get_open_session_by_group", lambda flow: None)
    assert result_merge.integration_wire(merge_db.attempt)["integration_blocked_by"] is None
    second = run_store.open_attempt("run", "second", 1)
    merge_db._execute("UPDATE graph_node_attempts SET integration_status='integrating' WHERE attempt_id=?", [second])
    assert result_merge.integration_wire(merge_db.attempt)["integration_blocked_by"] == "second"
    assert result_merge.integration_wire(second)["integration_blocked_by"] is None     # not pending
    assert result_merge.integration_wire(None)["integration_blocked_by"] is None


# ── 3. the card's merge state and choices ─────────────────────────────────────

@pytest.fixture
def card(merge_db, monkeypatch):
    monkeypatch.setattr(run_store, "current_run", lambda flow: run_store.run_by_id("run"))
    monkeypatch.setattr(service, "_title", lambda flow, key: {"four": "업로드 대기열 이전"}.get(key, key))
    monkeypatch.setattr(service, "_default_provider", lambda flow, key: {"id": "aip_x", "name": "Claude"})
    monkeypatch.setattr(service, "_resolving", lambda flow: None)
    return merge_db


def set_status(db, status_, reason=None):
    db._execute("UPDATE graph_node_attempts SET integration_status=?, integration_reason=?, result_commit=? "
                "WHERE attempt_id=?", [status_, reason, RESULT, db.attempt])


def open_session(monkeypatch, review_state="open", resolved=(False,)):
    session = {"merge_id": 7, "status": "open"}
    monkeypatch.setattr(service, "_open_session", lambda row: session)
    monkeypatch.setattr(service.db_git, "session_context", lambda s: {"review_state": review_state})
    monkeypatch.setattr(service.db_git, "session_kind", lambda s: "graph_attempt_merge")
    monkeypatch.setattr(service.db_git, "session_files",
                        lambda merge_id: [{"path": f"f{i}.py", "resolved": int(r)} for i, r in enumerate(resolved)])


def test_view_states(card, monkeypatch):
    assert service.view("flow", "node")["state"] == service.HIDDEN
    set_status(card, "integrated")
    assert service.view("flow", "node")["state"] == service.HIDDEN

    set_status(card, "pending", "session_busy")
    monkeypatch.setattr(service.rm, "blocked_by", lambda row: "four")
    got = service.view("flow", "node")
    assert got["state"] == service.WAITING and got["actions"] == []
    assert got["blocked_by"] == {"node_key": "four", "title": "업로드 대기열 이전"}

    set_status(card, "integrating")
    assert service.view("flow", "node")["state"] == service.CATCHING_UP

    set_status(card, "failed", "conflict_aborted")
    got = service.view("flow", "node")
    assert (got["state"], got["actions"], got["reason"]) == (service.FAILED, ["discard_rerun"], "conflict_aborted")


def test_view_of_a_deferred_conflict_lists_its_files(card):
    deferred(card, ("a.txt", "b.txt"))
    got = service.view("flow", "node")
    assert got["state"] == service.NEEDS_CHOICE and got["session"] is None
    assert got["actions"] == ["ai_resolve", "pick", "discard_rerun"]
    assert got["files"] == [{"path": "a.txt", "resolved": False}, {"path": "b.txt", "resolved": False}]
    assert got["default_provider"] == {"id": "aip_x", "name": "Claude"}


def test_view_of_a_session_conflict(card, monkeypatch):
    set_status(card, "conflict", "merge_session:7")
    open_session(monkeypatch, resolved=(True, False))
    got = service.view("flow", "node")
    assert got["state"] == service.NEEDS_CHOICE and "merge" not in got["actions"]
    assert got["session"] == {"merge_id": 7, "kind": "graph_attempt_merge", "review_state": "open"}
    open_session(monkeypatch, review_state="resolved", resolved=(True, True))
    assert service.view("flow", "node")["actions"] == ["ai_resolve", "pick", "merge", "discard_rerun"]
    monkeypatch.setattr(service, "_resolving", lambda flow: {"run_id": "aiv_1"})
    got = service.view("flow", "node")
    assert got["state"] == service.CATCHING_UP and got["actions"] == [] and got["resolving"] == {"run_id": "aiv_1"}


def body(action, card_db, **extra):
    return {"action": action, "expected_attempt_id": card_db.attempt, **extra}


def test_a_moved_state_or_an_action_not_offered_is_refused(card):
    deferred(card)
    with pytest.raises(GraphApiError) as exc:
        service.act("flow", "node", "u", {"action": "ai_resolve", "expected_attempt_id": "old"})
    assert (exc.value.status, exc.value.code) == (409, "merge_state_changed")
    with pytest.raises(GraphApiError) as exc:
        service.act("flow", "node", "u", body("merge", card))
    assert exc.value.code == "merge_state_changed"
    with pytest.raises(GraphApiError) as exc:
        service.act("flow", "node", "u", body("pick", card, files=[{"path": "a.txt", "side": "theirs"}]))
    assert (exc.value.status, exc.value.code) == (422, "invalid_request")


def test_a_deferred_conflict_opens_its_session_before_the_ai_starts(card, monkeypatch):
    deferred(card)

    def resolution(attempt_id):
        set_status(card, "conflict", "merge_session:7")
        open_session(monkeypatch)
        return "conflict"
    monkeypatch.setattr(service.rm, "request_resolution", resolution)
    start = Mock(return_value="aiv_9")
    monkeypatch.setattr(service, "_start_ai", start)
    got = service.act("flow", "node", "u", body("ai_resolve", card))
    assert got["started_run_id"] == "aiv_9"
    assert start.call_args.args[:2] == ("flow", 7) and start.call_args.args[4] is None   # default AI


def test_a_busy_group_is_told_and_nothing_starts(card, monkeypatch):
    deferred(card)

    def resolution(attempt_id):
        set_status(card, "pending", "session_busy")
        return "pending"
    monkeypatch.setattr(service.rm, "request_resolution", resolution)
    monkeypatch.setattr(service, "_start_ai", Mock(side_effect=AssertionError("no AI on a busy group")))
    with pytest.raises(GraphApiError) as exc:
        service.act("flow", "node", "u", body("ai_resolve", card))
    assert (exc.value.status, exc.value.code) == (409, "group_busy")


def test_pick_sides_in_the_group_session(card, monkeypatch, repo):
    """The group session merges the card result (MERGE_HEAD) into the group (HEAD): card = theirs, latest = ours."""
    first = repo.branch("first", "a.txt", "first\n")
    other = repo.branch("other", "a.txt", "other\n")
    git(repo.root, "merge", "--no-ff", "-m", "first", first)
    with pytest.raises(Exception):
        git(repo.root, "merge", "--no-ff", "-m", "other", other)          # conflict, MERGE_HEAD = the card result
    assert service._side_content(Path(repo.root), service.SESSION_SIDES["card"][1], "a.txt") == "other\n"
    assert service._side_content(Path(repo.root), service.SESSION_SIDES["latest"][1], "a.txt") == "first\n"
    assert service._side_content(Path(repo.root), "HEAD", "missing.txt") is None

    from modules.flow_gate.services import git_service
    from modules.flow_gate.services.git import conflict
    monkeypatch.setattr(git_service, "_session_context", lambda flow, merge_id: ({}, {}, "proj", Path(repo.root)))
    monkeypatch.setattr(service.db_git, "session_files", lambda merge_id: [{"path": "a.txt", "resolved": 0}])
    apply = Mock()
    monkeypatch.setattr(conflict, "_apply_conflict_resolution_locked", apply)
    service._apply_picks("flow", 7, [{"path": "a.txt", "side": "card"}])
    staged, complete, supersedes = apply.call_args.args[2], apply.call_args.args[3], apply.call_args.args[4]
    assert [(p, c) for p, _t, c, _o in staged] == [("a.txt", "other\n")] and complete is True
    assert supersedes == [{"path": "a.txt", "side": "theirs", "reason": service.PICK_REASON, "chunks": []}]
    service._apply_picks("flow", 7, [{"path": "a.txt", "side": "latest"}])
    assert apply.call_args.args[2][0][2] == "first\n" and apply.call_args.args[4][0]["side"] == "ours"
    with pytest.raises(GraphApiError) as exc:
        service._apply_picks("flow", 7, [{"path": "zzz.txt", "side": "card"}])
    assert exc.value.code == "merge_state_changed"


def test_pick_applies_the_chosen_sides_and_leaves_the_rest_to_one_ai_run(card, monkeypatch):
    set_status(card, "conflict", "merge_session:7")
    open_session(monkeypatch, resolved=(False, False))
    picks, start = Mock(), Mock(return_value="aiv_2")
    monkeypatch.setattr(service, "_apply_picks", picks)
    monkeypatch.setattr(service, "_start_ai", start)
    got = service.act("flow", "node", "u", body("pick", card, files=[
        {"path": "f0.py", "side": "latest"}, {"path": "f1.py", "side": "ai"}], provider_id="aip_y"))
    picks.assert_called_once_with("flow", 7, [{"path": "f0.py", "side": "latest"}])
    assert start.call_args.args[4] == "aip_y" and start.call_args.args[6] == ["f1.py"]
    assert got["started_run_id"] == "aiv_2"


def test_merge_commits_the_resolved_session(card, monkeypatch):
    set_status(card, "conflict", "merge_session:7")
    open_session(monkeypatch, review_state="resolved", resolved=(True,))
    from modules.flow_gate.services import tr_commit_service
    commit = Mock(return_value={"ok": True})
    monkeypatch.setattr(tr_commit_service, "commit_conflict_resolution", commit)
    service.act("flow", "node", "u", body("merge", card))
    commit.assert_called_once_with("flow", 7)


def test_discard_rerun_aborts_the_session_then_retries_on_the_latest_code(card, monkeypatch):
    set_status(card, "conflict", "merge_session:7")
    open_session(monkeypatch)
    from modules.flow_gate.services import tr_commit_service
    from flowgate.graph_run import recovery, recovery_basis

    def abort(flow, merge_id):
        set_status(card, "failed", "conflict_aborted")
        monkeypatch.setattr(service, "_open_session", lambda row: None)
        return {"ok": True}
    monkeypatch.setattr(tr_commit_service, "abort_conflict_resolution", Mock(side_effect=abort))
    from modules.flow_gate.db import groups as db_groups
    monkeypatch.setattr(db_groups, "get_by_id", lambda flow: {"group_id": "flow", "project_id": "proj"})
    monkeypatch.setattr(recovery_basis, "view", lambda group, key: {"context": {"run_id": "run"}})
    retry = Mock(return_value={"created": True})
    monkeypatch.setattr(recovery, "retry", retry)
    recheck = Mock()
    service.act("flow", "node", "u", body("discard_rerun", card), recheck=recheck)
    tr_commit_service.abort_conflict_resolution.assert_called_once_with("flow", 7)
    group, user, key, sent, passed = retry.call_args.args
    assert (group["group_id"], user, key, sent["expected_context"], passed) == ("flow", "u", "node", {"run_id": "run"},
                                                                              recheck)
    assert sent["request_id"].startswith("merge-discard-")


def test_merge_items_list_the_waiting_cards_of_readable_flows(card, monkeypatch):
    deferred(card)
    from modules.flow_gate.db import groups as db_groups
    monkeypatch.setattr(db_groups, "get_by_id", lambda flow: {"group_id": flow, "project_id": "proj", "title": "편의성"})
    items = service.merge_items("u", lambda uid, project: True)
    assert items == [{"flow_id": "flow", "flow_title": "편의성", "node_key": "node", "title": "node",
                      "state": "needs_choice", "reason": "conflict_deferred", "blocked_by": None}]
    assert service.merge_items("u", lambda uid, project: False) == []
