"""T#3 (0134): job_view carries retry/recovery detail for the screen without stderr."""
from __future__ import annotations

import json

from test_git_integration_0115 import _git, needs_git  # noqa: F401 -- sets FLOWGATE_STORAGE_DIR etc.


def _ap():
    from modules.flow_gate.services.git import approval_publish as ap
    return ap


def _job(status, **over):
    job = {"job_id": "opj_r1", "kind": "final_approval_publish", "status": status,
           "phase": "fetched", "last_error_code": "target_diverged", "attempt_count": 3,
           "available_at": "2026-11-05T04:00:00+00:00",
           "payload": json.dumps({"doc_id": "p.default.0001.0002-AC"}),
           "evidence": json.dumps({"attempt_error": {
               "code": "target_diverged", "stage": "merge", "cause": "conflict_other",
               "diagnostic": "fatal: SECRET stderr text"}})}
    job.update(over)
    return job


def test_retry_wait_view_has_code_stage_cause_attempts_and_next_time():
    from modules.flow_gate.services.git import job_store as store
    view = _ap().job_view(_job("retry_wait"))
    assert view["last_error_code"] == "target_diverged"
    assert view["error_stage"] == "merge" and view["error_cause"] == "conflict_other"
    assert view["attempt_count"] == 3 and view["max_attempts"] == store.retry_max_attempts()
    assert view["available_at"] == "2026-11-05T04:00:00+00:00"
    assert "cancelable" in view
    assert "SECRET" not in json.dumps(view)


def test_recovery_required_view_carries_the_same_detail():
    view = _ap().job_view(_job("recovery_required"))
    assert view["error_stage"] == "merge" and view["max_attempts"]


def test_other_statuses_add_no_retry_detail_and_missing_evidence_is_safe():
    view = _ap().job_view(_job("running"))
    assert "error_stage" not in view and "max_attempts" not in view
    view = _ap().job_view(_job("retry_wait", evidence=None))
    assert view["error_stage"] is None and view["error_cause"] is None


def test_db_finalize_error_after_git_error_does_not_borrow_the_old_stage_cause():
    # fetch/network Git error was recorded, Git then succeeded and the DB record failed:
    # last_error_code moved on, the evidence still holds the older Git attempt.
    ev = json.dumps({"attempt_error": {
        "code": "git_error", "stage": "fetch", "cause": "network",
        "diagnostic": "fatal: SECRET stderr text"}})
    view = _ap().job_view(_job("retry_wait", last_error_code="approval_commit_failed", evidence=ev))
    assert view["last_error_code"] == "approval_commit_failed"
    assert view["error_stage"] is None and view["error_cause"] is None
    assert view["max_attempts"] and view["available_at"]
    assert "SECRET" not in json.dumps(view)


def test_recovery_after_publish_error_does_not_borrow_the_old_stage_cause():
    ev = json.dumps({"attempt_error": {
        "code": "git_error", "stage": "fetch", "cause": "network",
        "diagnostic": "fatal: SECRET stderr text"}})
    view = _ap().job_view(_job("recovery_required", last_error_code="approval_attempt_unsettled",
                               evidence=ev))
    assert view["last_error_code"] == "approval_attempt_unsettled"
    assert view["error_stage"] is None and view["error_cause"] is None
    assert view["available_at"] == "2026-11-05T04:00:00+00:00"
    assert "SECRET" not in json.dumps(view)


def test_matching_error_code_keeps_its_own_stage_cause():
    ev = json.dumps({"attempt_error": {"code": "git_error", "stage": "fetch", "cause": "network"}})
    view = _ap().job_view(_job("retry_wait", last_error_code="git_error", evidence=ev))
    assert view["error_stage"] == "fetch" and view["error_cause"] == "network"
