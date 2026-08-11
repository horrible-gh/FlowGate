"""0406 T0007: canonical GET metadata survives a no-edit PATCH round trip."""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("ALLOWED_ORIGIN", "")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import workflow_decision_routes  # noqa: E402
from modules.flow_gate.services import workflow_decision_service  # noqa: E402
from routers.main import app  # noqa: E402

_ROOT_DOC = "flowgate.default.0406.0001-B"
_QUERY_PATH = "/flowgate/api/v1/workflow/sequence"
_AUTH_HEADERS = {"Authorization": "Bearer test-token"}
_METADATA_KEYS = ("note", "source_doc_id", "source_revision_no")

_STORED_PENDING = [
    {
        "id": 4061,
        "item_seq": 1,
        "type": "M",
        "label": "Keep the handoff",
        "doc_class": "B",
        "sort_order": 1,
        "status": "pending",
        "note": "Keep the saved handoff",
        "source_doc_id": "flowgate.default.0406.0004-WP",
        "source_revision_no": 7,
        "result_doc_id": None,
    },
    {
        "id": 4062,
        "item_seq": 2,
        "type": "WP",
        "label": "Empty metadata values",
        "doc_class": "B",
        "sort_order": 2,
        "status": "pending",
        "note": None,
        "source_doc_id": None,
        "source_revision_no": None,
        "result_doc_id": None,
    },
    {
        "id": 4063,
        "item_seq": 3,
        "type": "D",
        "label": "Revision zero",
        "doc_class": "B",
        "sort_order": 3,
        "status": "pending",
        "note": "Revision zero survives",
        "source_doc_id": "flowgate.default.0406.0003-WP",
        "source_revision_no": 0,
        "result_doc_id": None,
    },
]


class _MemoryStore:
    @contextmanager
    def transaction(self):
        yield


def _metadata(rows):
    return [{key: row[key] for key in _METADATA_KEYS} for row in rows]


def test_canonical_sequence_metadata_survives_get_patch_get(monkeypatch):
    """Reuse the real app/routes and service with only its persistence boundary in memory."""
    state = [dict(item) for item in _STORED_PENDING]
    sequence = {"id": 406, "doc_id": _ROOT_DOC, "head_advanced_at": None}
    owner_doc = {
        "doc_id": _ROOT_DOC,
        "type_code": "B",
        "doc_review_status": "wf_in_progress",
        "project_id": None,
        "group_id": None,
    }

    monkeypatch.setattr(
        workflow_decision_routes,
        "verify_bearer",
        lambda request: {"_is_user_jwt": True, "issued_to": "usr_test", "is_admin": True},
    )
    monkeypatch.setattr(
        workflow_decision_routes,
        "_active_ai_run_response_for_user",
        lambda doc, auth: None,
    )
    monkeypatch.setattr(
        workflow_decision_service.db_documents,
        "get_by_id",
        lambda doc_id: dict(owner_doc) if doc_id == _ROOT_DOC else None,
    )
    monkeypatch.setattr(
        workflow_decision_service.db_documents,
        "update",
        lambda doc_id, values: None,
    )
    monkeypatch.setattr(
        workflow_decision_service.db_wfseq,
        "get_sequence_by_doc_id",
        lambda doc_id: dict(sequence) if doc_id == _ROOT_DOC else None,
    )
    monkeypatch.setattr(
        workflow_decision_service.db_wfseq,
        "get_sequence_items",
        lambda sequence_id: [dict(item) for item in state],
    )
    monkeypatch.setattr(
        workflow_decision_service.db_wfseq,
        "get_max_item_seq",
        lambda sequence_id: max((item["item_seq"] for item in state), default=0),
    )

    def delete_pending_items(sequence_id):
        state[:] = [item for item in state if item["status"] != "pending"]

    def insert_sequence_item(**values):
        state.append(
            {
                "id": 5000 + len(state),
                "item_seq": values["item_seq"],
                "type": values["type_"],
                "label": values["label"],
                "doc_class": values["doc_class"],
                "sort_order": values["sort_order"],
                "status": "pending",
                "note": values["note"],
                "source_doc_id": values["source_doc_id"],
                "source_revision_no": values["source_revision_no"],
                "result_doc_id": None,
            }
        )

    monkeypatch.setattr(
        workflow_decision_service.db_wfseq,
        "delete_pending_items",
        delete_pending_items,
    )
    monkeypatch.setattr(
        workflow_decision_service.db_wfseq,
        "insert_sequence_item",
        insert_sequence_item,
    )
    monkeypatch.setattr(workflow_decision_service, "get_store", lambda: _MemoryStore())

    client = TestClient(app, raise_server_exceptions=False)
    first = client.get(_QUERY_PATH, params={"doc_id": _ROOT_DOC}, headers=_AUTH_HEADERS)
    assert first.status_code == 200, first.text
    before_rows = [row for row in first.json()["items"] if row["status"] == "pending"]

    # This is the modal's save projection, mechanically built from the canonical response.
    patch_items = [
        {
            "type": row["type"],
            "label": row["label"],
            "note": row["note"],
            "source_doc_id": row["source_doc_id"],
            "source_revision_no": row["source_revision_no"],
        }
        for row in before_rows
    ]
    patched = client.patch(
        _QUERY_PATH,
        json={"doc_id": _ROOT_DOC, "items": patch_items},
        headers=_AUTH_HEADERS,
    )
    assert patched.status_code == 200, patched.text

    second = client.get(_QUERY_PATH, params={"doc_id": _ROOT_DOC}, headers=_AUTH_HEADERS)
    assert second.status_code == 200, second.text
    after_rows = [row for row in second.json()["items"] if row["status"] == "pending"]

    before = _metadata(before_rows)
    after = _metadata(after_rows)
    assert len(after_rows) == len(before_rows) == 3
    assert after == before
    assert after[0] == {
        "note": "Keep the saved handoff",
        "source_doc_id": "flowgate.default.0406.0004-WP",
        "source_revision_no": 7,
    }
    assert after[1] == {"note": "", "source_doc_id": None, "source_revision_no": None}
    assert after[2]["source_revision_no"] == 0
    print("SERVER_ROUNDTRIP_METADATA=" + json.dumps({"before": before, "after": after}, sort_keys=True))