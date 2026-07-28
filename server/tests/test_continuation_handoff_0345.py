"""Regression coverage for the continuous-hop monitor handoff (group 0345).

An engine-driven continuous chain deliberately finishes one AI invoke run before
starting the next run with a newly resolved provider. The inbox boundary must
therefore queue the replacement hop and publish an explicit lifecycle marker for
the run that is about to finish; otherwise the client briefly presents that
per-hop finish as completion of the whole chain.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from modules.flow_gate.api.v1.events.event_types import EventType


class _FakeRequest:
    headers = {"x-locale": "ko"}
    base_url = "http://flowgate.test/"


def _invoke_engine_handoff(monkeypatch, broadcaster):
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.api.v1.events import publisher
    from modules.flow_gate.db import ai_invoke_paused_chains
    from modules.flow_gate.db import workflow_sequences
    from modules.flow_gate.services import ai_invoke_service

    group_id = "flowgate.default.0345"
    run_status = {
        "run_id": "airun-hop-1",
        "group_id": group_id,
        "doc_ref": "flowgate.default.0345.0001-B",
        "mode": "continuous",
        "status": "running",
        "docs_target": 3,
        "docs_reached_so_far": 1,
        "provider": {"id": "provider-1", "name": "Provider One"},
        "attempt_no": 1,
        "started_at": "2026-07-28T12:00:00+00:00",
        "elapsed_ms": 2_500,
    }
    queue_resume = MagicMock()

    monkeypatch.setattr(
        workflow_sequences,
        "get_item_by_result_doc_id",
        lambda _doc_id: {"item_seq": 2, "type": "M"},
    )
    monkeypatch.setattr(ai_invoke_paused_chains, "get_by_group", lambda _group_id: None)
    monkeypatch.setattr(ai_invoke_service, "has_active_run", lambda value: value == group_id)
    monkeypatch.setattr(ai_invoke_service, "get_active_status", lambda _group_id: run_status)
    monkeypatch.setattr(ai_invoke_service, "request_auto_resume", queue_resume)
    monkeypatch.setattr(publisher, "broadcast_event_threadsafe", broadcaster)
    monkeypatch.setattr(
        inbox_routes,
        "_inbox_api_base",
        lambda _request: "http://flowgate.test/flowgate/api/v1",
    )

    envelope = inbox_routes._continuation_self_chain(
        request=_FakeRequest(),
        token_rec={
            "doc_ref": "flowgate.default.0345.0001-B",
            "issued_to": "usr-admin",
            "group_id": group_id,
            "continuation_target_seq": 4,
            "continuation_review_mode": 0,
            "continuation_locale": "ko",
        },
        project="flowgate",
        canonical_doc_id="flowgate.default.0345.0006-M",
        # M is auto-completed, keeping this test focused on the hop boundary rather
        # than the independent document-approval permission path.
        doc_type="M",
    )
    return envelope, queue_resume


def test_engine_handoff_queues_respawn_and_broadcasts_pending_marker(monkeypatch):
    published = []
    envelope, queue_resume = _invoke_engine_handoff(monkeypatch, published.append)

    assert envelope["continuation_pending"] is True
    assert envelope["continuation_respawn"] is True
    assert "next_token" not in envelope
    queue_resume.assert_called_once()
    assert queue_resume.call_args.args[0] == "flowgate.default.0345"
    assert queue_resume.call_args.args[1]["target_seq"] == 4

    assert len(published) == 1
    event = published[0]
    assert event.event_type == EventType.AI_INVOKE_STARTED
    assert event.project == "flowgate"
    assert event.group_id == "flowgate.default.0345"
    assert event.payload["run_id"] == "airun-hop-1"
    assert event.payload["continuation_pending"] is True
    assert event.payload["continuation_completed_doc_id"] == "flowgate.default.0345.0006-M"
    assert event.payload["continuation_completed_item_seq"] == 2
    assert event.payload["continuation_target_seq"] == 4


def test_handoff_signal_failure_does_not_cancel_queued_continuation(monkeypatch):
    def fail_broadcast(_event):
        raise RuntimeError("simulated SSE transport failure")

    envelope, queue_resume = _invoke_engine_handoff(monkeypatch, fail_broadcast)

    queue_resume.assert_called_once()
    assert envelope["continuation_pending"] is True
    assert envelope["continuation_respawn"] is True
