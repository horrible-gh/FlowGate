from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

os.environ.setdefault("TESTING", "1")


def test_request_workflow_decision_issues_dedicated_token(monkeypatch):
    from modules.flow_gate.services import workflow_decision_service as service

    doc = {
        "doc_id": "flowgate.default.0002.0001-R",
        "project_id": "flowgate",
        "group_id": "flowgate.default.0002",
        "module": "default",
        "type_code": "R",
        "seq": 1,
        "title": "Requirement",
    }
    monkeypatch.setattr(service.db_documents, "get_by_id", lambda _doc_id: doc)
    monkeypatch.setattr(service.db_documents, "get_group_max_seq", lambda _group_id: 1)
    monkeypatch.setattr(service.db_documents, "fetch_recent_group_docs", lambda **_kwargs: [])
    monkeypatch.setattr(service.db_wfseq, "get_sequence_by_doc_id", lambda _doc_id: None)
    issue = MagicMock(return_value={
        "raw_token": "worker-token",
        "token_id": "tok-1",
        "expires_at": "2026-06-12T00:00:00+00:00",
        "scratch_dir": "C:/scratch/tok-1",
    })
    monkeypatch.setattr(service.token_service, "issue", issue)

    result = service.request_workflow_decision(
        doc_id=doc["doc_id"],
        issued_to="user-1",
        api_base_url="http://localhost/flowgate/api/v1",
    )

    # 0086 "워크플로 결정부터": an ordinary (non-continuous) decision request passes the
    # continuation params as None/False — the token stays a plain workflow_decide token.
    issue.assert_called_once_with(
        project="flowgate",
        group_id="flowgate.default.0002",
        action_scope="workflow_decide",
        doc_ref=doc["doc_id"],
        issued_to="user-1",
        continuation_target_seq=None,
        continuation_review_mode=False,
    )
    assert result["action_scope"] == "workflow_decide"
    assert result["raw_token"] == "worker-token"
    assert "POST http://localhost/flowgate/api/v1/workflow/decide" in result["mention"]
    assert '"doc_id": "flowgate.default.0002.0001-R"' in result["mention"]


def test_workflow_decide_rejects_wrong_worker_scope(monkeypatch):
    from modules.flow_gate.api.v1 import workflow_decision_routes as routes

    monkeypatch.setattr(
        routes,
        "verify_bearer",
        lambda _request: {
            "token_id": "tok-1",
            "project": "flowgate",
            "issued_to": "user-1",
            "action_scope": "edit",
            "doc_ref": "flowgate.default.0002.0001-R",
        },
    )
    decide = MagicMock()
    monkeypatch.setattr(routes, "decide_workflow", decide)

    response = routes.post_workflow_decide(
        "flowgate.default.0002.0001-R",
        routes.DecideRequest(
            doc_class="R",
            sequence=[routes.SequenceItem(id=1, type="D", label="Design")],
        ),
        MagicMock(),
    )

    assert response.status_code == 403
    assert json.loads(response.body)["error"] == "workflow_decision_token_mismatch"
    decide.assert_not_called()


def test_workflow_decide_consumes_matching_worker_token(monkeypatch):
    from modules.flow_gate.api.v1 import workflow_decision_routes as routes
    from modules.flow_gate.services import token_service

    doc_id = "flowgate.default.0002.0001-R"
    monkeypatch.setattr(
        routes,
        "verify_bearer",
        lambda _request: {
            "token_id": "tok-1",
            "project": "flowgate",
            "issued_to": "user-1",
            "action_scope": "workflow_decide",
            "doc_ref": doc_id,
        },
    )
    monkeypatch.setattr(
        routes,
        "decide_workflow",
        lambda **_kwargs: {
            "status": "decided",
            "doc_id": doc_id,
            "sequence_count": 1,
            "head": None,
        },
    )
    monkeypatch.setattr(routes._db_documents, "get_by_id", lambda _doc_id: None)
    consume = MagicMock()
    monkeypatch.setattr(token_service, "consume", consume)

    response = routes.post_workflow_decide(
        doc_id,
        routes.DecideRequest(
            doc_class="R",
            sequence=[routes.SequenceItem(id=1, type="D", label="Design")],
        ),
        MagicMock(),
    )

    assert response.status_code == 201
    consume.assert_called_once_with(
        token_id="tok-1",
        project_id="flowgate",
        doc_id=doc_id,
    )


def test_workflow_decision_request_requires_update_permission(monkeypatch):
    from modules.flow_gate.api.v1 import workflow_decision_routes as routes

    doc_id = "flowgate.default.0002.0001-R"
    monkeypatch.setattr(
        routes,
        "verify_bearer",
        lambda _request: {
            "_is_user_jwt": True,
            "issued_to": "viewer-1",
        },
    )
    monkeypatch.setattr(
        routes._db_documents,
        "get_by_id",
        lambda _doc_id: {"doc_id": doc_id, "project_id": "flowgate"},
    )
    monkeypatch.setattr(routes, "has_permission", lambda *_args: False)
    request_service = MagicMock()
    monkeypatch.setattr(routes, "request_workflow_decision", request_service)

    response = routes.post_workflow_decision_request(
        routes.WorkflowDecisionRequestBody(doc_id=doc_id),
        MagicMock(),
    )

    assert response.status_code == 403
    assert json.loads(response.body)["error"] == "workflow_decision_permission_denied"
    request_service.assert_not_called()


def test_next_step_token_guard_ignores_workflow_decision_tokens(monkeypatch):
    from modules.flow_gate.db import tokens

    store = MagicMock()
    store._fetch_one.return_value = None
    monkeypatch.setattr(tokens, "get_store", lambda: store)

    tokens.get_unconsumed_by_doc_ref("flowgate.default.0002.0001-R")

    sql, params = store._fetch_one.call_args.args
    assert "action_scope = 'new'" in sql
    assert params == ["flowgate.default.0002.0001-R"]


def test_workflow_decide_broadcasts_status_and_group_refresh(monkeypatch):
    from modules.flow_gate.api.v1 import workflow_decision_routes as routes

    doc_id = "flowgate.default.0002.0001-R"
    monkeypatch.setattr(
        routes,
        "verify_bearer",
        lambda _request: {"_is_user_jwt": True, "issued_to": "user-1"},
    )
    monkeypatch.setattr(
        routes,
        "decide_workflow",
        lambda **_kwargs: {
            "status": "decided",
            "doc_id": doc_id,
            "sequence_count": 1,
            "head": None,
        },
    )
    docs = [
        {
            "doc_id": doc_id,
            "project_id": "flowgate",
            "group_id": "flowgate.default.0002",
            "status": "open",
            "doc_review_status": None,
        },
        {
            "doc_id": doc_id,
            "project_id": "flowgate",
            "group_id": "flowgate.default.0002",
            "status": "open",
            "doc_review_status": "wf_in_progress",
        },
    ]
    monkeypatch.setattr(routes._db_documents, "get_by_id", lambda _doc_id: docs.pop(0))

    broadcast = MagicMock(return_value=1)
    from modules.flow_gate.api.v1.events import publisher
    monkeypatch.setattr(publisher, "broadcast_event_threadsafe", broadcast)

    response = routes.post_workflow_decide(
        doc_id,
        routes.DecideRequest(
            doc_class="R",
            sequence=[routes.SequenceItem(id=1, type="M", label="Memo")],
        ),
        MagicMock(),
    )

    assert response.status_code == 201
    assert broadcast.call_count == 2
    event_types = [call.args[0].event_type.value for call in broadcast.call_args_list]
    assert event_types == ["doc_review_status_changed", "group_view_refresh"]
