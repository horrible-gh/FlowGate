"""flowgate.default.0317 T0013 결함 ② — the continuous FIRST hop must forward the chosen
instruction mode to advance_workflow.

_issue_first_hop routes the continuous first hop through advance_workflow (the only place
N/T instruction heads are auto-created + auto-approved for an unmanned chain). It passed
every continuation field EXCEPT continuation_instruction_mode, which then normalized to
auto_approved — and that value gets baked into the issued token and propagated down the
whole self-chain (inbox reads it off token_rec and hands it to each re-spawn). So
[지시서 작성 후 진행](ai_direct) died on EVERY hop, not just the first, and N/T steps were
silently auto-approved away ("단계 건너뜀").

These pin the parity with _issue_workflow_decision (workflow_decide scope), which already
forwarded the mode. The harness mirrors test_ai_invoke_continuation_target_0242.py.
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
from modules.flow_gate.services import workflow_decision_service  # noqa: E402

ROOT_DOC = "flowgate.default.0317.0001-R"

# N/NR done, T/TR pending → item_seq 4 (TR) is a valid continuation target.
_ITEMS = [
    {"id": 1, "item_seq": 1, "type": "N", "label": "조사지시",
     "result_doc_id": "flowgate.default.0317.0002-N", "result_doc_review_status": "approved"},
    {"id": 2, "item_seq": 2, "type": "NR", "label": "조사레포트",
     "result_doc_id": "flowgate.default.0317.0003-NR", "result_doc_review_status": "approved"},
    {"id": 3, "item_seq": 3, "type": "T", "label": "작업지시",
     "result_doc_id": None, "result_doc_review_status": None},
    {"id": 4, "item_seq": 4, "type": "TR", "label": "작업레포트",
     "result_doc_id": None, "result_doc_review_status": None},
]


@pytest.fixture
def harness(monkeypatch):
    """A TestClient plus the list of advance_workflow calls _issue_first_hop makes.

    start_run is stubbed to INVOKE the issue_builder it is handed (the real engine calls it
    to mint the hop token) so the closure under test actually runs, then returns a running
    envelope without spawning a worker.
    """
    app = FastAPI()
    app.include_router(ai_invoke_routes.router)

    monkeypatch.setattr(
        ai_invoke_routes, "verify_bearer",
        lambda request: {"_is_user_jwt": True, "issued_to": "usr_admin", "is_admin": True},
    )
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id", lambda pid: {"project_id": pid})
    monkeypatch.setattr(db_wfseq, "get_sequence_for_member_doc", lambda doc_id: {"id": 1})
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda seq_id: list(_ITEMS))

    adv_calls: list[dict] = []

    def _fake_advance(**kw):
        adv_calls.append(kw)
        return {"token": "raw_tok", "token_id": "tok_1",
                "scratch_dir": "/tmp/s", "mention": "MENTION"}
    monkeypatch.setattr(workflow_decision_service, "advance_workflow", _fake_advance)

    def _fake_start_run(**kw):
        builder = kw.get("issue_builder")
        if builder is not None:
            builder()  # the engine calls this to mint the hop token — run the closure
        return {"run_id": "aiv_1", "status": "running"}
    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", _fake_start_run)

    client = TestClient(app, raise_server_exceptions=False)
    return client, adv_calls


def _post(client, **over):
    body = {
        "project": "flowgate", "module": "default", "group": "0317",
        "doc_ref": ROOT_DOC, "action_scope": "new", "mode": "continuous",
        "continuation_target_seq": 4,
    }
    body.update(over)
    return client.post("/api/v1/ai-invoke/start", json=body,
                       headers={"Authorization": "Bearer tok"})


def test_first_hop_forwards_ai_direct_instruction_mode(harness):
    client, adv_calls = harness
    resp = _post(client, continuation_instruction_mode="ai_direct")
    assert resp.status_code == 200, resp.text
    assert len(adv_calls) == 1
    # The fix: the chosen mode reaches advance_workflow instead of defaulting to auto_approved.
    assert adv_calls[0]["continuation_instruction_mode"] == "ai_direct"
    assert adv_calls[0]["continuous"] is True


def test_first_hop_forwards_auto_approved_instruction_mode(harness):
    client, adv_calls = harness
    resp = _post(client, continuation_instruction_mode="auto_approved")
    assert resp.status_code == 200, resp.text
    assert adv_calls[0]["continuation_instruction_mode"] == "auto_approved"
