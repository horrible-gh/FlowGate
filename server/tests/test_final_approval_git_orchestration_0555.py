"""Final-approval/Git orchestration contract for flowgate.default.0555 T0006."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from types import SimpleNamespace

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.flow_gate.services import git_service
from modules.flow_gate.workflow.routers import workflow


DOC = {
    "doc_id": "demo.default.0555.0001-AC",
    "project_id": "demo",
    "group_id": "demo.default.0555",
    "target_id": "demo.default.0555.0001-R",
    "type_code": "AC",
    "doc_review_status": "pending_review",
}
USER = {"user_id": "reviewer", "is_admin": True}


def _payload(response):
    return json.loads(response.body.decode("utf-8"))


def _install_orchestration_fakes(monkeypatch, outcome, commit, events, parked=None):
    # 0555 T0008: before it commits, the orchestrator asks whether a conflict of
    # its own parked an approval intent it now has to consume (D0005 §3.9). These
    # tests own no database, so that lookup is the fake's responsibility too;
    # `parked` is (session, intent) for the recovery case and (None, None) for the
    # ordinary one.
    monkeypatch.setattr(
        workflow.git_service, "approval_intent",
        SimpleNamespace(
            find_intent_session=lambda group_id: parked or (None, None),
            consume_intent=lambda merge_id, intent_id: events.append(
                f"consume:{merge_id}:{intent_id}") or True,
        ),
    )
    monkeypatch.setattr(workflow, "_guard_group_not_disposed", lambda *a: None)
    monkeypatch.setattr(workflow, "_guard_group_not_ai_running", lambda *a: None)
    monkeypatch.setattr(workflow.db_docs, "get_by_id", lambda doc_id: dict(DOC))
    monkeypatch.setattr(workflow, "precheck_document_review_transition", lambda **kw: events.append("precheck") or {"document": dict(DOC)})
    monkeypatch.setattr(workflow.git_service, "precheck_approve_git_action", lambda doc, action: DOC["group_id"])
    monkeypatch.setattr(workflow.git_service, "_project_of_group", lambda group_id: "demo")
    monkeypatch.setattr(workflow.git_service, "_acquire_lock", lambda *a, **kw: events.append("lock") or True)
    monkeypatch.setattr(workflow.git_service.db_git, "release_lock", lambda *a: events.append("release"))
    monkeypatch.setattr(workflow.git_service, "run_approve_git_action", lambda *a, **kw: events.append("git") or outcome)
    monkeypatch.setattr(workflow, "commit_final_approval", lambda **kw: events.append("approval") or commit())
    monkeypatch.setattr(workflow.git_service, "complete_approve_git_action", lambda *a, **kw: events.append("complete"))
    monkeypatch.setattr(workflow.git_service, "realize_wf_done_transition", lambda *a: events.append("realize"))


def test_wait_and_commit_only_are_rejected_only_for_final_approval(monkeypatch):
    monkeypatch.setattr(git_service.db_git, "get_config", lambda project_id: {"enabled": 1})
    monkeypatch.setattr(git_service.db_git, "get_state", lambda group_id: {"worktree_registered": 1})
    for action in ("wait", "commit_only"):
        with pytest.raises(git_service.GitServiceError) as exc:
            git_service.precheck_approve_git_action(DOC, action)
        assert (exc.value.status, exc.value.code) == (422, "invalid_request")
    assert "wait" in git_service.ACTION_VALUES
    assert "commit_only" in git_service.ACTION_VALUES


def test_clean_git_finishes_before_atomic_approval_and_holds_lock(monkeypatch):
    events = []
    outcome = {"ok": True, "terminal": True, "result": {"status": "merged", "merge_commit": "abc"}}
    committed = {"document": {**DOC, "doc_review_status": "approved"}, "root": {"doc_review_status": "wf_done"}}
    _install_orchestration_fakes(monkeypatch, outcome, lambda: committed, events)

    response = asyncio.run(workflow.document_review_transition_rpc(
        "approve", workflow.DocumentBodyRequest(doc_id=DOC["doc_id"], git_action="merge"), USER, None
    ))
    body = _payload(response)
    assert response.status_code == 200
    assert body["approval"] == {
        "approved": True, "document_status": "approved", "root_status": "wf_done",
        "stage": "complete", "deferred": False,
    }
    assert events[:5] == ["precheck", "lock", "git", "approval", "release"]
    assert events.count("git") == 1


def test_git_failure_never_commits_approval(monkeypatch):
    events = []
    outcome = {"ok": False, "terminal": False, "http_status": 409, "error": {"code": "base_dirty", "message": "dirty", "details": {"files": ["x.py"]}}}
    _install_orchestration_fakes(monkeypatch, outcome, lambda: pytest.fail("approval must not run"), events)

    response = asyncio.run(workflow.document_review_transition_rpc(
        "approve", workflow.DocumentBodyRequest(doc_id=DOC["doc_id"], git_action="merge"), USER, None
    ))
    body = _payload(response)
    assert response.status_code == 409
    assert body["error"]["code"] == "base_dirty"
    assert body["approval"]["approved"] is False
    assert body["approval"]["stage"] == "git_finalize"
    assert events == ["precheck", "lock", "git", "release"]


def test_conflict_is_deferred_without_approval(monkeypatch):
    events = []
    outcome = {"ok": True, "terminal": False, "deferred": True, "result": {"status": "conflict", "merge_id": 7, "conflict_files": ["x.py"]}}
    _install_orchestration_fakes(monkeypatch, outcome, lambda: pytest.fail("approval must not run"), events)

    response = asyncio.run(workflow.document_review_transition_rpc(
        "approve", workflow.DocumentBodyRequest(doc_id=DOC["doc_id"], git_action="merge"), USER, None
    ))
    body = _payload(response)
    assert response.status_code == 200
    assert body["approval"]["deferred"] is True
    assert body["approval"]["document_status"] == "pending_review"
    assert events == ["precheck", "lock", "git", "release"]


def test_approval_commit_failure_preserves_terminal_git_for_retry(monkeypatch):
    events = []
    outcome = {"ok": True, "terminal": True, "result": {"status": "merged", "merge_commit": "abc", "terminal_retry": True}}
    _install_orchestration_fakes(monkeypatch, outcome, lambda: (_ for _ in ()).throw(RuntimeError("root CAS failed")), events)

    response = asyncio.run(workflow.document_review_transition_rpc(
        "approve", workflow.DocumentBodyRequest(doc_id=DOC["doc_id"], git_action="merge"), USER, None
    ))
    body = _payload(response)
    assert response.status_code == 500
    assert body["error"]["code"] == "approval_commit_failed"
    assert body["git"]["terminal"] is True
    assert body["git"]["result"]["terminal_retry"] is True
    assert body["approval"]["stage"] == "approval_commit"
    assert events == ["precheck", "lock", "git", "approval", "release"]


def test_terminal_retry_consumes_the_intent_its_own_conflict_parked(monkeypatch):
    """0555 T0008 §9 — the D0005 §3.9 re-approval finishes the parked intent too.

    Approving without consuming would leave a live intent on the closed session,
    and the next merge review to read it would approve the same document twice.
    """
    events = []
    outcome = {"ok": True, "terminal": True,
               "result": {"status": "merged", "merge_commit": "abc", "terminal_retry": True}}
    committed = {"document": {**DOC, "doc_review_status": "approved"}, "root": {"doc_review_status": "wf_done"}}
    parked = (
        {"merge_id": 7},
        {"approval_intent_id": "intent-1", "ac_doc_id": DOC["doc_id"]},
    )
    captured = {}

    def _commit(**kwargs):
        events.append("approval")
        captured["hook"] = kwargs.get("consume_hook")
        return committed

    _install_orchestration_fakes(monkeypatch, outcome, lambda: committed, events, parked=parked)
    monkeypatch.setattr(workflow, "commit_final_approval", _commit)

    response = asyncio.run(workflow.document_review_transition_rpc(
        "approve", workflow.DocumentBodyRequest(doc_id=DOC["doc_id"], git_action="merge"), USER, None
    ))
    assert response.status_code == 200
    assert _payload(response)["approval"]["approved"] is True
    assert captured["hook"] is not None
    assert captured["hook"]({}, {}) is True
    assert "consume:7:intent-1" in events


def test_terminal_without_a_parked_intent_passes_no_consume_hook(monkeypatch):
    """The ordinary (never-conflicted) approval keeps its T#1 shape exactly."""
    events = []
    outcome = {"ok": True, "terminal": True, "result": {"status": "merged", "merge_commit": "abc"}}
    committed = {"document": {**DOC, "doc_review_status": "approved"}, "root": {"doc_review_status": "wf_done"}}
    captured = {}

    def _commit(**kwargs):
        events.append("approval")
        captured["hook"] = kwargs.get("consume_hook")
        return committed

    _install_orchestration_fakes(monkeypatch, outcome, lambda: committed, events)
    monkeypatch.setattr(workflow, "commit_final_approval", _commit)

    response = asyncio.run(workflow.document_review_transition_rpc(
        "approve", workflow.DocumentBodyRequest(doc_id=DOC["doc_id"], git_action="merge"), USER, None
    ))
    assert response.status_code == 200
    assert captured["hook"] is None
    assert not [e for e in events if e.startswith("consume:")]


def test_conflict_reports_the_parked_intent_id(monkeypatch):
    """§2 — the deferred answer names the intent, so the caller can follow it."""
    events = []
    outcome = {"ok": True, "terminal": False, "deferred": True,
               "result": {"status": "conflict", "merge_id": 11, "approval_intent_id": "x"}}
    _install_orchestration_fakes(monkeypatch, outcome, lambda: pytest.fail("no approval"), events)

    response = asyncio.run(workflow.document_review_transition_rpc(
        "approve", workflow.DocumentBodyRequest(doc_id=DOC["doc_id"], git_action="merge"), USER, None
    ))
    approval = _payload(response)["approval"]
    assert approval["deferred"] is True
    assert approval["merge_id"] == 11
    assert approval["approval_intent_id"]


def test_lock_busy_stops_before_git_and_approval(monkeypatch):
    events = []
    _install_orchestration_fakes(monkeypatch, {}, lambda: pytest.fail("approval must not run"), events)
    monkeypatch.setattr(workflow.git_service, "_acquire_lock", lambda *a, **kw: events.append("lock") or False)

    response = asyncio.run(workflow.document_review_transition_rpc(
        "approve", workflow.DocumentBodyRequest(doc_id=DOC["doc_id"], git_action="merge"), USER, None
    ))
    body = _payload(response)
    assert response.status_code == 409
    assert body["error"]["code"] == "git_busy"
    assert body["approval"]["stage"] == "lock"
    assert events == ["precheck", "lock"]