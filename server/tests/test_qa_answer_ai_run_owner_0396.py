"""Regression guard for legacy QA answer AI run ownership (0396 T0004)."""
from __future__ import annotations

import inspect
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate import process_service  # noqa: E402
from modules.flow_gate.api.v1 import qa_routes  # noqa: E402
from modules.flow_gate.api.v1.events import publisher  # noqa: E402
from modules.flow_gate.db import ai_invoke_paused_chains as db_paused  # noqa: E402
from modules.flow_gate.services import ai_invoke_service  # noqa: E402
from modules.flow_gate.services import mutation_policy as policy  # noqa: E402
from modules.flow_gate.services import qa_service  # noqa: E402

RUN_ID = "aiv_test_0396_000001"
GROUP_ID = "flowgate.default.0396"
PROJECT = "flowgate"
USER = "4d96c7c2-c0be-4f4e-8594-bd65d2a8fa39"
Q_ID = f"{GROUP_ID}.0002-Q"
A_ID = f"{GROUP_ID}.0005-A"
PARENT_ID = f"{GROUP_ID}.0001-B"
TOKEN_ID = "tok_qa_0396"
SCRATCH = "/scratch/tok_qa_0396"
EXPIRES = "2999-01-01T00:00:00+00:00"
Q_DOC = {
    "doc_id": Q_ID,
    "group_id": GROUP_ID,
    "project_id": PROJECT,
    "triggered_by": PARENT_ID,
}


def _prepare_answer(monkeypatch) -> None:
    monkeypatch.setattr(qa_service, "get_q_for_answer", lambda _qid: dict(Q_DOC))
    monkeypatch.setattr(qa_service, "create_answer_doc", lambda **_kw: (A_ID, "documents/a.md"))
    monkeypatch.setattr(qa_service, "transition_q_to_answered", lambda **_kw: None)
    monkeypatch.setattr(process_service, "is_group_disposed", lambda _gid: False)
    monkeypatch.setattr(qa_routes, "has_permission", lambda *_args: True)
    monkeypatch.setattr(qa_service.db_events, "create", lambda _row: None)

    async def _ignore_event(_event):
        return None

    monkeypatch.setattr(publisher, "publish_event", _ignore_event)


def _request():
    return SimpleNamespace(
        base_url="http://127.0.0.1:8089/",
        headers={"x-locale": "ko"},
    )


def _post(mode: str):
    return qa_routes.post_answer(
        q_id=Q_ID,
        body=qa_routes.AnswerRequest(answer_body="answer", dispatch_mode=mode),
        request=_request(),
        current_user={"user_id": USER},
    )


@pytest.fixture
def ai_dispatch(monkeypatch):
    _prepare_answer(monkeypatch)
    monkeypatch.setattr(db_paused, "get_by_group", lambda _gid: None)
    issued_kwargs: dict = {}

    def _fake_issue(**kwargs):
        issued_kwargs.update(kwargs)
        return {
            "raw_token": "raw-qa-token",
            "token_id": TOKEN_ID,
            "scratch_dir": SCRATCH,
            "expires_at": EXPIRES,
        }

    monkeypatch.setattr(qa_service.token_service, "issue", _fake_issue)
    captured: dict = {}
    start_run_signature = inspect.signature(ai_invoke_service.start_run)

    def _fake_start_run(**kwargs):
        start_run_signature.bind(**kwargs)
        captured.update(kwargs)
        captured["issue"] = ai_invoke_service._call_issue_builder(
            kwargs["issue_builder"], RUN_ID
        )
        return {"run_id": RUN_ID, "status": "running", "provider": "fake"}

    monkeypatch.setattr(ai_invoke_service, "start_run", _fake_start_run)
    payload = json.loads(_post("ai").body)
    return issued_kwargs, captured, payload


def test_ai_builder_mints_followup_token_under_its_run_id(ai_dispatch):
    issued, captured, payload = ai_dispatch
    assert "ai_run_id" in inspect.signature(captured["issue_builder"]).parameters
    assert issued.get("ai_run_id") == RUN_ID
    assert captured["issue"]["token_id"] == TOKEN_ID
    assert payload["ai_run_id"] == RUN_ID
    assert payload["raw_token"] is None


def test_ai_followup_token_passes_the_real_group_lease_gate(ai_dispatch, monkeypatch):
    issued, _captured, _payload = ai_dispatch
    token_row = {
        "token_id": TOKEN_ID,
        "group_id": GROUP_ID,
        "doc_ref": PARENT_ID,
        "action_scope": issued["action_scope"],
        "issued_to": USER,
        "ai_run_id": issued["ai_run_id"],
    }
    lease = {
        "group_id": GROUP_ID,
        "project_id": PROJECT,
        "run_id": RUN_ID,
        "chain_id": RUN_ID,
        "token_id": TOKEN_ID,
        "action_scope": "edit",
        "worker_identity": USER,
        "state": "active",
        "generation": 1,
        "expires_at": EXPIRES,
    }
    beats: list = []
    monkeypatch.setattr(policy.db_leases, "get_active", lambda _gid: dict(lease))
    monkeypatch.setattr(
        policy.db_leases,
        "heartbeat",
        lambda group_id, run_id: beats.append((group_id, run_id)) or True,
    )
    principal = policy.worker_principal(token_row)
    granted = policy.assert_group_mutation_allowed(
        GROUP_ID, principal, "POST /flowgate/api/v1/inbox"
    )
    assert granted["run_id"] == RUN_ID
    assert beats == [(GROUP_ID, RUN_ID)]


def test_runless_followup_token_is_rejected_by_the_real_gate(monkeypatch):
    lease = {
        "group_id": GROUP_ID,
        "project_id": PROJECT,
        "run_id": RUN_ID,
        "chain_id": RUN_ID,
        "token_id": TOKEN_ID,
        "action_scope": "edit",
        "worker_identity": USER,
        "state": "active",
        "generation": 1,
        "expires_at": EXPIRES,
    }
    monkeypatch.setattr(policy.db_leases, "get_active", lambda _gid: dict(lease))
    principal = policy.worker_principal(
        {
            "token_id": TOKEN_ID,
            "group_id": GROUP_ID,
            "doc_ref": PARENT_ID,
            "action_scope": "edit",
            "issued_to": USER,
            "ai_run_id": None,
        }
    )
    with pytest.raises(policy.MutationPolicyError) as exc_info:
        policy.assert_group_mutation_allowed(
            GROUP_ID, principal, "POST /flowgate/api/v1/inbox"
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.error["code"] == "GROUP_AI_RUN_OWNER_MISMATCH"


def test_continuous_paused_chain_resumes_without_issuing_token(monkeypatch):
    _prepare_answer(monkeypatch)
    issued: list = []
    started: list = []
    resumed: list = []

    def _fake_issue(**kwargs):
        issued.append(kwargs)
        return {
            "raw_token": "must-not-be-issued",
            "token_id": TOKEN_ID,
            "scratch_dir": SCRATCH,
            "expires_at": EXPIRES,
        }

    monkeypatch.setattr(qa_service.token_service, "issue", _fake_issue)
    monkeypatch.setattr(qa_service.token_service, "revoke", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        db_paused, "get_by_group", lambda _gid: {"mode": "continuous"}
    )
    monkeypatch.setattr(
        ai_invoke_service,
        "resume_chain",
        lambda **kwargs: resumed.append(kwargs) or {"run_id": RUN_ID},
    )
    monkeypatch.setattr(
        ai_invoke_service,
        "start_run",
        lambda **kwargs: started.append(kwargs) or {"run_id": "wrong"},
    )
    payload = json.loads(_post("ai").body)
    assert issued == []
    assert started == []
    assert len(resumed) == 1
    assert payload["ai_run_id"] == RUN_ID
    assert payload["ai_run_mode"] == "continuous"
    assert payload["token_id"] is None
    assert payload["scratch_dir"] is None
    assert payload["expires_at"] is None


def test_ment_copy_still_issues_immediately_and_returns_mention(monkeypatch):
    _prepare_answer(monkeypatch)
    issued_kwargs: dict = {}

    def _fake_issue(**kwargs):
        issued_kwargs.update(kwargs)
        return {
            "raw_token": "raw-copy-token",
            "token_id": TOKEN_ID,
            "scratch_dir": SCRATCH,
            "expires_at": EXPIRES,
        }

    monkeypatch.setattr(qa_service.token_service, "issue", _fake_issue)
    payload = json.loads(_post("ment_copy").body)
    assert issued_kwargs.get("ai_run_id") is None
    assert payload["raw_token"] == "raw-copy-token"
    assert payload["token_id"] == TOKEN_ID
    assert "raw-copy-token" in payload["ment_text"]
