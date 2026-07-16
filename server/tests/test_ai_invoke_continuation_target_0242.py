"""flowgate.default.0242 T0004 — /ai-invoke/start must reject a bogus continuation target.

NR0003 발견 4: the only continuation_target_seq checks were "not null" and "-1 for a
pre-decision run". Every other number was accepted, and the mistake surfaced only as
*behaviour* at chain-termination time (inbox_routes: ``completed_seq >= target_seq``):

  - an already-done seq  → chain stopped after ONE document, no error
  - a nonexistent seq    → chain ran the sequence to its very end, no error

For an unmanned run nobody is watching, so a silent under/over-run is a safety problem.
The UI now picks the target from the live sequence, but the endpoint is reachable without
it — these tests pin the server-side rule the hint text previously only described.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import ai_invoke_routes  # noqa: E402
from modules.flow_gate.db import workflow_sequences as db_wfseq  # noqa: E402

ROOT_DOC = "flowgate.default.0242.0001-R"

# N/NR realized+approved (done), T/TR pending. Only 3 and 4 are runnable targets.
_ITEMS = [
    {"id": 1, "item_seq": 1, "type": "N", "label": "조사지시",
     "result_doc_id": "flowgate.default.0242.0002-N", "result_doc_review_status": "approved"},
    {"id": 2, "item_seq": 2, "type": "NR", "label": "조사레포트",
     "result_doc_id": "flowgate.default.0242.0003-NR", "result_doc_review_status": "approved"},
    {"id": 3, "item_seq": 3, "type": "T", "label": "작업지시",
     "result_doc_id": None, "result_doc_review_status": None},
    {"id": 4, "item_seq": 4, "type": "TR", "label": "작업레포트",
     "result_doc_id": None, "result_doc_review_status": None},
]


@pytest.fixture
def client(monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(ai_invoke_routes.router)
    monkeypatch.setattr(
        ai_invoke_routes, "verify_bearer",
        lambda request: {"_is_user_jwt": True, "issued_to": "usr_admin", "is_admin": True},
    )
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id", lambda pid: {"project_id": pid})
    # _continuation_target_error imports the db module lazily, so patch it at the source.
    monkeypatch.setattr(db_wfseq, "get_sequence_for_member_doc", lambda doc_id: {"id": 1})
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda seq_id: list(_ITEMS))
    monkeypatch.setattr(
        ai_invoke_routes.ai_invoke_service, "start_run",
        lambda **kw: {"run_id": "aiv_1", "status": "running"},
    )
    return TestClient(app, raise_server_exceptions=False)


def _body(**over):
    body = {
        "project": "flowgate", "module": "default", "group": "0242",
        "doc_ref": ROOT_DOC, "action_scope": "new", "mode": "continuous",
        "continuation_target_seq": 4,
    }
    body.update(over)
    return body


def _post(client, **over):
    return client.post("/api/v1/ai-invoke/start", json=_body(**over),
                       headers={"Authorization": "Bearer tok"})


def _locs(resp):
    return [e["loc"] for e in resp.json()["errors"]]


def test_remaining_step_is_accepted(client):
    # item_seq 3 (T, pending) and 4 (TR, pending) are the remaining steps — both runnable.
    assert _post(client, continuation_target_seq=3).status_code == 200
    resp = _post(client, continuation_target_seq=4)
    assert resp.status_code == 200
    assert resp.json()["run_id"] == "aiv_1"


def test_already_completed_step_is_rejected(client):
    # Previously accepted: the chain registered one document, saw completed_seq >= 1, and
    # stopped — the user just saw "왜 1개만 하고 멈췄지?" with no error anywhere.
    resp = _post(client, continuation_target_seq=1)

    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_failed"
    assert _locs(resp) == ["continuation_target_seq"]
    assert "already complete" in resp.json()["errors"][0]["msg"]


def test_nonexistent_step_is_rejected(client):
    # The 3-vs-30 typo: previously ran the WHOLE sequence unattended instead of 2 steps.
    resp = _post(client, continuation_target_seq=30)

    assert resp.status_code == 422
    assert _locs(resp) == ["continuation_target_seq"]
    assert "does not exist" in resp.json()["errors"][0]["msg"]


def test_run_to_end_sentinel_is_rejected_outside_a_pre_decision_run(client):
    # -1 is only meaningful for action_scope=workflow_decide. On a decided sequence it is
    # not a step, and `completed_seq >= -1` would have ended the chain immediately.
    resp = _post(client, continuation_target_seq=-1)

    assert resp.status_code == 422
    assert _locs(resp) == ["continuation_target_seq"]


def test_pre_decision_run_skips_sequence_validation(client):
    # workflow_decide + -1: the sequence does not exist yet, so there is nothing to check
    # against. The existing "-1 required" rule already covers this scope.
    resp = _post(client, action_scope="workflow_decide", continuation_target_seq=-1)

    assert resp.status_code == 200


def test_single_mode_ignores_the_target(client):
    # A single run never chains, so a stale target must not block it.
    resp = _post(client, mode="single", continuation_target_seq=1)

    assert resp.status_code == 200


def test_undecided_sequence_is_not_blocked(client, monkeypatch):
    # No sequence resolves for the doc → no item_seqs to validate against. Stay permissive
    # rather than inventing a rule the DB cannot answer.
    monkeypatch.setattr(db_wfseq, "get_sequence_for_member_doc", lambda doc_id: None)

    assert _post(client, continuation_target_seq=7).status_code == 200
