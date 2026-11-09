"""flowgate.v02test.0014: back-to-back reviews of one revision and a loop left "active".

Reported on flowgate.v02test.0012: a TR with r1 and `doc_review_status` NULL got two
`issues` reviews and no rejection, and its card stayed running/locked. Four guards:

1. a rejection that cannot be written stops the loop -- it no longer reserves the review
   hop again (resolve_document_review_loop_gate / _checkpoint_document_review_loop_tx);
2. the post-process recovery closes the abandoned run's loop row (worker recover ->
   review.force_stop_loop_after_abandon -> db stop_for_abandoned_run; covered in
   services/test_ai_invoke_post_process_recovery_0541.py and the CAS in
   test_ai_invoke_restart_orphan_loop_0486.py);
3. a new loop never starts on a document with no review status (compute_review_baseline);
4. the instruction review path honours `submission_judged` like the other start paths.
"""
from __future__ import annotations

import os
import sys
from contextlib import nullcontext
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from modules.flow_gate.services import ai_invoke_service as service  # noqa: E402
from modules.flow_gate.services.ai_invoke import review  # noqa: E402

BASE = {
    "review_count": 2,
    "reviewer_provider_id": "reviewer",
    "review_criteria": "document_type_default",
    "rework_provider_id": "reworker",
    "rework_timeout_sec": 1800,
    "rework_message": "fix every finding",
    "failure_restart_max_attempts": 0,
    "total_timeout_sec": 3600,
    "review_baseline_id": 0,
    "baseline_revision_no": 1,
    "starts_with_rework": False,
    "round_no": 1,
    "current_stage": "review",
    "attempts_used": 0,
}
REFUSED = "Invalid transition: '' --reject-->"


# -- 1. a failed rejection stops the loop ----------------------------------------------

@pytest.mark.parametrize("maximum", [-1, 0, 3])
def test_failed_rejection_stops_instead_of_reserving_the_review_again(maximum):
    resolved = service.resolve_document_review_loop_gate({
        **BASE,
        "failure_restart_max_attempts": maximum,
        "attempts_used": 1,
        "reviews": [{"id": 1, "verdict": "issues", "revision_no": 1, "review_run_id": "r"}],
        "review_run_id": "r",
        "doc": {"revision_no": 1, "doc_review_status": ""},
        "last_hop_kind": "review",
        "last_hop_outcome": "failed",
        "transition_failed": True,
        "failure_detail": REFUSED,
    })
    assert resolved["current_stage"] == "stopped"
    assert resolved["stop_reason"] == "retry_exhausted"
    assert resolved["stop_detail"] == f"{review.AUTO_REJECT_FAILED_STOP_DETAIL}: {REFUSED}"


@pytest.mark.parametrize("maximum,used,expected", [(-1, 5, "review"), (1, 1, "review"), (0, 1, "stopped")])
def test_a_hop_that_itself_failed_keeps_its_retry_budget(maximum, used, expected):
    """Unchanged: only the rejection failure stops at once; a failed hop still retries."""
    resolved = service.resolve_document_review_loop_gate({
        **BASE,
        "failure_restart_max_attempts": maximum,
        "attempts_used": used,
        "reviews": [],
        "doc": {"revision_no": 1, "doc_review_status": "pending_review"},
        "last_hop_kind": "review",
        "last_hop_outcome": "failed",
    })
    assert resolved["current_stage"] == expected


def test_checkpoint_with_a_refused_rejection_writes_a_terminal_stop(monkeypatch):
    """The reported shape end to end through the checkpoint: r1, status NULL, an owned
    `issues` verdict, and a rejection the transition table refuses."""
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops
    from modules.flow_gate.db import document_reviews as db_reviews

    persisted = {
        **BASE,
        "failure_restart_max_attempts": -1,
        "run_id": "aiv_reject_refused",
        "group_id": "flowgate.v02test.0012",
        "doc_ref": "flowgate.v02test.0012.0006-TR",
        "updated_at": "v1",
        "deadline_at": "2099-01-01T00:00:00+00:00",
    }
    monkeypatch.setattr(db_loops, "get", lambda _run_id: dict(persisted))
    monkeypatch.setattr(
        service, "get_store",
        lambda: type("Store", (), {"transaction": lambda self: nullcontext(self)})(),
    )
    monkeypatch.setattr(service.db_docs, "get_by_id", lambda _doc_id: {
        "revision_no": 1, "doc_review_status": None, "rejection_history": "[]",
    })
    monkeypatch.setattr(db_reviews, "list_by_doc", lambda _doc_id: [
        {"id": 2609, "verdict": "issues", "revision_no": 1, "findings": '["x"]',
         "review_run_id": "aiv_reject_refused"},
    ])
    rejects = []
    monkeypatch.setattr(service, "_auto_reject", lambda slot, row, _bundle: (
        rejects.append((slot["review_status"], row["id"])) or {
            "ok": False, "stop_code": "review_reject_failed", "detail": REFUSED,
        }))
    captured = {}

    def checkpoint(_run_id, **kwargs):
        captured.update(kwargs)
        return True, {**persisted, **{k: v for k, v in kwargs.items() if not k.startswith("expected_")}}

    monkeypatch.setattr(db_loops, "checkpoint", checkpoint)

    latest = service._checkpoint_document_review_loop({
        "run_id": "aiv_reject_refused", "outcome": "complete",
        "document_review_loop": dict(persisted),
    })

    assert rejects == [("", 2609)]
    assert captured["current_stage"] == "stopped"
    assert captured["stop_reason"] == "retry_exhausted"
    assert captured["stop_detail"].startswith(review.AUTO_REJECT_FAILED_STOP_DETAIL)
    assert captured["last_hop_outcome"] == "failed"
    assert latest["current_stage"] == "stopped"


# -- 3. no loop on an unsubmitted document ---------------------------------------------

@pytest.mark.parametrize("status", [None, ""])
def test_a_loop_never_starts_on_a_document_with_no_review_status(monkeypatch, status):
    monkeypatch.setattr(service.db_docs, "get_by_id", lambda _doc_id: {
        "doc_review_status": status, "revision_no": 1,
    })
    monkeypatch.setattr(service.db_reviews, "list_by_doc", lambda _doc_id: [])
    with pytest.raises(HTTPException) as exc:
        service.compute_review_baseline("flowgate.v02test.0012.0006-TR")
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == review.DOCUMENT_NOT_SUBMITTED_CODE


@pytest.mark.parametrize("status,rework", [
    ("pending_review", False), ("revised", False), ("rejected", True), ("approved", False),
])
def test_submitted_documents_still_start_as_before(monkeypatch, status, rework):
    monkeypatch.setattr(service.db_docs, "get_by_id", lambda _doc_id: {
        "doc_review_status": status, "revision_no": 4,
    })
    monkeypatch.setattr(service.db_reviews, "list_by_doc", lambda _doc_id: [])
    assert service.compute_review_baseline("d") == {
        "review_baseline_id": 0, "starts_with_rework": rework, "baseline_revision_no": 4,
    }


# -- 4. the instruction review path honours submission_judged --------------------------

@pytest.fixture
def instruction(monkeypatch):
    from flowgate.card_settings import review_stage

    state = {"status": "pending_review", "judged": False, "started": [], "waits": []}
    monkeypatch.setattr(review_stage, "_assigned_attempt", lambda _attempt: False)
    monkeypatch.setattr(review_stage.repository, "values", lambda *_a: {
        "instruction_review_count": 2, "instruction_review_ai": "reviewer"})
    monkeypatch.setattr(review_stage.fields, "instruction_provider", lambda _stored: "reworker")
    monkeypatch.setattr(review_stage.fields, "review_loop", lambda *_a, **_k: {"review_criteria": "c"})
    monkeypatch.setattr(review_stage.completion_records, "document",
                        lambda _doc_id: {"doc_review_status": state["status"]})
    monkeypatch.setattr(review_stage.legacy, "attempt_owns", lambda *_a: True)
    monkeypatch.setattr(review_stage.legacy, "review_loop_active", lambda _doc_id: False)
    monkeypatch.setattr(review_stage.legacy, "submission_judged", lambda _doc_id: state["judged"])
    monkeypatch.setattr(review_stage.legacy, "project_of", lambda _flow_id: "flowgate")
    monkeypatch.setattr(review_stage.legacy, "start_review_loop",
                        lambda *a, **k: state["started"].append(a[2]) or {"run_id": "aiv_x"})
    monkeypatch.setattr(review_stage.legacy, "record_approval_wait",
                        lambda *a, **k: state["waits"].append(a))

    def call(doc_id="doc-1"):
        return review_stage.review_instruction(
            "flowgate.v02test.0012", {"node_key": "n1"}, doc_id, "actor", "run-1", "att-1")

    state["call"] = call
    return state


@pytest.mark.parametrize("status", [None, "", "pending_review", "revised"])
def test_instruction_review_does_not_review_an_answered_submission_again(instruction, status):
    instruction.update(status=status, judged=True)
    assert instruction["call"]() is True
    assert instruction["started"] == []


def test_instruction_review_still_starts_an_unanswered_submission(instruction):
    instruction.update(status="pending_review", judged=False)
    assert instruction["call"]() is True
    assert instruction["started"] == ["doc-1"]


def test_instruction_review_still_takes_a_rejected_document_to_its_rework(instruction):
    instruction.update(status="rejected", judged=True)
    assert instruction["call"]() is True
    assert instruction["started"] == ["doc-1"]
