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
        svc.admission_run_id.set("aiv_partial")
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


def test_wrapper_preserves_concurrent_request_lease_and_token(monkeypatch):
    active = {"value": None}
    released, revoked = [], []
    monkeypatch.setattr(svc.db_group_ai_leases, "get_active", lambda _: active["value"])
    monkeypatch.setattr(svc.db_group_ai_leases, "release", lambda *a, **k: released.append(a))
    monkeypatch.setattr(svc.token_service, "revoke", lambda *a, **k: revoked.append(a))
    monkeypatch.setattr(svc._facade, "_runs", {})

    def fail_after_other_request_takes_lease(**kwargs):
        svc.admission_run_id.set("aiv_failed")
        active["value"] = {
            "run_id": "aiv_other", "state": "acquiring", "token_id": "tok_other",
            "worker_identity": kwargs["issued_to"], "action_scope": kwargs["action_scope"],
        }
        raise RuntimeError("failed request")

    monkeypatch.setattr(svc, "_admission_start_run", fail_after_other_request_takes_lease)
    with pytest.raises(HTTPException):
        svc.start_run(**_kwargs())
    assert released == []
    assert revoked == []


def test_wrapper_preserves_registered_run_even_with_matching_id(monkeypatch):
    active = {"run_id": "aiv_live", "state": "active", "token_id": "tok_live"}
    released, revoked = [], []
    monkeypatch.setattr(svc.db_group_ai_leases, "get_active", lambda _: active)
    monkeypatch.setattr(svc.db_group_ai_leases, "release", lambda *a, **k: released.append(a))
    monkeypatch.setattr(svc.token_service, "revoke", lambda *a, **k: revoked.append(a))
    monkeypatch.setattr(svc._facade, "_runs", {"aiv_live": {"status": "running"}})
    # Simulate a previously absent lease becoming a registered run during admission.
    reads = iter([None, active])
    monkeypatch.setattr(svc.db_group_ai_leases, "get_active", lambda _: next(reads))
    def fail(**kwargs):
        svc.admission_run_id.set("aiv_live")
        raise RuntimeError("post registration")
    monkeypatch.setattr(svc, "_admission_start_run", fail)
    with pytest.raises(HTTPException):
        svc.start_run(**_kwargs())
    assert released == []
    assert revoked == []

def _real_admission_stub(monkeypatch):
    from modules.flow_gate.services.ai_invoke import admission
    from modules.flow_gate.services import provider_capability_service
    from modules.flow_gate.services import token_scratch
    monkeypatch.setattr(
        admission.ai_settings_service, "resolve_effective",
        lambda _: {"providers": [{"id": "prov-1", "name": "P1"}],
                   "source": "project", "registered_count": 1},
    )
    monkeypatch.setattr(admission.db_docs, "get_by_id",
                        lambda _: {"type": "TR", "revision_no": 1, "branch": "main"})
    monkeypatch.setattr(admission.db_docs, "get_group_max_seq", lambda _: 0)
    monkeypatch.setattr(provider_capability_service, "capability_finding",
                        lambda *a, **k: None)
    monkeypatch.setattr(admission.git_service, "get_branch_merge_group_claim", lambda _: None)
    monkeypatch.setattr(admission, "_refuse_frozen_group", lambda _: None)
    monkeypatch.setattr(admission.db_git, "get_config", lambda _: None)
    monkeypatch.setattr(admission.git_service, "ensure_initial_group_source_sync",
                        lambda *a, **k: {"reason": "already_synced"})
    leases = {"row": None}
    releases, revokes = [], []
    monkeypatch.setattr(admission.db_group_ai_leases, "get_active",
                        lambda _: leases["row"])
    def acquire(**kwargs):
        assert leases["row"] is None
        leases["row"] = {"run_id": kwargs["run_id"], "state": "acquiring",
                         "group_id": kwargs["group_id"]}
        return dict(leases["row"])
    def release(group_id, run_id, reason=None):
        if leases["row"] and leases["row"]["run_id"] == run_id:
            leases["row"] = None
        releases.append((run_id, reason))
        return True
    monkeypatch.setattr(admission.db_group_ai_leases, "acquire", acquire)
    monkeypatch.setattr(admission.db_group_ai_leases, "release", release)
    monkeypatch.setattr(admission.token_service, "revoke",
                        lambda token_id, reason=None: revokes.append((token_id, reason)))
    ids = iter(["aiv_first", "aiv_retry", "aiv_third"])
    monkeypatch.setattr(admission._svc(), "_next_run_id", lambda: next(ids))
    kwargs = {**_kwargs(), "action_scope": "review",
              "issue_builder": lambda **k: None}
    return admission, token_scratch, leases, releases, revokes, kwargs


@pytest.mark.parametrize("failure", ["issue_builder", "token_issue", "mention_builder"])
def test_real_admission_failure_releases_only_own_lease_and_allows_retry(monkeypatch, failure):
    admission, scratch, leases, releases, revokes, kwargs = _real_admission_stub(monkeypatch)
    if failure == "issue_builder":
        kwargs["issue_builder"] = lambda **k: (_ for _ in ()).throw(ValueError("issue failed"))
        expected = ValueError
    elif failure == "token_issue":
        kwargs.pop("issue_builder")
        monkeypatch.setattr(admission.token_service, "issue",
                            lambda **k: (_ for _ in ()).throw(
                                scratch.TokenScratchStorageUnsafe("unsafe")))
        expected = scratch.TokenScratchStorageUnsafe
    else:
        kwargs.pop("issue_builder")
        monkeypatch.setattr(admission.token_service, "issue",
                            lambda **k: {"token_id": "tok_owned", "raw_token": "raw",
                                         "scratch_dir": "work"})
        kwargs["mention_builder"] = lambda *a: (_ for _ in ()).throw(RuntimeError("mention failed"))
        expected = RuntimeError
    with pytest.raises(expected):
        admission.start_run(**kwargs)
    assert leases["row"] is None
    assert any(run_id == "aiv_first" for run_id, _ in releases)
    if failure == "mention_builder":
        assert [token for token, _ in revokes] == ["tok_owned"]
    else:
        assert revokes == []

    # A second request reaches its own issue builder immediately, proving no stale lease.
    kwargs["issue_builder"] = lambda **k: (_ for _ in ()).throw(ValueError("retry reached issue"))
    with pytest.raises(ValueError, match="retry reached issue"):
        admission.start_run(**kwargs)
    assert leases["row"] is None


def test_real_admission_does_not_release_replaced_lease(monkeypatch):
    admission, _, leases, releases, revokes, kwargs = _real_admission_stub(monkeypatch)
    def fail_after_replacement(**k):
        leases["row"] = {"run_id": "aiv_other", "state": "acquiring"}
        raise ValueError("failed request")
    kwargs["issue_builder"] = fail_after_replacement
    with pytest.raises(ValueError, match="failed request"):
        admission.start_run(**kwargs)
    assert leases["row"]["run_id"] == "aiv_other"
    assert releases == []
    assert revokes == []

def test_api_maps_storage_unsafe_to_safe_code(monkeypatch):
    import json
    from starlette.requests import Request
    from modules.flow_gate.api.v1 import ai_invoke_routes as routes
    from modules.flow_gate.services import token_scratch
    monkeypatch.setattr(routes, "_require_user",
                        lambda _: {"issued_to": "user-1", "is_admin": True})
    monkeypatch.setattr(routes.db_projects, "get_by_id", lambda _: {"project_id": "flowgate"})
    monkeypatch.setattr(
        routes.ai_invoke_service, "start_run",
        lambda **kwargs: (_ for _ in ()).throw(
            token_scratch.TokenScratchStorageUnsafe("private C:/internal/storage/path")),
    )
    import sys
    import types
    fake_token_routes = types.ModuleType("modules.flow_gate.api.token_routes")
    fake_token_routes._build_api_base = lambda _: "http://testserver/flowgate/api/v1"
    monkeypatch.setitem(sys.modules, "modules.flow_gate.api.token_routes", fake_token_routes)
    request = Request({"type": "http", "scheme": "http",
                       "server": ("testserver", 80), "path": "/flowgate/api/v1/ai-invoke/start",
                       "headers": [], "query_string": b""})
    body = routes.AiInvokeStartRequest(
        project="flowgate", module="default", group="0491",
        doc_ref="flowgate.default.0491.0004-T", action_scope="new", mode="single",
    )
    response = routes.start_ai_invoke(body, request)
    payload = json.loads(response.body)
    assert response.status_code == 409
    assert payload["code"] == "token_scratch_storage_unsafe"
    assert "private" not in payload["message"]
    assert "C:/" not in payload["message"]
