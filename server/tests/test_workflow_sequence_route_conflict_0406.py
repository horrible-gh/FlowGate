"""0406 T0005: query-form workflow sequence has one canonical GET contract."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("ALLOWED_ORIGIN", "")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import workflow_decision_routes  # noqa: E402
from modules.flow_gate.api.v1 import workflow_head_routes  # noqa: E402
from modules.flow_gate.services import workflow_decision_service  # noqa: E402
from routers.main import app  # noqa: E402

_ROOT_DOC = "flowgate.default.0406.0001-B"
_SEQUENCE_ID = 406
_QUERY_PATH = "/flowgate/api/v1/workflow/sequence"
_AUTH_HEADERS = {"Authorization": "Bearer test-token"}
_REQUIRED_METADATA = {"note", "source_doc_id", "source_revision_no"}

_STORED_ITEMS = [
    {
        "id": 4061,
        "item_seq": 1,
        "type": "T",
        "label": "Implementation instruction",
        "doc_class": "B",
        "sort_order": 1,
        "status": "done",
        "note": "Keep the saved handoff",
        "source_doc_id": "flowgate.default.0406.0004-WP",
        "source_revision_no": 7,
        "result_doc_id": "flowgate.default.0406.0005-T",
        "result_doc_review_status": "approved",
        "result_seq": 5,
    },
    {
        "id": 4062,
        "item_seq": 2,
        "type": "TR",
        "label": "Implementation report",
        "doc_class": "B",
        "sort_order": 2,
        "status": "pending",
        "note": None,
        "source_doc_id": None,
        "source_revision_no": None,
        "result_doc_id": None,
        "result_doc_review_status": None,
        "result_seq": None,
    },
]


def _routes_for(method: str):
    return [
        route
        for route in app.routes
        if route.path == _QUERY_PATH and method in (route.methods or set())
    ]


def test_query_sequence_get_and_patch_are_registered_once_on_real_app():
    get_routes = _routes_for("GET")
    assert len(get_routes) == 1
    assert get_routes[0].endpoint is workflow_decision_routes.get_workflow_sequence_endpoint

    patch_routes = _routes_for("PATCH")
    assert len(patch_routes) == 1
    assert patch_routes[0].endpoint is workflow_decision_routes.patch_workflow_sequence_endpoint
    print(
        "ROUTE_REGISTRATION="
        + json.dumps(
            {
                "GET": len(get_routes),
                "GET_endpoint": get_routes[0].endpoint.__name__,
                "PATCH": len(patch_routes),
                "PATCH_endpoint": patch_routes[0].endpoint.__name__,
            },
            sort_keys=True,
        )
    )


@pytest.fixture
def client(monkeypatch):
    authenticated = {
        "_is_user_jwt": True,
        "issued_to": "usr_test",
        "is_admin": True,
    }
    monkeypatch.setattr(
        workflow_decision_routes, "verify_bearer", lambda request: authenticated
    )
    monkeypatch.setattr(
        workflow_head_routes, "verify_bearer", lambda request: authenticated
    )
    monkeypatch.setattr(
        workflow_decision_service.db_documents,
        "get_by_id",
        lambda doc_id: {"doc_id": doc_id, "type_code": "B"},
    )
    monkeypatch.setattr(
        workflow_decision_service.db_wfseq,
        "get_sequence_by_doc_id",
        lambda doc_id: {"id": _SEQUENCE_ID, "doc_id": doc_id},
    )
    monkeypatch.setattr(
        workflow_decision_service.db_wfseq,
        "get_sequence_items",
        lambda sequence_id: [dict(item) for item in _STORED_ITEMS],
    )
    monkeypatch.setattr(
        workflow_decision_service.db_wfseq,
        "get_effective_head",
        lambda sequence_id: dict(_STORED_ITEMS[1]),
    )
    return TestClient(app, raise_server_exceptions=False)


def test_query_sequence_response_preserves_metadata_keys_on_every_row(client):
    response = client.get(
        _QUERY_PATH,
        params={"doc_id": _ROOT_DOC},
        headers=_AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    print("QUERY_SEQUENCE_RESPONSE=" + json.dumps(body, ensure_ascii=False, sort_keys=True))

    assert body["doc_id"] == _ROOT_DOC
    assert body["doc_class"] == "B"
    assert body["decided"] is True
    assert body["sequence_id"] == _SEQUENCE_ID
    assert len(body["items"]) == len(_STORED_ITEMS)
    assert all(_REQUIRED_METADATA <= set(item) for item in body["items"])

    assert body["items"][0]["note"] == "Keep the saved handoff"
    assert body["items"][0]["source_doc_id"] == "flowgate.default.0406.0004-WP"
    assert body["items"][0]["source_revision_no"] == 7
    assert body["items"][1]["note"] == ""
    assert body["items"][1]["source_doc_id"] is None
    assert body["items"][1]["source_revision_no"] is None


def test_undecided_query_keeps_canonical_400_contract(client, monkeypatch):
    monkeypatch.setattr(
        workflow_decision_service.db_wfseq,
        "get_sequence_by_doc_id",
        lambda doc_id: None,
    )
    response = client.get(
        _QUERY_PATH,
        params={"doc_id": _ROOT_DOC},
        headers=_AUTH_HEADERS,
    )
    assert response.status_code == 400
    assert response.json() == {"error": "sequence_not_decided", "doc_id": _ROOT_DOC}


def test_path_sequence_keeps_head_and_result_document_contract(client):
    response = client.get(
        f"/flowgate/api/v1/workflow/{_ROOT_DOC}/sequence",
        headers=_AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    print("PATH_SEQUENCE_RESPONSE=" + json.dumps(body, ensure_ascii=False, sort_keys=True))

    assert body["decided"] is True
    assert body["head"]["item_seq"] == 2
    assert body["sequence"][0]["result_doc_id"] == "flowgate.default.0406.0005-T"
    assert body["sequence"][0]["result_seq"] == 5
    assert all("result_doc_id" in item and "result_seq" in item for item in body["sequence"])