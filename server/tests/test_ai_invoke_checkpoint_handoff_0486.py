from __future__ import annotations

import logging
import os
import sys
from contextlib import nullcontext
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from modules.flow_gate.services import ai_invoke_service as svc
from modules.flow_gate.services.ai_invoke import chain as chain_service


def _loop_world(monkeypatch, *, changed, latest):
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops
    from modules.flow_gate.db import document_reviews as db_reviews

    persisted = {
        "run_id": "aiv_cas_0486",
        "group_id": "flowgate.default.0486",
        "doc_ref": "flowgate.default.0486.0023-T",
        "review_count": -1,
        "review_baseline_id": 10,
        "baseline_revision_no": 3,
        "starts_with_rework": False,
        "round_no": 1,
        "current_stage": "review",
        "attempts_used": 0,
        "failure_restart_max_attempts": 0,
        "updated_at": "2026-09-10T00:00:00+00:00",
        "deadline_at": None,
    }
    doc = {"revision_no": 3, "doc_review_status": "pending_review"}
    rows = [{
        "id": 11,
        "verdict": "pass",
        "revision_no": 3,
        "review_run_id": persisted["run_id"],
        "attempt_no": 1,
        "findings": [],
    }]
    monkeypatch.setattr(db_loops, "get", lambda _run_id: dict(persisted))
    monkeypatch.setattr(db_loops, "checkpoint", lambda *_a, **_kw: (changed, latest))
    monkeypatch.setattr(svc, "get_store",
                        lambda: type("Store", (), {"transaction": lambda self: nullcontext(self)})())
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda _doc_id: dict(doc))
    monkeypatch.setattr(db_reviews, "list_by_doc", lambda _doc_id: list(rows))
    run = {
        "run_id": persisted["run_id"],
        "attempt_no": 1,
        "outcome": "complete",
        "issued_to": "owner",
        "document_review_loop": dict(persisted),
    }
    return run, persisted


def test_checkpoint_cas_hit_publishes_latest_and_logs_expected_state(monkeypatch, caplog):
    latest = {
        "run_id": "aiv_cas_0486", "round_no": 1, "current_stage": "stopped",
        "stop_reason": "review_passed", "updated_at": "2026-09-10T00:00:01+00:00",
    }
    run, persisted = _loop_world(monkeypatch, changed=True, latest=latest)

    with caplog.at_level(logging.INFO):
        result = svc._checkpoint_document_review_loop(run)

    assert result is latest
    assert run["document_review_loop"] is latest
    record = next(r for r in caplog.records if r.getMessage().endswith("CAS hit"))
    assert (record.run_id, record.expected_round_no, record.expected_stage) == (
        run["run_id"], 1, "review",
    )
    assert record.expected_updated_at == persisted["updated_at"]
    assert (record.latest_round_no, record.latest_stage) == (1, "stopped")


def test_checkpoint_cas_miss_uses_competing_row_not_stale_resolved(monkeypatch, caplog):
    competing = {
        "run_id": "aiv_cas_0486", "round_no": 2, "current_stage": "rework",
        "stop_reason": None, "updated_at": "2026-09-10T00:00:02+00:00",
    }
    run, persisted = _loop_world(monkeypatch, changed=False, latest=competing)

    with caplog.at_level(logging.WARNING):
        result = svc._checkpoint_document_review_loop(run)

    assert result is competing
    assert run["document_review_loop"] is competing
    assert run["document_review_loop"]["current_stage"] == "rework"
    record = next(r for r in caplog.records if r.getMessage().endswith("CAS miss"))
    assert record.run_id == run["run_id"]
    assert record.expected_updated_at == persisted["updated_at"]
    assert (record.latest_round_no, record.latest_stage, record.latest_updated_at) == (
        2, "rework", competing["updated_at"],
    )


def _run(*, end_reason="exited"):
    return {
        "run_id": "aiv_handoff_0486",
        "group_id": "flowgate.default.0486",
        "doc_ref": "flowgate.default.0486.0023-T",
        "issued_to": "owner",
        "end_reason": end_reason,
        "stop_code": "hop_handoff",
        "continuation_locale": "ko",
    }


def _pending(marker="old"):
    return {
        "doc_ref": "flowgate.default.0486.0023-T",
        "target_seq": 4,
        "issued_to": "owner",
        "marker": marker,
    }


@pytest.fixture(autouse=True)
def clean_queue():
    with svc._auto_resume_lock:
        svc._auto_resume.clear()
    yield
    with svc._auto_resume_lock:
        svc._auto_resume.clear()


@pytest.mark.parametrize("end_reason", ["exited", "provider_failed"])
def test_handoff_write_precedes_identity_pop_for_complete_and_abnormal(
    monkeypatch, end_reason,
):
    run = _run(end_reason=end_reason)
    queued = _pending()
    order = []
    with svc._auto_resume_lock:
        svc._auto_resume[run["group_id"]] = queued

    monkeypatch.setattr(svc, "_write_handoff_row",
                        lambda *_a, **_kw: order.append("write"))
    real_compare_pop = svc._pop_auto_resume_if_same

    def observed_pop(group_id, expected):
        order.append("pop")
        return real_compare_pop(group_id, expected)

    monkeypatch.setattr(svc, "_pop_auto_resume_if_same", observed_pop)
    monkeypatch.setattr(svc, "_park_handoff",
                        lambda *_a, **_kw: order.append("park"))
    monkeypatch.setattr(chain_service.review, "run_review_gate",
                        lambda *_a, **_kw: order.append("gate") or True)
    monkeypatch.setattr(svc, "_clear_handoff_row",
                        lambda *_a, **_kw: order.append("clear"))

    svc._maybe_auto_resume_hop(run)

    assert order[:2] == ["write", "pop"]
    if end_reason == "exited":
        assert order == ["write", "pop", "gate", "clear"]
    else:
        assert order == ["write", "pop", "park"]


def test_write_failure_before_pop_leaves_pending_for_retry(monkeypatch):
    run = _run()
    queued = _pending()
    with svc._auto_resume_lock:
        svc._auto_resume[run["group_id"]] = queued

    monkeypatch.setattr(
        svc, "_write_handoff_row",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("crash after DB boundary")),
    )

    with pytest.raises(RuntimeError, match="crash after DB boundary"):
        svc._maybe_auto_resume_hop(run)

    assert svc.peek_auto_resume(run["group_id"]) is queued


def test_compare_remove_does_not_consume_newly_enqueued_pending(monkeypatch):
    run = _run()
    queued = _pending("old")
    replacement = _pending("new")
    replacement["target_seq"] = 5
    with svc._auto_resume_lock:
        svc._auto_resume[run["group_id"]] = queued

    def write_then_replace(*_a, **_kw):
        with svc._auto_resume_lock:
            svc._auto_resume[run["group_id"]] = replacement

    monkeypatch.setattr(svc, "_write_handoff_row", write_then_replace)
    monkeypatch.setattr(
        chain_service.review, "run_review_gate",
        lambda *_a, **_kw: pytest.fail("stale pending must not reach the gate"),
    )

    svc._maybe_auto_resume_hop(run)

    assert svc.peek_auto_resume(run["group_id"]) is replacement


def test_gate_exception_occurs_after_write_and_pop_then_parks(monkeypatch):
    run = _run()
    order = []
    with svc._auto_resume_lock:
        svc._auto_resume[run["group_id"]] = _pending()

    monkeypatch.setattr(svc, "_write_handoff_row",
                        lambda *_a, **_kw: order.append("write"))
    real_compare_pop = svc._pop_auto_resume_if_same
    monkeypatch.setattr(
        svc, "_pop_auto_resume_if_same",
        lambda group, expected: order.append("pop") or real_compare_pop(group, expected),
    )
    monkeypatch.setattr(
        chain_service.review, "run_review_gate",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("gate boom")),
    )
    monkeypatch.setattr(svc, "_park_handoff",
                        lambda *_a, **_kw: order.append("park"))

    svc._maybe_auto_resume_hop(run)

    assert order == ["write", "pop", "park"]
