"""Final-approval/Git orchestration contract for flowgate.default.0555 T0006, on the real job
layer of flowgate.default.0669 (units 6a/6b).

The approval used to take the project mutex with ``wait_sec=0`` and answer ``git_busy`` 409 when
it was busy. It is now a ``final_approval_publish`` job: ``precheck -> job -> freeze -> publish
(W/B + R) -> db_finalize``, and a busy lock leaves the job queued instead of dropping the intent.
These tests drive the real route function, the real ``approval_publish`` / ``job_store`` /
``lock_manager`` on SQLite and only script the Git body and the AC transition (see
``approval_job_fakes``). The real freeze and the real Git bodies run in ``test_git_real_bodies_0669``.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import approval_job_fakes as fakes  # noqa: E402
from group_lock_stub import group_store, held_locks, hold_lock  # noqa: E402,F401
from modules.flow_gate.services import git_service  # noqa: E402
from modules.flow_gate.services.git import approval_freeze  # noqa: E402
from modules.flow_gate.services.git import approval_publish as ap  # noqa: E402
from modules.flow_gate.services.git import job_runner  # noqa: E402

PROJECT = "demo"
DOC = {
    "doc_id": "demo.default.0555.0001-AC",
    "project_id": PROJECT,
    "group_id": "demo.default.0555",
    "target_id": "demo.default.0555.0001-R",
    "type_code": "AC",
    "doc_review_status": "pending_review",
    "revision_no": 1,
}
USER = {"user_id": "reviewer", "is_admin": True}


@pytest.fixture
def fx(group_store, monkeypatch):
    return fakes.install(monkeypatch, group_store, project_id=PROJECT, group_id=DOC["group_id"], doc=DOC)


def _merged(extra=None):
    return {"ok": True, "terminal": True,
            "result": {"status": "merged", "merge_commit": "abc", **(extra or {})}}


def test_wait_and_commit_only_are_rejected_only_for_final_approval(monkeypatch):
    monkeypatch.setattr(git_service.db_git, "get_config", lambda project_id: {"enabled": 1})
    monkeypatch.setattr(git_service.db_git, "get_state", lambda group_id: {"worktree_registered": 1})
    for action in ("wait", "commit_only"):
        with pytest.raises(git_service.GitServiceError) as exc:
            git_service.precheck_approve_git_action(DOC, action)
        assert (exc.value.status, exc.value.code) == (422, "invalid_request")
    assert "wait" in git_service.ACTION_VALUES
    assert "commit_only" in git_service.ACTION_VALUES


def test_clean_git_finishes_before_atomic_approval_under_b_and_r(fx):
    fx.script["outcome"] = _merged()

    status, body = fakes.approve(DOC["doc_id"], USER)

    assert status == 200, body
    assert body["approval"] == {
        "approved": True, "document_status": "approved", "root_status": "wf_done",
        "stage": "complete", "deferred": False,
    }
    # precheck, then freeze, then Git, and only then the approval transaction
    assert fx.events[:5] == ["precheck", "freeze", "git", "approval", "complete"]
    assert fx.events.count("git") == 1
    # the project mutex is gone: Git ran under exactly B (base target) and R, held by the job
    assert fx.held_at_git == [[("B", "publish"), ("R", "publish")]]
    job = fakes.job_of(DOC["group_id"])
    assert job["status"] == "succeeded" and job["phase"] == "done"
    assert held_locks(PROJECT) == []                    # released, nothing leaked
    assert job["job_id"] == body["job"]["job_id"]


def test_git_failure_never_commits_approval_and_releases_everything(fx):
    fx.script["outcome"] = {"ok": False, "terminal": False, "http_status": 409, "error": {
        "code": "base_dirty", "message": "dirty", "details": {"files": ["x.py"]}}}

    status, body = fakes.approve(DOC["doc_id"], USER)

    assert status == 409
    assert body["error"]["code"] == "base_dirty"
    assert body["approval"]["approved"] is False
    assert body["approval"]["stage"] == "git_finalize"
    assert "approval" not in fx.events
    job = fakes.job_of(DOC["group_id"])
    assert job["status"] == "failed" and job["last_error_code"] == "base_dirty"
    assert held_locks(PROJECT) == []
    assert approval_freeze.freeze_claim_owner(DOC["group_id"]) is None    # the Group is open again


def test_conflict_is_deferred_without_approval(fx):
    fx.script["outcome"] = {"ok": True, "terminal": False, "deferred": True, "result": {
        "status": "conflict", "merge_id": 7, "conflict_files": ["x.py"]}}

    status, body = fakes.approve(DOC["doc_id"], USER)

    assert status == 200
    assert body["approval"]["deferred"] is True
    assert body["approval"]["document_status"] == "pending_review"
    assert "approval" not in fx.events
    assert fakes.job_of(DOC["group_id"])["status"] == "succeeded"       # handed off to the review
    assert held_locks(PROJECT) == []


def test_approval_commit_failure_keeps_the_job_queued_with_terminal_git(fx):
    fx.script["outcome"] = _merged({"terminal_retry": True})
    fx.script["commit"] = RuntimeError("root CAS failed")

    status, body = fakes.approve(DOC["doc_id"], USER)

    # 0669: Git is terminal and published; only the DB step is retried by the job
    assert status == 200, body
    assert body["approval"]["stage"] == "queued" and body["approval"]["deferred"] is True
    assert body["block_reason"]["code"] == "approval_commit_failed"
    assert body["git"]["terminal"] is True
    assert body["git"]["result"]["terminal_retry"] is True
    job = fakes.job_of(DOC["group_id"])
    assert job["status"] == "retry_wait" and job["phase"] == "db_finalize"
    assert fx.events.index("complete") > fx.events.index("approval")
    assert held_locks(PROJECT) == []


def test_terminal_retry_consumes_the_intent_its_own_conflict_parked(fx, monkeypatch):
    """0555 T0008 §9 -- the D0005 §3.9 re-approval finishes the parked intent too.

    Approving without consuming would leave a live intent on the closed session, and the next
    merge review to read it would approve the same document twice."""
    fx.script["outcome"] = _merged({"terminal_retry": True})

    def clean_retry(group_id):
        if group_id != DOC["group_id"]:
            return (None, None)
        job = fakes.job_of(group_id)
        intent_id = json.loads(job["payload"])["approval_intent_id"]
        return ({}, {"intent": {"approval_intent_id": intent_id, "ac_doc_id": DOC["doc_id"]},
                     "terminal_status": "merged", "merge_commit": "abc"})

    monkeypatch.setattr(ap.approval_intent, "find_clean_retry", clean_retry)

    status, body = fakes.approve(DOC["doc_id"], USER)

    assert status == 200, body
    assert body["approval"]["approved"] is True
    assert fx.consumed and fx.consumed[0][0] == DOC["group_id"]
    assert fakes.job_of(DOC["group_id"])["status"] == "succeeded"


def test_terminal_without_a_parked_intent_consumes_nothing(fx):
    """The ordinary (never-conflicted) approval keeps its T#1 shape exactly."""
    fx.script["outcome"] = _merged()

    status, _body = fakes.approve(DOC["doc_id"], USER)

    assert status == 200
    assert fx.consumed == []


def test_conflict_reports_the_parked_intent_id(fx):
    """§2 -- the deferred answer names the intent, so the caller can follow it."""
    fx.script["outcome"] = {"ok": True, "terminal": False, "deferred": True, "result": {
        "status": "conflict", "merge_id": 11, "approval_intent_id": "x"}}

    _status, body = fakes.approve(DOC["doc_id"], USER)

    approval = body["approval"]
    assert approval["deferred"] is True
    assert approval["merge_id"] == 11
    job = fakes.job_of(DOC["group_id"])
    assert approval["approval_intent_id"] == json.loads(job["payload"])["approval_intent_id"]


def test_busy_lock_queues_the_approval_instead_of_dropping_it(fx):
    """0669 B: a busy R used to be a 409 git_busy that lost the approval. Now the job waits."""
    with hold_lock("R", PROJECT, holder_kind="publish"):
        status, body = fakes.approve(DOC["doc_id"], USER)

        assert status == 200, body
        assert body["approval"]["stage"] == "queued" and body["approval"]["deferred"] is True
        assert body["approval"]["blocker"]["domain"] == "R"
        assert "git" not in fx.events and "approval" not in fx.events
        job = fakes.job_of(DOC["group_id"])
        assert job["status"] == "blocked" and job["blocked_domain"] == "R"

    # the lock is free again: the Runner's claim finishes the very same job
    fx.script["outcome"] = _merged()
    claim = job_runner.try_claim(job["job_id"])
    assert claim is not None
    job_runner.execute(claim)
    done = fakes.job_of(DOC["group_id"])
    assert done["job_id"] == job["job_id"] and done["status"] == "succeeded"
    assert fx.events.count("git") == 1 and fx.commits == [DOC["doc_id"]]
    assert held_locks(PROJECT) == []
