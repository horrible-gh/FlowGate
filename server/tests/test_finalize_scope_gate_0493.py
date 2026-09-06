from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from modules.flow_gate.services import tr_scope_service
from modules.flow_gate.workflow.routers import workflow


PROJECT = "flowgate"
GROUP = "flowgate.default.0493"
ROOT_ID = f"{GROUP}.0001-B"


def _root(status: str = "wf_in_progress") -> dict:
    return {
        "doc_id": ROOT_ID,
        "project_id": PROJECT,
        "group_id": GROUP,
        "type_code": "B",
        "doc_review_status": status,
        "seq": 1,
    }


def _install_finalize(monkeypatch, *, status: str = "wf_in_progress"):
    root = _root(status)
    updates = []
    realized = MagicMock()
    monkeypatch.setattr(workflow, "_get_user_permissions", lambda _user: {"document.approve"})
    monkeypatch.setattr(workflow, "_guard_group_not_disposed", lambda *_args: None)
    monkeypatch.setattr(workflow, "_guard_group_not_ai_running", lambda *_args: None)
    monkeypatch.setattr(workflow.db_docs, "get_by_id", lambda _doc_id: root)
    monkeypatch.setattr(workflow.db_docs, "list_documents", lambda **_kwargs: [root])

    def update(_doc_id, values):
        updates.append(dict(values))
        root.update(values)
        return dict(root)

    monkeypatch.setattr(workflow.db_docs, "update", update)
    monkeypatch.setattr(workflow.git_service, "realize_wf_done_transition", realized)
    return root, updates, realized


def test_group_recheck_uses_live_changes_and_all_declared_paths(monkeypatch):
    calls = []
    snapshots = [
        {
            "available": True,
            "paths": ["server/reported.py", "server/leftover.py", "server/.test-tmp-x/junk"],
            "entries": [
                {"path": "server/reported.py", "status": "M", "old_path": None},
                {"path": "server/leftover.py", "status": "A", "old_path": None},
                {"path": "server/.test-tmp-x/junk", "status": "A", "old_path": None},
            ],
            "branch": "work",
            "worktree": "group-tree",
        },
        {
            "available": True,
            "paths": ["server/reported.py"],
            "entries": [{"path": "server/reported.py", "status": "M", "old_path": None}],
        },
    ]
    monkeypatch.setattr(tr_scope_service, "resolve_stage", lambda _project: "observe")
    monkeypatch.setattr(
        tr_scope_service.git_service,
        "collect_scope_changes",
        lambda project, group: calls.append((project, group)) or snapshots.pop(0),
    )
    monkeypatch.setattr(
        tr_scope_service,
        "group_declared_paths",
        lambda _group: ["server/reported.py"],
    )

    first = tr_scope_service.evaluate_group_unreported(PROJECT, GROUP)
    second = tr_scope_service.evaluate_group_unreported(PROJECT, GROUP)

    assert calls == [(PROJECT, GROUP), (PROJECT, GROUP)]
    assert first["unreported"] == [
        {"path": "server/leftover.py", "actual_status": "A", "old_path": None}
    ]
    assert second["unreported"] == []


@pytest.mark.parametrize(
    ("stage", "actual", "reason"),
    [
        (None, None, "git_integration_off"),
        ("warn", {"available": False, "reason": "no_group_worktree"}, "no_group_worktree"),
    ],
)
def test_group_recheck_skips_git_off_or_unavailable(monkeypatch, stage, actual, reason):
    collect = MagicMock(return_value=actual)
    monkeypatch.setattr(tr_scope_service, "resolve_stage", lambda _project: stage)
    monkeypatch.setattr(tr_scope_service.git_service, "collect_scope_changes", collect)

    result = tr_scope_service.evaluate_group_unreported(PROJECT, GROUP)

    assert result == {"checked": False, "reason": reason, "unreported": []}
    if stage is None:
        collect.assert_not_called()
    else:
        collect.assert_called_once_with(PROJECT, GROUP)


def test_finalize_blocks_structured_unreported_without_partial_transition(monkeypatch):
    root, updates, realized = _install_finalize(monkeypatch)
    monkeypatch.setattr(
        workflow.tr_scope_service,
        "evaluate_group_unreported",
        lambda *_args: {
            "checked": True,
            "unreported": [
                {"path": "server/leftover.py", "actual_status": "A", "old_path": None},
                {"path": "server/deleted.py", "actual_status": "D", "old_path": None},
            ],
        },
    )

    with pytest.raises(HTTPException) as caught:
        workflow.finalize_workflow_endpoint(
            workflow.DocumentBodyRequest(doc_id=ROOT_ID),
            {"user_id": "reviewer"},
        )

    assert caught.value.status_code == 409
    detail = caught.value.detail
    assert detail["code"] == "unresolved_unreported_changes"
    assert detail["unresolved_count"] == 2
    assert detail["unresolved"][0] == {
        "path": "server/leftover.py", "actual_status": "A", "old_path": None
    }
    assert detail["truncated"] is False
    assert "new TR/TS" in detail["message"]
    assert "remove or revert" in detail["message"]
    assert root["doc_review_status"] == "wf_in_progress"
    assert updates == []
    realized.assert_not_called()


def test_finalize_rework_then_retry_succeeds(monkeypatch):
    root, updates, realized = _install_finalize(monkeypatch)
    results = iter([
        {"checked": True, "unreported": [{"path": "server/new.py", "actual_status": "A"}]},
        {"checked": True, "unreported": []},
    ])
    monkeypatch.setattr(
        workflow.tr_scope_service,
        "evaluate_group_unreported",
        lambda *_args: next(results),
    )

    with pytest.raises(HTTPException):
        workflow.finalize_workflow_endpoint(
            workflow.DocumentBodyRequest(doc_id=ROOT_ID), {"user_id": "reviewer"}
        )
    result = workflow.finalize_workflow_endpoint(
        workflow.DocumentBodyRequest(doc_id=ROOT_ID), {"user_id": "reviewer"}
    )

    assert result["document"]["doc_review_status"] == "wf_done"
    assert updates == [{"doc_review_status": "wf_done"}]
    realized.assert_called_once_with(GROUP)


def test_finalize_normal_or_git_off_result_is_unchanged(monkeypatch):
    _root_doc, updates, realized = _install_finalize(monkeypatch)
    monkeypatch.setattr(
        workflow.tr_scope_service,
        "evaluate_group_unreported",
        lambda *_args: {"checked": False, "reason": "git_integration_off", "unreported": []},
    )

    result = workflow.finalize_workflow_endpoint(
        workflow.DocumentBodyRequest(doc_id=ROOT_ID), {"user_id": "reviewer"}
    )

    assert result["document"]["doc_review_status"] == "wf_done"
    assert updates == [{"doc_review_status": "wf_done"}]
    realized.assert_called_once_with(GROUP)


def test_finalize_idempotent_path_does_not_recheck(monkeypatch):
    root, updates, realized = _install_finalize(monkeypatch, status="wf_done")
    recheck = MagicMock(side_effect=AssertionError("idempotent finalize must not recheck"))
    monkeypatch.setattr(workflow.tr_scope_service, "evaluate_group_unreported", recheck)

    result = workflow.finalize_workflow_endpoint(
        workflow.DocumentBodyRequest(doc_id=ROOT_ID), {"user_id": "reviewer"}
    )

    assert result == {"document": root}
    recheck.assert_not_called()
    assert updates == []
    realized.assert_not_called()
