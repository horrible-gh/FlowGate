"""0061 TR0005 — predecessor endpoint parity (NextActionModal visible auto-check).

The review rejection was that the [Proceed to next step] dialog did not *show* the
2-previous action checked. The fix gives the modal a server endpoint
(`GET /api/v1/documents/{doc_id}/predecessors`) that reuses the SAME helper the token
path uses (`get_predecessor_result_doc_ids`), so the dialog's checked set matches what
the worker receives — no client/server drift. These tests pin that the endpoint mirrors
the token-path computation (same sequence, same effective head, same helper).
"""
import json

from modules.flow_gate.api.v1 import module_routes
from modules.flow_gate.db import workflow_sequences as db_wfseq


class _FakeRequest:
    base_url = "http://192.168.0.250:8089/"


def _body(resp):
    return json.loads(resp.body)


def _wire(monkeypatch):
    monkeypatch.setattr(module_routes, "verify_bearer", lambda req: {"user_id": "tester"})
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", lambda did: {"id": 7})
    monkeypatch.setattr(
        db_wfseq, "get_effective_head", lambda sid: {"id": 5, "type": "TR"}
    )
    monkeypatch.setattr(
        db_wfseq,
        "get_predecessor_result_doc_ids",
        lambda sid, hid, limit=2: ["G-T0004", "G-NR0003"],
    )


def test_endpoint_returns_two_predecessors(monkeypatch):
    _wire(monkeypatch)
    resp = module_routes.list_predecessor_docs(_FakeRequest(), "G-R0001", limit=2)
    body = _body(resp)
    assert body["ok"] is True
    assert body["doc_id"] == "G-R0001"
    # previous + the one before it (e.g. T and NR when advancing to TR).
    assert body["predecessor_doc_ids"] == ["G-T0004", "G-NR0003"]


def test_endpoint_mirrors_effective_head_exclusion(monkeypatch):
    """Endpoint must pass the effective-head item id to the helper (same exclusion
    the token path applies), not a hard-coded None."""
    _wire(monkeypatch)
    seen = {}

    def _capture(sid, hid, limit=2):
        seen["sid"] = sid
        seen["hid"] = hid
        return ["G-T0004"]

    monkeypatch.setattr(db_wfseq, "get_predecessor_result_doc_ids", _capture)
    module_routes.list_predecessor_docs(_FakeRequest(), "G-R0001", limit=2)
    assert seen["sid"] == 7
    assert seen["hid"] == 5  # effective head's item id


def test_endpoint_no_sequence_returns_empty(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", lambda did: None)
    body = _body(module_routes.list_predecessor_docs(_FakeRequest(), "G-R0001"))
    assert body["ok"] is True
    assert body["predecessor_doc_ids"] == []


def test_endpoint_rejects_out_of_range_limit(monkeypatch):
    _wire(monkeypatch)
    assert module_routes.list_predecessor_docs(_FakeRequest(), "G-R0001", limit=0).status_code == 400
    assert module_routes.list_predecessor_docs(_FakeRequest(), "G-R0001", limit=11).status_code == 400
