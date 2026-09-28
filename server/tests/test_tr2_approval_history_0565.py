"""Focused contracts for TR2 approval recovery and durable history."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from modules.flow_gate.documents import tr2_approval_service as approval
from modules.flow_gate.documents import tr2_history, tr2_service
from modules.flow_gate.services import git_service, tr_commit_service


def _spec():
    return {"termination": "ready_to_apply", "edits": [{"file": "a.txt"}],
            "gate": {"commands": ["pytest -q"]}}


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([], "tr2_validation_command_unapproved"),
        ([{"id": 1, "command": "pytest -q", "origin": "manual",
           "verified_os": "nt"}], "tr2_validation_command_os_mismatch"),
        ([{"id": 1, "command": "pytest -q", "origin": "auto",
           "verified_os": None}], "tr2_validation_command_unverified"),
    ],
)
def test_validation_registry_rejects_untrusted_commands(monkeypatch, rows, expected):
    monkeypatch.setattr(approval.db_commands, "list_active", lambda _project: rows)
    monkeypatch.setattr(approval.test_command_service, "normalize_command", lambda x: x)
    monkeypatch.setattr(approval.test_command_service, "current_os", lambda: "posix")
    with pytest.raises(tr2_service.Tr2ValidationError, match=expected):
        approval._check_commands({"project_id": "p"}, _spec())


def test_duplicate_registry_rows_use_most_restrictive_trust(monkeypatch):
    rows = [
        {"id": 1, "command": "pytest -q", "origin": "manual", "verified_os": None},
        {"id": 2, "command": "pytest -q", "origin": "auto", "verified_os": None},
    ]
    monkeypatch.setattr(approval.db_commands, "list_active", lambda _project: rows)
    monkeypatch.setattr(approval.test_command_service, "normalize_command", lambda x: x)
    monkeypatch.setattr(approval.test_command_service, "current_os", lambda: "posix")
    with pytest.raises(tr2_service.Tr2ValidationError, match="tr2_validation_command_unverified"):
        approval._check_commands({"project_id": "p"}, _spec())


def test_validation_timeout_is_recorded_before_rollback(monkeypatch, tmp_path):
    updates = []
    monkeypatch.setattr(approval.db_attempts, "update", lambda _id, **kw: updates.append(kw))
    monkeypatch.setattr(approval.process_runner, "run_command",
                        lambda *_args: (True, None, "timed out"))
    result = approval._run_validation(tmp_path, [{"command": "pytest -q", "registry_row_id": 1}], "a")
    assert result["status"] == "failed" and result["reason"] == "timeout"
    assert updates[-1]["validation_json"]["commands"][0]["timed_out"] is True


def test_restart_rollback_uses_journaled_spec_after_document_edit(monkeypatch, tmp_path):
    spec = _spec()
    row = {"attempt_id": "a", "tr2_doc_id": "d", "project_id": "p", "group_id": "g",
           "phase": "apply", "pre_apply_head_sha": "h", "backup_bundle_id": "b",
           "baseline_fingerprint": "base", "spec_fingerprint": "fingerprint",
           "precheck_json": json.dumps({"spec": spec})}
    locked = SimpleNamespace(root=tmp_path)
    restored = []
    finished = []
    monkeypatch.setattr(approval.db_attempts, "update", lambda _id, **kw: row.update(kw) or row)
    monkeypatch.setattr(approval.db_attempts, "by_id", lambda _id: row)
    monkeypatch.setattr(approval.db_attempts, "finish", lambda _id, **kw: finished.append(kw))
    monkeypatch.setattr(approval, "_head", lambda _root: "h")
    monkeypatch.setattr(approval, "_clean", lambda _root: True)
    monkeypatch.setattr(approval, "_git", lambda *_args: SimpleNamespace(returncode=0))
    monkeypatch.setattr(approval, "_backup_root", lambda _doc: tmp_path / "backups")
    monkeypatch.setattr(approval.db_docs, "get_by_id", lambda _id: {"doc_id": "d"})
    monkeypatch.setattr(approval.tr2, "spec_fingerprint", lambda _spec: "fingerprint")
    monkeypatch.setattr(approval.tr2_precheck, "restore_locked",
                        lambda _locked, old_spec, *_args, **_kw: restored.append(old_spec))
    monkeypatch.setattr(approval.tr2, "load_body",
                        lambda *_args: pytest.fail("current document body is not recovery authority"))
    approval._rollback(locked, row, None, RuntimeError("stale"))
    assert restored == [spec]
    assert finished[0]["state"] == "failed"
    assert finished[0]["result_code"] == "recovered_rollback"


def test_stale_success_requires_reachable_commit(monkeypatch, tmp_path):
    row = {"attempt_id": "a", "tr2_doc_id": "d", "group_id": "g",
           "phase": "finalize", "commit_sha": "sha", "ledger_row_id": 1,
           "document_revision": 0}
    monkeypatch.setattr(approval.db_docs, "get_by_id",
                        lambda _id: {"doc_review_status": "approved"})
    monkeypatch.setattr(approval.db_ledger, "get_by_id",
                        lambda _id: {"doc_id": "d", "group_id": "g", "commit_sha": "sha",
                                     "state": "live"})
    monkeypatch.setattr(approval, "_git", lambda *_args: SimpleNamespace(returncode=1))
    finished = []
    monkeypatch.setattr(approval.db_attempts, "finish", lambda _id, **kw: finished.append(kw))
    with pytest.raises(tr2_service.Tr2ValidationError, match="tr2_recovery_required"):
        approval._recover_stale(SimpleNamespace(root=tmp_path), row)
    assert finished[0]["state"] == "recovery_required"


def test_duplicate_success_replays_without_a_second_source_lock(monkeypatch):
    doc = {"doc_id": "d", "type_code": "TR2", "doc_review_status": "approved"}
    row = {"tr2_doc_id": "d", "state": "succeeded", "attempt_id": "a"}
    monkeypatch.setattr(approval, "in_transaction", lambda: False)
    monkeypatch.setattr(approval.db_docs, "get_by_id", lambda _id: doc)
    monkeypatch.setattr(approval, "check_permission", lambda *_args: True)
    monkeypatch.setattr(approval.db_attempts, "by_request_key", lambda _key: row)
    monkeypatch.setattr(approval.tr2_precheck, "source_lock",
                        lambda *_args, **_kw: pytest.fail("duplicate approval took source lock"))
    assert approval.approve(doc_id="d", actor_user_id="u",
                            user_permissions={"document.approve"},
                            mutation_principal=object(), request_key="same") is doc


def test_strong_approval_refuses_nested_transaction(monkeypatch):
    monkeypatch.setattr(approval, "in_transaction", lambda: True)
    with pytest.raises(tr2_service.Tr2ValidationError, match="tr2_nested_transaction"):
        approval.approve(doc_id="d", actor_user_id="u",
                         user_permissions={"document.approve"},
                         mutation_principal=object())


def test_restart_rollback_phase_never_reapplies(monkeypatch, tmp_path):
    row = {"attempt_id": "a", "tr2_doc_id": "d", "phase": "rollback"}
    called = []
    monkeypatch.setattr(approval, "_rollback",
                        lambda *_args: called.append("rollback"))
    monkeypatch.setattr(approval.adapter, "apply_all",
                        lambda *_args: pytest.fail("restart must not apply"))
    approval._recover_stale(SimpleNamespace(root=tmp_path), row)
    assert called == ["rollback"]


def test_uncertain_compensation_stays_recovery_required(monkeypatch, tmp_path):
    row = {"attempt_id": "a", "commit_sha": "sha", "pre_apply_head_sha": "parent"}
    states = []
    monkeypatch.setattr(approval.db_attempts, "update", lambda _id, **kw: row.update(kw) or row)
    monkeypatch.setattr(approval.db_attempts, "by_id", lambda _id: row)
    monkeypatch.setattr(approval.db_attempts, "finish", lambda _id, **kw: states.append(kw))
    monkeypatch.setattr(approval, "_compensate",
                        lambda *_args: approval._raise("tr2_recovery_required", "git_head"))
    with pytest.raises(tr2_service.Tr2ValidationError, match="tr2_recovery_required"):
        approval._rollback(SimpleNamespace(root=tmp_path), row, None, RuntimeError("db failed"))
    assert states[0]["state"] == "recovery_required"
    assert states[0]["error_code"] == "tr2_recovery_required"


def test_git_compensation_verifies_parent_and_original_baseline(monkeypatch, tmp_path):
    spec = _spec()
    row = {"commit_sha": "sha", "pre_apply_head_sha": "parent",
           "precheck_json": json.dumps({"spec": spec}),
           "spec_fingerprint": "fingerprint", "baseline_fingerprint": "baseline"}
    heads = iter(["sha", "parent"])
    commands = []
    monkeypatch.setattr(approval, "_head", lambda _root: next(heads))
    monkeypatch.setattr(approval, "_clean", lambda _root: True)
    def git(_root, args):
        commands.append(args)
        return SimpleNamespace(returncode=0, stdout="parent" if args[0] == "rev-parse" else "")
    monkeypatch.setattr(approval, "_git", git)
    monkeypatch.setattr(approval.tr2, "spec_fingerprint", lambda _spec: "fingerprint")
    monkeypatch.setattr(approval.tr2, "target_fingerprint",
                        lambda saved, _root: "baseline" if saved == spec else "wrong")
    approval._compensate(SimpleNamespace(root=tmp_path), row, None)
    assert ["reset", "--hard", "parent"] in commands


def test_source_history_distinguishes_restore_pending_and_conflict(monkeypatch, tmp_path):
    doc = {"doc_id": "d", "group_id": "g", "project_id": "p", "type_code": "TR2",
           "doc_review_status": "approved"}
    row = {"id": 1, "doc_id": "d", "state": "canceled", "commit_sha": "sha",
           "cancel_commit": "undo", "restored_from_id": None}
    monkeypatch.setattr(tr2_history.db_docs, "get_by_id", lambda _id: doc)
    monkeypatch.setattr(tr2_history.db_ledger, "list_by_group", lambda *_args, **_kw: [row])
    monkeypatch.setattr(tr2_history.db_ledger, "get_by_id", lambda _id: row)
    monkeypatch.setattr(tr2_history.db_attempts, "successful_root",
                        lambda _id: {"commit_sha": "sha", "spec_fingerprint": "s",
                                     "baseline_fingerprint": "b"})
    monkeypatch.setattr(tr2_history.tr_commit_service, "conflict_session", lambda _g: None)
    monkeypatch.setattr(tr2_service, "resolve_source_root", lambda *_args: tmp_path)
    monkeypatch.setattr(git_service, "_run_git",
                        lambda *_args, **_kw: SimpleNamespace(returncode=0))
    assert tr2_history.source_history_state("d") == "restore_pending"
    monkeypatch.setattr(git_service, "_run_git",
                        lambda *_args, **_kw: SimpleNamespace(returncode=1))
    assert tr2_history.source_history_state("d") == "invariant_error"
    monkeypatch.setattr(git_service, "_run_git",
                        lambda *_args, **_kw: SimpleNamespace(returncode=0))
    monkeypatch.setattr(tr2_history.tr_commit_service, "conflict_session",
                        lambda _g: {"doc_id": "d"})
    assert tr2_history.source_history_state("d") == "conflict"
    monkeypatch.setattr(tr2_history.tr_commit_service, "conflict_session", lambda _g: None)
    row["state"] = "live"
    row["cancel_commit"] = None
    assert tr2_history.source_history_state("d") == "aligned"


def test_time_machine_uses_one_mode_for_mixed_batch(monkeypatch):
    ids = ["t", "r", "r2"]
    docs = {"t": {"type_code": "T"}, "r": {"type_code": "TR"},
            "r2": {"type_code": "TR2"}}
    monkeypatch.setattr(tr_commit_service.db_docs, "get_documents_by_ids",
                        lambda _ids: {i: docs[i] for i in _ids})
    rows = [{"id": 1, "doc_id": "r", "commit_sha": "a"},
            {"id": 2, "doc_id": "r2", "commit_sha": "b"}]
    monkeypatch.setattr(tr_commit_service.db_ledger, "live_rows", lambda *_args: rows)
    monkeypatch.setattr(tr_commit_service, "_doc_codes", lambda _rows: {"r": "0001-TR", "r2": "0002-TR2"})
    monkeypatch.setattr(tr_commit_service.git_service, "open_cancel_session",
                        lambda *_args: {"ok": True, "session": {}})
    monkeypatch.setattr(tr_commit_service.git_service, "close_cancel_session", lambda _s: None)
    observed = []
    def durable(_group, reopened, **kw):
        observed.append((list(reopened), kw["opened_session"]))
        return tr_commit_service.empty_cancel_result()
    monkeypatch.setattr(tr_commit_service, "cancel_tr_commits", durable)
    mixed = tr_commit_service.cancel_for_reopen("g", ids)
    assert mixed["history_mode"] == "durable_revert"
    assert observed == [(ids, {})]


def test_pure_tr_time_machine_retains_legacy_uncommit(monkeypatch):
    row = {"id": 1, "doc_id": "r", "commit_sha": "a"}
    monkeypatch.setattr(tr_commit_service.db_docs, "get_documents_by_ids",
                        lambda _ids: {"r": {"type_code": "TR"}})
    monkeypatch.setattr(tr_commit_service.db_ledger, "live_rows", lambda *_args: [row])
    monkeypatch.setattr(tr_commit_service.db_ledger, "mark_canceled", lambda *_args, **_kw: True)
    monkeypatch.setattr(tr_commit_service, "_doc_codes", lambda _rows: {"r": "0001-TR"})
    monkeypatch.setattr(tr_commit_service.git_service, "open_cancel_session",
                        lambda *_args: {"ok": True, "session": {}})
    monkeypatch.setattr(tr_commit_service.git_service, "uncommit_tr_suffix",
                        lambda *_args: {"kind": "ok"})
    monkeypatch.setattr(tr_commit_service.git_service, "close_cancel_session", lambda _s: None)
    result = tr_commit_service.cancel_for_reopen("g", ["r"])
    assert result["history_mode"] == "legacy_uncommit"
    assert len(result["canceled"]) == 1
