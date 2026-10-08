"""Regression tests for flowgate.default.0609 T0004.

Git-active AC final approval used to treat "git-active" (git config enabled and
a worktree still registered) as reason enough to demand `git_action`, even when
the group provably has nothing left to merge or push. That blocked approval for
no-work / already-applied groups behind

    git_action is required for final approval of a git-active group

This module locks the fix: the legacy path-shaped approval endpoint
(document_review_transition_endpoint) now defers to
`git_service.group_finalize_is_noop` -- the same real-Git-state judgment the
finalize panel/gate already use (flowgate.default.0548 T0004) -- and only
requires `git_action` when there is an actual pending merge/discard choice.
Cases follow flowgate.default.0609 T0004 SS4 / NR0003 SS7 (A/B/C/F).
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from group_lock_stub import group_store  # noqa: F401
from modules.flow_gate.workflow import pipeline_service as ps
from modules.flow_gate.workflow.routers import workflow


def _ac_and_root():
    ac = {
        "id": 80,
        "doc_id": "flowgate.default.0609.0008-AC",
        "project_id": "flowgate",
        "group_id": "flowgate.default.0609",
        "branch": "main",
        "type_code": "AC",
        "target_id": "flowgate.default.0609.0001-B",
        "seq": 8,
        "file_path": None,
        "doc_review_status": "pending_review",
    }
    root = {
        "id": 1,
        "doc_id": ac["target_id"],
        "project_id": ac["project_id"],
        "group_id": ac["group_id"],
        "type_code": "B",
        "seq": 1,
        "doc_review_status": "approved",
    }
    return ac, root


def _install(monkeypatch, ac, root, *, cfg, state, is_noop):
    docs = MagicMock()
    docs.get_by_id.side_effect = lambda doc_id: ac if doc_id == ac["doc_id"] else root
    docs.list_documents.return_value = [root, ac]

    def update(doc_id, fields):
        current = ac if doc_id == ac["doc_id"] else root
        current.update(fields)
        return dict(current)

    docs.update.side_effect = update
    monkeypatch.setattr(ps, "db_docs", docs)
    monkeypatch.setattr(workflow, "db_docs", docs)
    monkeypatch.setattr(ps.db_wfseq, "get_sequence_by_doc_id", lambda _root_id: {"id": 10})
    monkeypatch.setattr(ps.db_wfseq, "get_effective_head", lambda _sequence_id: None)
    # The workflow-head guard (0660 T0004 §5) fails closed without a sequence store, which
    # this DB-less fixture does not have; it is covered by test_tr2_lifecycle_0660.py.
    monkeypatch.setattr(ps, "_require_workflow_head_for_approval", lambda doc, locale="ko": None)
    monkeypatch.setattr(ps, "log_state_changed", MagicMock())
    monkeypatch.setattr(workflow.process_service, "is_group_disposed", lambda _group_id: False)
    monkeypatch.setattr(workflow.git_service, "realize_wf_done_transition", MagicMock())
    monkeypatch.setattr(workflow.git_service.db_git, "get_config", lambda _project_id: cfg)
    monkeypatch.setattr(workflow.git_service.db_git, "get_state", lambda _group_id: state)
    monkeypatch.setattr(
        workflow.git_service, "group_finalize_is_noop", MagicMock(return_value=is_noop)
    )
    resolver = MagicMock(side_effect=AssertionError("file-less AC must not read a file"))
    monkeypatch.setattr(ps.storage_paths, "resolve_storage_path", resolver)
    return docs, resolver


def _approve(ac):
    return asyncio.run(
        workflow.document_review_transition_endpoint(
            ac["doc_id"],
            "approve",
            workflow.DocumentTransitionRequest(),
            {"user_id": "reviewer", "is_admin": True},
        )
    )


_ACTIVE_CFG = {"enabled": 1, "base_branch": "main"}
_REGISTERED_STATE = {"worktree_registered": 1, "status": "awaiting_choice"}


def test_case_a_no_work_git_active_approves_without_git_action(monkeypatch):
    """Case A: git-active + no-work + no git_action -> final approval succeeds."""
    ac, root = _ac_and_root()
    docs, resolver = _install(
        monkeypatch, ac, root, cfg=_ACTIVE_CFG, state=_REGISTERED_STATE, is_noop=True,
    )

    result = _approve(ac)

    assert result["document"]["doc_review_status"] == "approved"
    assert root["doc_review_status"] == "wf_done"
    resolver.assert_not_called()


def test_case_b_already_applied_worktree_unregistered_approves_without_git_action(
    monkeypatch,
):
    """Case B: git-active + already-applied (worktree already unregistered) + no
    git_action -> final approval succeeds, matching pre-existing behavior for
    unregistered slots, WITHOUT running any Git finalize/cleanup/rollback side
    effect (T0004 §3: "기존 반영 상태 유지" / "불필요한 cleanup/rollback 없음").

    The legacy path-shaped endpoint has no git_action field and, per the guard
    at workflow.py just above the group_finalize_is_noop() check, must never
    itself orchestrate Git -- only the RPC endpoint (with an explicit
    git_action) is allowed to call finalize/run_approve_git_action. This pins
    that the already-applied state is left exactly as found: no finalize, no
    slot cleanup, no git status rewrite.
    """
    ac, root = _ac_and_root()
    state = {"worktree_registered": 0, "status": "none"}
    docs, resolver = _install(
        monkeypatch, ac, root, cfg=_ACTIVE_CFG, state=state, is_noop=True,
    )
    finalize_spy = MagicMock(side_effect=AssertionError("legacy endpoint must not call finalize"))
    run_git_action_spy = MagicMock(
        side_effect=AssertionError("legacy endpoint must not run a git_action")
    )
    complete_git_action_spy = MagicMock(
        side_effect=AssertionError("legacy endpoint must not complete a git_action")
    )
    cleanup_slot_spy = MagicMock(side_effect=AssertionError("no slot cleanup for already-applied"))
    set_status_spy = MagicMock(side_effect=AssertionError("no git status rewrite for already-applied"))
    monkeypatch.setattr(workflow.git_service, "finalize", finalize_spy)
    monkeypatch.setattr(workflow.git_service, "run_approve_git_action", run_git_action_spy)
    monkeypatch.setattr(workflow.git_service, "complete_approve_git_action", complete_git_action_spy)
    monkeypatch.setattr(workflow.git_service, "_cleanup_group_slot", cleanup_slot_spy)
    monkeypatch.setattr(workflow.git_service.db_git, "set_status", set_status_spy)

    result = _approve(ac)

    assert result["document"]["doc_review_status"] == "approved"
    resolver.assert_not_called()
    finalize_spy.assert_not_called()
    run_git_action_spy.assert_not_called()
    complete_git_action_spy.assert_not_called()
    cleanup_slot_spy.assert_not_called()
    set_status_spy.assert_not_called()
    # The already-applied Git state itself is untouched by approval.
    assert state == {"worktree_registered": 0, "status": "none"}


def test_case_c_merge_required_still_blocks_without_git_action(monkeypatch):
    """Case C: git-active + real merge/discard pending + no git_action -> the
    existing validation error is unchanged."""
    ac, root = _ac_and_root()
    docs, resolver = _install(
        monkeypatch, ac, root, cfg=_ACTIVE_CFG, state=_REGISTERED_STATE, is_noop=False,
    )

    with pytest.raises(Exception) as excinfo:
        _approve(ac)

    exc = excinfo.value
    assert getattr(exc, "status_code", None) == 422
    assert "git_action is required" in str(getattr(exc, "detail", exc))
    assert ac["doc_review_status"] == "pending_review"
    docs.update.assert_not_called()


def test_case_f_non_git_active_group_unaffected(monkeypatch):
    """Case F: non-git-active group -> approval behavior is unchanged and the
    no-work judgment is never consulted (there is nothing Git to judge)."""
    ac, root = _ac_and_root()
    docs, resolver = _install(
        monkeypatch, ac, root, cfg=None, state=None, is_noop=False,
    )
    noop_probe = MagicMock(return_value=False)
    monkeypatch.setattr(workflow.git_service, "group_finalize_is_noop", noop_probe)

    result = _approve(ac)

    assert result["document"]["doc_review_status"] == "approved"
    noop_probe.assert_not_called()


# ── Case E: real discard-required + git_action=discard (normal path) ─────────
#
# Case A/B/C/F above exercise the legacy path-shaped endpoint, which has no
# git_action field at all -- by construction it can never drive a discard.
# The actual discard finalize is owned by the RPC orchestrator
# (document_review_transition_rpc / _orchestrate_final_approval in
# workflow.py), the same one flowgate.default.0555 T0006 pinned for
# git_action="merge". T0004 §4 Case E requires the discard twin of that
# contract to keep working untouched by this revision's no-work carve-out.

_DISCARD_DOC = {
    "doc_id": "flowgate.default.0609.0008-AC",
    "project_id": "flowgate",
    "group_id": "flowgate.default.0609",
    "target_id": "flowgate.default.0609.0001-B",
    "type_code": "AC",
    "doc_review_status": "pending_review",
    "revision_no": 1,
}
_DISCARD_USER = {"user_id": "reviewer", "is_admin": True}


def test_case_e_discard_required_git_action_discard_finalizes_normally(group_store, monkeypatch):
    """Case E: git-active + real discard required + git_action=discard -> normal
    finalize succeeds exactly as before this revision (T0004 §3/§4).

    0669 units 6a/6b: the approval is a final_approval_publish job on the real job layer
    (approval_job_fakes); the Git body answers "discarded" and is only scripted."""
    import approval_job_fakes as fakes

    fx = fakes.install(monkeypatch, group_store, project_id="flowgate",
                       group_id=_DISCARD_DOC["group_id"], doc=_DISCARD_DOC)
    fx.script["outcome"] = {
        "ok": True, "terminal": True,
        "result": {"status": "discarded", "reason": "already_applied"},
    }
    seen = {}
    monkeypatch.setattr(workflow.git_service, "precheck_approve_git_action",
                        lambda doc, action: seen.update(precheck=action) or _DISCARD_DOC["group_id"])

    # "discard" is not a ride-along action (APPROVAL_FINALIZE_ACTIONS): the real precheck and
    # ``approval_publish.start`` refuse it with 422. A group with nothing left to merge is
    # discarded by the finalize body itself when the approver chose "merge".
    status, body = fakes.approve(_DISCARD_DOC["doc_id"], _DISCARD_USER, git_action="merge")

    assert status == 200, body
    assert body["approval"]["approved"] is True
    assert body["git"]["result"]["status"] == "discarded"
    # The approver's choice must reach Git and the terminal notifier unchanged -- the
    # no-work carve-out must not swallow the discard outcome silently.
    assert seen["precheck"] == "merge"
    assert fx.git_actions == ["merge"]
    assert fx.completed == [("merge", True)]
    assert fx.events == ["precheck", "freeze", "git", "approval", "complete", "realize"]
    assert fakes.job_of(_DISCARD_DOC["group_id"])["status"] == "succeeded"
