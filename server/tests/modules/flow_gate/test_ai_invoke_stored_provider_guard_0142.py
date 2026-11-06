"""0142 T#2: admission selection only; all I/O is replaced by memory doubles."""
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

from modules.flow_gate.services.ai_invoke import admission as adm
from modules.flow_gate.services import provider_capability_service
from modules.flow_gate.services.ai_invoke import runtime
from flowgate.ai_roles import invocations as journal

SOL, LUNA = "synthetic_sol_0142", "synthetic_luna_0142"
DOC = "flowgate.v02.0142.0007-T"
REASON = "stored_provider_not_selectable"


def provider(pid):
    return {"id": pid, "name": pid, "exec_type": "cli", "kind": "codex", "owner_scope": "system"}


class SelectionReady(BaseException):
    """Stop after the real run dict is built, before any registration or launch."""
    def __init__(self, run, chain):
        self.run = run
        self.chain = chain


@pytest.fixture
def env(monkeypatch):
    effective = {"providers": [provider(LUNA), provider(SOL)], "source": "system", "registered_count": 2}
    service = SimpleNamespace(
        stored_hop_provider=Mock(return_value=(SOL, "Sol", 2)),
        _resolve_continuation_hop_override=Mock(return_value=None),
        _resolve_continuation_hop_provider=Mock(return_value=None),
        _require_group_worktree=Mock(return_value=Path("unused-memory-root")),
        _ensure_initial_source_sync=Mock(), _next_run_id=Mock(return_value="memory-run-0142"),
        _create_scratch=Mock(return_value=Path("unused-memory-scratch")),
        _git_status_paths=Mock(return_value=set()), request_run_scratch_sweep=Mock(),
        _worker=Mock(), _runs={}, RUN_TIMEOUT_CAP_SEC=3600,
    )
    monkeypatch.setattr(adm, "_svc", lambda: service)
    monkeypatch.setattr(adm, "git_service", SimpleNamespace(get_branch_merge_group_claim=Mock(return_value=None)))
    monkeypatch.setattr(adm, "ai_settings_service", SimpleNamespace(
        resolve_effective=Mock(return_value=effective), personal_selectable=Mock(return_value=[])))
    monkeypatch.setattr(adm, "db_docs", SimpleNamespace(
        get_by_id=Mock(return_value={"doc_id": DOC, "type": "T", "branch": "main"}),
        get_group_max_seq=Mock(return_value=1)))
    leases = SimpleNamespace(get_active=Mock(return_value=None),
        acquire=Mock(return_value={"run_id": "memory-run-0142"}), activate=Mock(return_value={}))
    monkeypatch.setattr(adm, "db_group_ai_leases", leases)
    monkeypatch.setattr(adm.ai_run_identity, "claim", Mock())
    issue = Mock(return_value={"raw_token": "synthetic-token", "token_id": "synthetic-token",
        "scratch_dir": "unused-memory-scratch"})
    monkeypatch.setattr(adm, "token_service", SimpleNamespace(issue=issue))
    monkeypatch.setattr(adm, "storage_paths", SimpleNamespace(
        resolve_project_src_root=Mock(return_value=Path("unused-memory-root"))))
    monkeypatch.setattr(adm, "carryover", SimpleNamespace(bind_run_scratch=Mock()))
    monkeypatch.setattr(adm, "review", SimpleNamespace(
        resolve_review_count=Mock(return_value=0), _provider_name_of=Mock(return_value=None)))
    monkeypatch.setattr(adm, "_refuse_frozen_group", Mock())
    monkeypatch.setattr(adm, "_continuation_docs_target", Mock(return_value=1))
    monkeypatch.setattr(adm, "continuation_hop_item_seq", Mock(return_value=2))
    monkeypatch.setattr(adm, "_inject_hop_notes", lambda mention, *a, **kw: mention)
    monkeypatch.setattr(adm, "_chat_start_tree", Mock(return_value=None))
    monkeypatch.setattr(adm, "_chat_start_seq", Mock(return_value=None))
    monkeypatch.setattr(adm, "_absolute_cap_sec", lambda: 3600)
    monkeypatch.setattr(adm.oracle, "_uses_scope_oracle", Mock(return_value=False))
    monkeypatch.setattr(adm.graph_execution_hooks, "_handler", None)
    capability = Mock(return_value=None)
    monkeypatch.setattr(provider_capability_service, "capability_finding", capability)
    def capture(run, chain):
        raise SelectionReady(run, chain)
    monkeypatch.setattr(adm.chat_run_hooks, "chat_run_start", Mock())
    registration = Mock(side_effect=capture)
    monkeypatch.setattr(adm, "_register_run", registration)
    # Real journal construction, pure unit keys and refusal handling; memory-only store.
    rows, outputs = [], []
    store = journal.store
    monkeypatch.setattr(store, "available", Mock(return_value=True))
    monkeypatch.setattr(store, "max_create_seq", Mock(return_value=None))
    monkeypatch.setattr(store, "first", Mock(return_value=None))
    monkeypatch.setattr(store, "latest", Mock(return_value=None))
    monkeypatch.setattr(store, "issue_create_seq", lambda *floors: 1)
    monkeypatch.setattr(store, "write_record", Mock(side_effect=lambda row: rows.append(deepcopy(row)) or True))
    monkeypatch.setattr(store, "write_output", Mock(side_effect=lambda row: outputs.append(deepcopy(row)) or True))
    identity_marker = runtime.admission_identity.set(None)
    run_marker = runtime.admission_run_id.set(None)
    yield SimpleNamespace(service=service, effective=effective, leases=leases, issue=issue,
        capability=capability, rows=rows, outputs=outputs, store=store, registration=registration)
    runtime.admission_identity.reset(identity_marker)
    runtime.admission_run_id.reset(run_marker)


def start(**overrides):
    args = dict(project_id="flowgate", module="v02", group_id="flowgate.v02.0142", doc_ref=DOC,
        action_scope="new", mode="continuous", continuation_target_seq=2,
        continuation_review_mode=False, continuation_instruction_mode="auto_approved",
        continuation_locale="ko", issued_to="synthetic-user", api_base_url="http://127.0.0.1:1/flowgate/api/v1",
        mention_builder=lambda *args: "memory-only mention")
    args.update(overrides)
    return adm.start_run(**args)


def selected(**kwargs):
    with pytest.raises(SelectionReady) as result:
        start(**kwargs)
    return result.value


def assert_no_start(env):
    for spy in (env.leases.get_active, env.leases.acquire, env.leases.activate, env.issue,
                env.service._create_scratch, env.service._next_run_id,
                env.service._require_group_worktree, env.service._worker, env.capability,
                env.service._resolve_continuation_hop_provider, adm.ai_run_identity.claim, env.registration):
        spy.assert_not_called()
    assert env.service._runs == {}


def test_active_stored_sol_is_selected(env):
    selection = selected()
    run = selection.run
    assert [p["id"] for p in selection.chain] == [SOL]
    assert run["requested_provider_id"] == SOL
    assert run["continuation_selected_provider_id"] == SOL
    assert run["selected_provider_source"] == "stored_sequence"
    assert run["fallback_allowed"] is False
    assert env.capability.call_args.args[2]["id"] == SOL
    assert env.rows == []


@pytest.mark.parametrize("case", ["inactive", "missing", "request", "document_type", "empty_chain"])
@pytest.mark.parametrize("with_origin", [False, True], ids=["non_card", "card"])
def test_unavailable_stored_sol_refused_with_original_id(env, caplog, case, with_origin):
    env.effective["providers"] = [] if case == "empty_chain" else [provider(LUNA)]
    # Inactive and missing IDs are both represented by absence from the selectable list.
    origin = {"origin_kind": "card_assigned", "purpose": "result_write", "role_source": "card",
        "requested_ai_id": LUNA, "target": {"flow_id": "flowgate.v02.0142", "graph_run_id": "graph-memory",
        "node_key": "node-memory", "attempt_id": "attempt-memory", "stage": "result", "seq": 0, "doc_id": DOC}}
    before = deepcopy(origin)
    env.service._resolve_continuation_hop_provider.return_value = LUNA if case == "document_type" else None
    with caplog.at_level(logging.WARNING), pytest.raises(HTTPException) as caught:
        start(provider_id=LUNA if case == "request" else None, invocation_origin=origin if with_origin else None)
    exc = caught.value
    assert exc.status_code == 422
    assert exc.detail == {"code": adm.PROVIDER_UNAVAILABLE_CODE, "message": adm.PROVIDER_UNAVAILABLE_MESSAGE,
        "requested_provider_id": SOL, "stored_provider_id": SOL, "selected_provider_source": "stored_sequence",
        "doc_ref": DOC, "stored_provider_item_seq": 2, "reason": REASON}
    assert_no_start(env)
    diagnostics = [r for r in caplog.records if getattr(r, "reason", None) == REASON]
    assert len(diagnostics) == 1
    assert diagnostics[0].stored_provider_id == SOL
    assert diagnostics[0].doc_ref == DOC
    assert diagnostics[0].stored_provider_item_seq == 2
    assert diagnostics[0].selected_provider_source == "stored_sequence"
    assert len(env.rows) == len(env.outputs) == 1
    row = env.rows[0]
    assert row["requested_ai_id"] == SOL
    assert row["result"] == "admission_refused"
    assert row["result_detail"] == "provider_unavailable"
    assert all(row.get(k) is None for k in ("actual_ai_id", "ai_run_id", "launch_no", "hop_no", "started_at"))
    assert json.loads(env.outputs[0]["detail"]) == exc.detail
    assert exc._role_refusal_recorded is True
    assert origin == before
    if with_origin:
        assert row["origin_kind"] == "card_assigned"
        assert row["purpose"] == "result_write" and row["inv_role"] == "execution"
        assert row["role_source"] == "card"
        for column, key in journal.TARGET.items():
            assert row["target_" + column] == origin["target"].get(key)
    else:
        assert row["origin_kind"] == "non_card"
        assert row["purpose"] is None and row["target_attempt_id"] is None


@pytest.mark.parametrize("failure", ["disabled", "write_false", "write_exception"])
def test_journal_unavailable_still_refuses_sol(env, caplog, failure):
    env.effective["providers"] = [provider(LUNA)]
    if failure == "disabled":
        env.store.available.return_value = False
    elif failure == "write_false":
        env.store.write_record.side_effect = None
        env.store.write_record.return_value = False
    else:
        env.store.write_record.side_effect = RuntimeError("synthetic journal failure")
    with caplog.at_level(logging.WARNING), pytest.raises(HTTPException) as caught:
        start()
    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "provider_unavailable"
    assert caught.value.detail["requested_provider_id"] == SOL
    assert caught.value.detail["reason"] == REASON
    assert_no_start(env)
    assert env.rows == env.outputs == []
    assert any(getattr(r, "stored_provider_id", None) == SOL for r in caplog.records)
    assert env.store.write_record.call_count == (0 if failure == "disabled" else 1)


def test_no_assignment_keeps_default_chain(env):
    env.service.stored_hop_provider.return_value = (None, None, 2)
    selection = selected()
    run = selection.run
    assert [p["id"] for p in selection.chain] == [LUNA, SOL]
    assert run["requested_provider_id"] == LUNA
    assert run["selected_provider_source"] == "project_default"
    assert run["fallback_allowed"] is True
    assert [p["id"] for p in env.effective["providers"]] == [LUNA, SOL]
    env.service._resolve_continuation_hop_provider.assert_called_once()


def test_inactive_pinned_sol_keeps_existing_error(env):
    env.effective["providers"] = [provider(LUNA)]
    with pytest.raises(HTTPException) as caught:
        start(provider_id=SOL, provider_pinned=True)
    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "provider_unavailable"
    assert_no_start(env)


@pytest.mark.parametrize("higher", ["override", "pinned"])
def test_higher_selection_wins_over_unavailable_stored_sol(env, higher):
    env.effective["providers"] = [provider(LUNA)]
    if higher == "override":
        env.service._resolve_continuation_hop_override.return_value = LUNA
        selection = selected(continuation_provider_overrides={"2": LUNA})
    else:
        selection = selected(provider_id=LUNA, provider_pinned=True)
    run = selection.run
    assert [p["id"] for p in selection.chain] == [LUNA]
    assert run["requested_provider_id"] == LUNA
    assert run["selected_provider_source"] == ("step_override" if higher == "override" else "force_all")
    assert run["fallback_allowed"] is False
    env.service.stored_hop_provider.assert_not_called()
    assert env.rows == []
