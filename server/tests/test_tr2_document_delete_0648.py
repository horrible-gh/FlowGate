"""flowgate.default.0648 T2#3 — guarded TR2 document lifecycle and races."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException

from modules.flow_gate.documents import document_service
from modules.flow_gate.documents import tr2_approval_service as approval
from modules.flow_gate.documents import tr2_service as tr2
from modules.flow_gate.services import tr2_file_policy as policy
from modules.flow_gate.services import tr_commit_service as trc


class _Store:
    @contextmanager
    def transaction(self):
        yield self

    def _execute(self, *_args, **_kwargs):
        return None


def _doc():
    return {
        "id": 7,
        "doc_id": "flowgate.default.0648.0010-TR2",
        "type_code": "TR2",
        "project_id": "flowgate",
        "group_id": "flowgate.default.0648",
        "status": "open",
        "doc_review_status": "pending_review",
        "revision_no": 1,
    }


def _install_delete(monkeypatch, *, active=False, recovery=False):
    state = {"doc": _doc(), "deleted": 0, "events": 0}
    monkeypatch.setattr(document_service, "get_store", lambda: _Store())
    monkeypatch.setattr(
        document_service.db_docs, "get_by_id",
        lambda _id: dict(state["doc"]) if state["doc"] else None,
    )

    def delete(_id):
        state["deleted"] += 1
        state["doc"] = None

    monkeypatch.setattr(document_service.db_docs, "delete", delete)
    monkeypatch.setattr(
        document_service.db_events, "create",
        lambda _payload: state.__setitem__("events", state["events"] + 1),
    )
    monkeypatch.setattr(document_service.git_service, "_acquire_lock", lambda *_a, **_kw: True)
    monkeypatch.setattr(document_service.db_git, "release_lock", lambda *_a: None)
    monkeypatch.setattr(document_service.db_recovery, "has_unresolved", lambda _g: recovery)
    monkeypatch.setattr(
        document_service.tr2_file_policy, "has_active_source_effect",
        lambda _g, _d: active,
    )
    return state


def test_active_tr2_delete_is_409_and_non_destructive(monkeypatch):
    state = _install_delete(monkeypatch, active=True)
    with pytest.raises(HTTPException) as exc:
        document_service.delete_document(state["doc"]["doc_id"], "u")
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "TR2_ACTIVE_SOURCE_EFFECT"
    assert state["doc"] is not None
    assert state["deleted"] == 0
    assert state["events"] == 0


def test_canceled_inactive_tr2_delete_succeeds(monkeypatch):
    state = _install_delete(monkeypatch, active=False, recovery=False)
    document_service.delete_document(state["doc"]["doc_id"], "u")
    assert state["doc"] is None
    assert state["deleted"] == 1
    assert state["events"] == 1


def test_unresolved_recovery_blocks_delete(monkeypatch):
    state = _install_delete(monkeypatch, active=False, recovery=True)
    with pytest.raises(HTTPException) as exc:
        document_service.delete_document(state["doc"]["doc_id"], "u")
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "TR_HISTORY_RECOVERY_REQUIRED"
    assert state["deleted"] == 0


def _install_ownership(monkeypatch, rows, attempts):
    monkeypatch.setattr(policy.db_ledger, "ownership_rows", lambda _g: rows)
    monkeypatch.setattr(policy.db_attempts, "successful_by_group", lambda _g: attempts)


def test_document_active_effect_live_cancel_reapply_terminal(monkeypatch):
    attempt = {
        "attempt_id": "a", "ledger_row_id": 1,
        "commit_json": {"paths": ["src/a.py"]},
    }
    root = {
        "id": 1, "group_id": "g", "doc_id": "d", "state": "live",
        "restored_from_id": None, "reopened_terminal_at": None,
        "doc_type_code": "TR2",
    }
    _install_ownership(monkeypatch, [root], [attempt])
    assert policy.has_active_source_effect("g", "d") is True

    root["state"] = "canceled"
    assert policy.has_active_source_effect("g", "d") is False

    descendant = {
        "id": 2, "group_id": "g", "doc_id": "d", "state": "live",
        "restored_from_id": 1, "reopened_terminal_at": None,
        "doc_type_code": "TR2",
    }
    _install_ownership(monkeypatch, [root, descendant], [attempt])
    assert policy.has_active_source_effect("g", "d") is True

    terminal = {
        **root, "state": "live",
        "reopened_terminal_at": "2026-09-29T00:00:00+09:00",
    }
    _install_ownership(monkeypatch, [terminal], [attempt])
    assert policy.has_active_source_effect("g", "d") is True


def test_broken_active_document_lineage_fails_closed(monkeypatch):
    live = {
        "id": 2, "group_id": "g", "doc_id": "d", "state": "live",
        "restored_from_id": 1, "reopened_terminal_at": None,
        "doc_type_code": "TR2",
    }
    _install_ownership(monkeypatch, [live], [])
    with pytest.raises(policy.Tr2OwnershipInvariantError):
        policy.has_active_source_effect("g", "d")


def test_delete_first_makes_stale_reapply_a_noop(monkeypatch):
    row = {
        "id": 10, "group_id": "g", "doc_id": "d", "state": "canceled",
        "cancel_commit": "c" * 40, "commit_sha": "o" * 40,
        "commit_subject": "s", "newer_live": 0,
    }
    monkeypatch.setattr(trc.db_recovery, "has_unresolved", lambda _g: False)
    monkeypatch.setattr(trc.db_ledger, "reappliable_rows", lambda *_a: [row])
    monkeypatch.setattr(trc, "_doc_codes", lambda _rows: {"d": "0001-TR2"})
    monkeypatch.setattr(
        trc.git_service, "open_cancel_session",
        lambda *_a: {"ok": True, "session": {
            "project_id": "p", "group_id": "g", "holder": "h", "wt_path": Path("."),
        }},
    )
    monkeypatch.setattr(trc.git_service, "close_cancel_session", lambda _s: None)
    monkeypatch.setattr(trc.db_docs, "get_by_id", lambda _id: None)
    monkeypatch.setattr(trc.db_ledger, "get_by_id", lambda _id: None)
    git_calls = []
    monkeypatch.setattr(
        trc.git_service, "reapply_tr_commit",
        lambda *_a, **_kw: git_calls.append(True) or {"kind": "ok", "commit": "r" * 40},
    )

    result = trc.reapply_tr_commits("g", ["d"])

    assert git_calls == []
    assert result["reapplied"] == []
    assert result["stopped_reason"] == "stale_history_target"


def test_delete_first_makes_waiting_approval_fail_before_source_write(monkeypatch):
    initial = _doc()
    reads = iter([dict(initial), None])
    monkeypatch.setattr(approval.db_docs, "get_by_id", lambda _id: next(reads))
    monkeypatch.setattr(approval, "assert_group_mutation_allowed", lambda *_a: None)

    class Locked:
        project_id = "flowgate"
        group_id = "flowgate.default.0648"

    @contextmanager
    def source_lock(*_a, **_kw):
        yield Locked()

    monkeypatch.setattr(approval.tr2_precheck, "source_lock", source_lock)
    source_writes = []
    monkeypatch.setattr(
        approval.adapter, "apply_all",
        lambda *_a, **_kw: source_writes.append(True),
    )

    with pytest.raises(tr2.Tr2ValidationError) as exc:
        approval.approve(
            doc_id=initial["doc_id"],
            actor_user_id="u",
            user_permissions={"document.approve"},
            mutation_principal=object(),
        )

    assert exc.value.code == "tr2_workflow_conflict"
    assert source_writes == []


def test_reapply_or_approval_first_makes_delete_recheck_active(monkeypatch):
    state = _install_delete(monkeypatch, active=False)
    active = {"value": True}
    monkeypatch.setattr(
        document_service.tr2_file_policy, "has_active_source_effect",
        lambda _g, _d: active["value"],
    )

    with pytest.raises(HTTPException) as exc:
        document_service.delete_document(state["doc"]["doc_id"], "u")

    assert exc.value.detail["code"] == "TR2_ACTIVE_SOURCE_EFFECT"
    assert state["deleted"] == 0


def test_delete_mutex_order_is_initial_lock_fresh_recovery_active_delete_unlock(monkeypatch):
    events = []
    doc = _doc()
    reads = {"n": 0}
    monkeypatch.setattr(document_service, "get_store", lambda: _Store())

    def get(_id):
        reads["n"] += 1
        events.append("initial" if reads["n"] == 1 else "fresh")
        return dict(doc)

    monkeypatch.setattr(document_service.db_docs, "get_by_id", get)
    monkeypatch.setattr(
        document_service.git_service, "_acquire_lock",
        lambda *_a, **_kw: events.append("lock") or True,
    )
    monkeypatch.setattr(
        document_service.db_git, "release_lock",
        lambda *_a: events.append("unlock"),
    )
    monkeypatch.setattr(
        document_service.db_recovery, "has_unresolved",
        lambda _g: events.append("recovery") or False,
    )
    monkeypatch.setattr(
        document_service.tr2_file_policy, "has_active_source_effect",
        lambda *_a: events.append("active") or False,
    )
    monkeypatch.setattr(document_service.db_docs, "delete", lambda _id: events.append("delete"))
    monkeypatch.setattr(document_service.db_events, "create", lambda _p: None)

    document_service.delete_document(doc["doc_id"], "u")

    assert events == ["initial", "lock", "fresh", "recovery", "active", "delete", "unlock"]


def test_migration_125_durability_contract_remains_present():
    root = Path(__file__).resolve().parents[1] / "sql" / "migrations"
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (root / dialect / "125_tr2_attempt_durable_ownership.sql").read_text(encoding="utf-8")
        assert "ledger_row_id" in sql
        assert "ON DELETE SET NULL" in sql.upper()
