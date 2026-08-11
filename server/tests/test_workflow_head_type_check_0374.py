"""0374 T0004 — reject new inbox documents whose type differs from the workflow head."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from inbox_client import post_inbox


def _body(doc_type: str, *, dry_run: bool = False) -> dict:
    return {
        "action": "new",
        "project": "flowgate",
        "module": "default",
        "group_name": "flowgate.default.0374",
        "prev_doc_id": "flowgate.default.0374.0004-T",
        "doc_type": doc_type,
        "title": "head type guard",
        "content": "body",
        "dry_run": dry_run,
    }


def _patch_validation(monkeypatch, head):
    from modules.flow_gate.api import inbox_routes

    token = {
        "token_id": "tok-0374",
        "project": "flowgate",
        "issued_to": "worker-0374",
        "action_scope": "new",
        "doc_ref": "flowgate.default.0374.0004-T",
        "dry_run_count": 0,
    }
    monkeypatch.setattr(inbox_routes, "_normalize_group_name", lambda _p, _m, g: g)
    monkeypatch.setattr(inbox_routes, "_normalize_doc_id", lambda _g, d: d)
    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: token)
    monkeypatch.setattr(inbox_routes, "has_permission", lambda *_a, **_k: True)
    monkeypatch.setattr(inbox_routes, "_is_valid_doc_type", lambda *_a, **_k: True)
    monkeypatch.setattr(inbox_routes.template_provision, "is_design_type", lambda _t: False)
    monkeypatch.setattr(inbox_routes, "_disposed_group_fail", lambda *_a, **_k: None)
    monkeypatch.setattr(
        inbox_routes, "_resolve_group", lambda *_a, **_k: {"group_id": "flowgate.default.0374"}
    )
    monkeypatch.setattr(
        inbox_routes.db_docs,
        "get_by_id",
        lambda doc_id: {"doc_id": doc_id, "doc_review_status": "pending_review"},
    )
    monkeypatch.setattr(inbox_routes, "_find_body_twin", lambda *_a, **_k: None)
    monkeypatch.setattr(inbox_routes.tr_scope_service, "evaluate", lambda **_k: None)
    get_head = MagicMock(return_value=head)
    monkeypatch.setattr(inbox_routes.db_wfseq, "get_pending_head_by_group", get_head)
    increment = MagicMock()
    monkeypatch.setattr(inbox_routes.token_service, "increment_dry_run", increment)
    return get_head, increment


@pytest.mark.parametrize("dry_run", [True, False])
def test_repeated_t_tr_head_rejects_tr_before_any_storage(monkeypatch, dry_run):
    """The NR0003 incident: a second T head cannot accept a stale TR identity."""
    from modules.flow_gate.api import inbox_routes

    _patch_validation(monkeypatch, {"id": 18, "type": "T"})
    reserve = MagicMock()
    create = MagicMock()
    consume = MagicMock()
    monkeypatch.setattr(inbox_routes.numbering_service, "reserve_document", reserve)
    monkeypatch.setattr(inbox_routes.db_docs, "create", create)
    monkeypatch.setattr(inbox_routes.token_service, "consume", consume)

    response = post_inbox(
        _body("tr", dry_run=dry_run)
    )
    payload = response.json()

    assert response.status_code == 409
    assert payload["ok"] is False
    assert "T" in payload["error_message"]
    assert "TR" in payload["error_message"]
    assert "doc_type" in payload["error_message"]
    reserve.assert_not_called()
    create.assert_not_called()
    consume.assert_not_called()
    inbox_routes.token_service.increment_dry_run.assert_not_called()


def test_matching_head_dry_run_passes_and_reports_executed_check(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    _patch_validation(monkeypatch, {"id": 18, "type": "T"})
    reserve = MagicMock()
    monkeypatch.setattr(inbox_routes.numbering_service, "reserve_document", reserve)

    response = post_inbox(_body("t", dry_run=True))
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert "workflow_head" in payload["would_register"]["checks_passed"]
    inbox_routes.token_service.increment_dry_run.assert_called_once_with("tok-0374")
    reserve.assert_not_called()


def test_matching_head_real_submit_reaches_step75_and_links_slot(monkeypatch, tmp_path):
    """A matching real submit retains the existing Step 7.5 registration behavior."""
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.workflow import pipeline_service

    _patch_validation(monkeypatch, {"id": 18, "type": "T"})
    stored = tmp_path / "T9001_document.md"
    create = MagicMock()
    consume = MagicMock()
    register = MagicMock()
    transition = MagicMock()
    monkeypatch.setattr(inbox_routes.numbering_service, "reserve_document", lambda **_k: "T9001")
    monkeypatch.setattr(inbox_routes, "_resolve_storage_path", lambda *_a, **_k: stored)
    monkeypatch.setattr(inbox_routes, "to_storage_relative", lambda *_a, **_k: "docs/T9001.md")
    monkeypatch.setattr(inbox_routes.db_projects, "get_settings", lambda _p: None)
    monkeypatch.setattr(inbox_routes.db_docs, "create", create)
    monkeypatch.setattr(inbox_routes.db_events, "create", lambda _event: None)
    monkeypatch.setattr(inbox_routes.token_service, "consume", consume)
    monkeypatch.setattr(pipeline_service, "register_workflow_result", register)
    monkeypatch.setattr(pipeline_service, "transition_document_review", transition)
    monkeypatch.setattr(inbox_routes, "_build_change_summary", lambda **_k: {"changed": True})
    monkeypatch.setattr(inbox_routes, "_continuation_self_chain", lambda *_a, **_k: None)

    response = post_inbox(_body("T"))

    assert response.status_code == 201, response.text
    create.assert_called_once()
    register.assert_called_once()
    assert register.call_args.kwargs["item_id"] == 18
    assert register.call_args.kwargs["registered_doc_id"].endswith(".T9001")
    transition.assert_called_once()
    consume.assert_called_once()
    assert stored.is_file()


@pytest.mark.parametrize("doc_type", ["M", "CH"])
def test_auto_complete_types_bypass_head_comparison(monkeypatch, doc_type):
    from modules.flow_gate.api import inbox_routes

    get_head, _increment = _patch_validation(monkeypatch, {"id": 18, "type": "T"})
    response = post_inbox(
        _body(doc_type, dry_run=True)
    )
    payload = response.json()

    assert response.status_code == 200
    assert "workflow_head" not in payload["would_register"]["checks_passed"]
    get_head.assert_not_called()


def test_group_without_workflow_head_keeps_legacy_creation_path(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    get_head, _increment = _patch_validation(monkeypatch, None)
    response = post_inbox(
        _body("NR", dry_run=True)
    )
    payload = response.json()

    assert response.status_code == 200
    assert "workflow_head" not in payload["would_register"]["checks_passed"]
    get_head.assert_called_once_with("flowgate.default.0374", "flowgate")