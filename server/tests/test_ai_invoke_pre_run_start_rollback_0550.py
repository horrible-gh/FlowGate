"""0550 regression: unexpected start failures do not strand a group AI lease."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from modules.flow_gate.services import ai_invoke_service as svc


def _kwargs():
    return {
        "project_id": "flowgate",
        "module": "default",
        "group_id": "flowgate.default.0550",
        "doc_ref": "flowgate.default.0550.0001-T",
        "action_scope": "new",
        "mode": "single",
        "continuation_target_seq": None,
        "continuation_review_mode": False,
        "continuation_instruction_mode": None,
        "continuation_locale": None,
        "issued_to": "user-1",
        "api_base_url": "http://flowgate.test/flowgate/api/v1",
        "mention_builder": lambda *_: "mention",
    }


def test_unexpected_partial_admission_releases_orphan_and_surfaces_detail(monkeypatch):
    active = {"value": None}
    released = []
    revoked = []

    monkeypatch.setattr(svc.db_group_ai_leases, "get_active", lambda group_id: active["value"])
    monkeypatch.setattr(
        svc.db_group_ai_leases,
        "release",
        lambda group_id, run_id, reason=None: released.append((group_id, run_id, reason)),
    )
    monkeypatch.setattr(
        svc.token_service,
        "revoke",
        lambda token_id, reason=None: revoked.append((token_id, reason)),
    )
    monkeypatch.setattr(svc._facade, "_runs", {})

    def _boom(**kwargs):
        active["value"] = {
            "group_id": kwargs["group_id"],
            "run_id": "aiv_partial",
            "state": "active",
            "action_scope": kwargs["action_scope"],
            "worker_identity": kwargs["issued_to"],
            "token_id": "tok_partial",
        }
        raise RuntimeError("synthetic start failure")

    monkeypatch.setattr(svc, "_admission_start_run", _boom)

    with pytest.raises(HTTPException) as caught:
        svc.start_run(**_kwargs())

    assert caught.value.status_code == 500
    assert caught.value.detail["code"] == "ai_invoke_start_internal_error"
    assert caught.value.detail["phase"] == "pre_run_admission"
    assert "RuntimeError: synthetic start failure" in caught.value.detail["message"]
    assert revoked == [("tok_partial", "ai_invoke_partial_admission_rollback")]
    assert released == [
        (
            "flowgate.default.0550",
            "aiv_partial",
            "admission_rollback_unexpected_start_failure",
        )
    ]


def test_preexisting_lease_is_never_released(monkeypatch):
    existing = {
        "group_id": "flowgate.default.0550",
        "run_id": "aiv_existing",
        "state": "active",
        "action_scope": "new",
        "worker_identity": "user-1",
        "token_id": "tok_existing",
    }
    released = []
    monkeypatch.setattr(svc.db_group_ai_leases, "get_active", lambda group_id: existing)
    monkeypatch.setattr(
        svc.db_group_ai_leases,
        "release",
        lambda *args, **kwargs: released.append((args, kwargs)),
    )
    monkeypatch.setattr(svc, "_admission_start_run", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(HTTPException):
        svc.start_run(**_kwargs())

    assert released == []


def test_expected_domain_error_keeps_existing_contract(monkeypatch):
    monkeypatch.setattr(svc.db_group_ai_leases, "get_active", lambda group_id: None)
    expected = HTTPException(status_code=409, detail={"code": "run_in_progress", "message": "busy"})
    monkeypatch.setattr(svc, "_admission_start_run", lambda **kwargs: (_ for _ in ()).throw(expected))

    with pytest.raises(HTTPException) as caught:
        svc.start_run(**_kwargs())

    assert caught.value is expected
