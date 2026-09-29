"""flowgate.default.0648 T2#2 — durable reapply recovery and fail-closed lifecycle."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from modules.flow_gate.api.v1 import git_routes
from modules.flow_gate.db import tr_history_recovery as recovery
from modules.flow_gate.documents import tr2_approval_service as approval
from modules.flow_gate.documents import tr2_service as tr2
from modules.flow_gate.services import tr2_file_policy as policy
from modules.flow_gate.services import tr_commit_service as trc


def test_recovery_migrations_define_durable_state_machine():
    root = Path(__file__).resolve().parents[1] / "sql" / "migrations"
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (root / dialect / "126_tr_history_recovery.sql").read_text(encoding="utf-8")
        for token in (
            "tr_history_recovery", "prepared", "git_applied",
            "recovery_required", "resolved", "ledger_completed", "compensated",
            "pre_git_head_sha", "git_commit_sha", "source_ledger_row_id",
        ):
            assert token in sql


def test_unresolved_journal_is_group_scoped(monkeypatch):
    seen = {}

    class Store:
        def _fetch_one(self, sql, params):
            seen["sql"] = sql
            seen["params"] = params
            return {"id": 1}

    monkeypatch.setattr(recovery, "get_store", lambda: Store())
    assert recovery.has_unresolved("flowgate.default.0648") is True
    assert "phase IN ('prepared','git_applied','recovery_required')" in seen["sql"]
    assert seen["params"] == ["flowgate.default.0648"]


def test_history_recovery_block_does_not_write_legacy_group_git_state(monkeypatch):
    called = []
    monkeypatch.setattr(
        trc.db_ledger, "record_block",
        lambda *args, **kwargs: called.append((args, kwargs)),
    )
    result = trc._blocked(
        trc.empty_restore_result(), "flowgate.default.0648",
        "history_recovery_required", "recovery_required",
    )
    assert result["blocked_reason"] == "history_recovery_required"
    assert called == []


def _row():
    return {
        "id": 10, "group_id": "g", "doc_id": "d", "state": "canceled",
        "cancel_commit": "c" * 40, "commit_sha": "o" * 40, "commit_subject": "subject",
        "newer_live": 0,
    }


def _install_reapply_harness(monkeypatch, *, compensate_kind="ok"):
    row = _row()
    monkeypatch.setattr(trc.db_recovery, "has_unresolved", lambda _g: False)
    monkeypatch.setattr(trc.db_recovery, "unresolved_by_group", lambda _g: [])
    monkeypatch.setattr(trc.db_ledger, "reappliable_rows", lambda _g, _ids: [row])
    monkeypatch.setattr(trc, "_doc_codes", lambda _rows: {"d": "0001-TR2"})
    monkeypatch.setattr(
        trc.git_service, "open_cancel_session",
        lambda *_a: {"ok": True, "session": {
            "project_id": "p", "group_id": "g", "holder": "h",
            "wt_path": Path("."), "author_env": None,
        }},
    )
    monkeypatch.setattr(trc.git_service, "close_cancel_session", lambda _s: None)
    heads = iter(["a" * 40, "r" * 40])
    monkeypatch.setattr(trc, "_session_head", lambda _s: next(heads))
    journal = {
        "recovery_id": "j1", "project_id": "p", "group_id": "g", "doc_id": "d",
        "source_ledger_row_id": 10, "phase": "prepared", "pre_git_head_sha": "a" * 40,
    }
    monkeypatch.setattr(trc.db_recovery, "create_prepared", lambda **_kw: dict(journal))
    monkeypatch.setattr(
        trc.git_service, "reapply_tr_commit",
        lambda *_a, **_kw: {"kind": "ok", "commit": "r" * 40, "sub": None},
    )
    monkeypatch.setattr(trc.db_recovery, "mark_git_applied", lambda _rid, sha: {
        **journal, "phase": "git_applied", "git_commit_sha": sha,
    })
    monkeypatch.setattr(trc.db_recovery, "by_recovery_id", lambda _rid: {
        **journal, "phase": "git_applied", "git_commit_sha": "r" * 40,
    })
    monkeypatch.setattr(
        trc, "_finalize_reapply",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("ledger boom")),
    )
    monkeypatch.setattr(
        trc, "_compensate_reapply",
        lambda *_a, **_kw: compensate_kind == "ok",
    )
    return row


def test_record_reapply_failure_compensation_success_does_not_claim_reapplied(monkeypatch):
    _install_reapply_harness(monkeypatch, compensate_kind="ok")
    marked = []
    monkeypatch.setattr(trc, "_require_recovery", lambda *_a: marked.append(True))

    result = trc.reapply_tr_commits("g", ["d"])

    assert result["reapplied"] == []
    assert result["skipped"][0]["reason"] == "compensated"
    assert result["stopped_reason"] == "recovery_compensated"
    assert result["blocked_reason"] is None
    assert marked == []


def test_record_reapply_failure_compensation_failure_requires_recovery(monkeypatch):
    _install_reapply_harness(monkeypatch, compensate_kind="blocked")
    marked = []
    monkeypatch.setattr(
        trc, "_require_recovery",
        lambda journal, error: marked.append(journal["recovery_id"]),
    )

    result = trc.reapply_tr_commits("g", ["d"])

    assert result["reapplied"] == []
    assert result["blocked_reason"] == "history_recovery_required"
    assert result["stopped_reason"] == "history_recovery_required"
    assert marked == ["j1"]


def _recovery_journal(*, phase, pre="a" * 40, git_sha=None):
    return {
        "recovery_id": "j1", "project_id": "p", "group_id": "g", "doc_id": "d",
        "source_ledger_row_id": 10, "phase": phase,
        "pre_git_head_sha": pre, "git_commit_sha": git_sha,
    }


def _install_recovery_session(monkeypatch, journal, *, head):
    source = _row()
    state = {"resolved": False}
    monkeypatch.setattr(
        trc.db_recovery, "unresolved_by_group",
        lambda _g: [] if state["resolved"] else [journal],
    )
    monkeypatch.setattr(trc.db_ledger, "get_by_id", lambda _i: source)
    monkeypatch.setattr(
        trc.git_service, "open_cancel_session",
        lambda *_a: {"ok": True, "session": {
            "wt_path": Path("."), "project_id": "p", "group_id": "g", "holder": "h",
        }},
    )
    monkeypatch.setattr(trc.git_service, "close_cancel_session", lambda _s: None)
    monkeypatch.setattr(trc, "_session_head", lambda _s: head)
    return source, state


def test_prepared_journal_process_interruption_recovers_inactive(monkeypatch):
    journal = _recovery_journal(phase="prepared")
    _source, state = _install_recovery_session(monkeypatch, journal, head="a" * 40)
    resolved = []

    def resolve(rid, *, resolution, error_detail=None):
        state["resolved"] = True
        resolved.append((rid, resolution))
        return {**journal, "phase": "resolved", "resolution": resolution}

    monkeypatch.setattr(trc.db_recovery, "resolve", resolve)

    outcome = trc.recover_tr_history("g")

    assert outcome["status"] == "clear"
    assert resolved == [("j1", "compensated")]


def test_git_applied_process_interruption_completes_missing_ledger(monkeypatch):
    journal = _recovery_journal(phase="git_applied", git_sha="r" * 40)
    source, state = _install_recovery_session(monkeypatch, journal, head="r" * 40)
    finalized = []

    def finalize(j, row, sha):
        assert j["phase"] == "git_applied"
        assert row is source
        assert sha == "r" * 40
        state["resolved"] = True
        finalized.append((j["recovery_id"], sha))
        return {"id": 20, "state": "live", "commit_sha": sha}

    monkeypatch.setattr(trc, "_finalize_reapply", finalize)

    outcome = trc.recover_tr_history("g")

    assert outcome["status"] == "clear"
    assert outcome["resolved"] == [{"recovery_id": "j1", "resolution": "ledger_completed"}]
    assert finalized == [("j1", "r" * 40)]


def test_git_applied_recovery_compensates_when_ledger_completion_fails(monkeypatch):
    journal = _recovery_journal(phase="git_applied", git_sha="r" * 40)
    _source, state = _install_recovery_session(monkeypatch, journal, head="r" * 40)
    monkeypatch.setattr(
        trc, "_finalize_reapply",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("cannot finalize ledger")),
    )

    def compensate(_session, j, sha, _error):
        assert j["recovery_id"] == "j1"
        assert sha == "r" * 40
        state["resolved"] = True
        return True

    monkeypatch.setattr(trc, "_compensate_reapply", compensate)

    outcome = trc.recover_tr_history("g")

    assert outcome["status"] == "clear"
    assert outcome["resolved"] == [{"recovery_id": "j1", "resolution": "compensated"}]


def test_recovery_required_blocks_ordinary_source_mutation(monkeypatch, tmp_path):
    root = tmp_path / "worktree"
    root.mkdir()
    (root / "a.py").write_text("x", encoding="utf-8")
    monkeypatch.setattr(policy.git_service, "_acquire_lock", lambda *_a, **_kw: True)
    monkeypatch.setattr(policy.db_git, "release_lock", lambda *_a: None)
    monkeypatch.setattr(policy, "_group_root", lambda *_a: root)
    monkeypatch.setattr(policy.db_recovery, "has_unresolved", lambda _g: True)

    with pytest.raises(policy.Tr2FilePolicyError) as exc:
        with policy.general_source_mutation("p", "g", exact_paths=["a.py"]):
            pass

    assert exc.value.code == policy.TR_HISTORY_RECOVERY_REQUIRED


def test_recovery_required_blocks_tr2_approval(monkeypatch):
    doc = {
        "doc_id": "flowgate.default.0648.0008-TR2", "type_code": "TR2",
        "project_id": "p", "group_id": "g", "doc_review_status": "pending_review",
        "revision_no": 1,
    }
    monkeypatch.setattr(approval.db_docs, "get_by_id", lambda _doc_id: dict(doc))
    monkeypatch.setattr(approval, "assert_group_mutation_allowed", lambda *_a: None)
    monkeypatch.setattr(approval.db_recovery, "has_unresolved", lambda _g: True)

    @contextmanager
    def source_lock(*_a, **_kw):
        yield object()

    monkeypatch.setattr(approval.tr2_precheck, "source_lock", source_lock)

    with pytest.raises(tr2.Tr2ValidationError) as exc:
        approval.approve(
            doc_id=doc["doc_id"], actor_user_id="u",
            user_permissions={"document.approve"}, mutation_principal=object(),
        )

    assert exc.value.code == "tr_history_recovery_required"


def test_recovery_required_blocks_additional_time_machine_history_mutation(monkeypatch):
    target = {
        "id": 1, "group_id": "g", "doc_id": "d", "commit_sha": "a" * 40,
        "commit_subject": "s", "state": "live",
    }
    monkeypatch.setattr(trc.db_ledger, "live_rows", lambda *_a: [target])
    monkeypatch.setattr(trc, "_doc_codes", lambda _rows: {"d": "0001-TR2"})
    monkeypatch.setattr(
        trc.git_service, "open_cancel_session",
        lambda *_a: {"ok": True, "session": {
            "project_id": "p", "group_id": "g", "holder": "h", "wt_path": Path("."),
        }},
    )
    monkeypatch.setattr(trc.git_service, "close_cancel_session", lambda _s: None)
    monkeypatch.setattr(trc.db_recovery, "has_unresolved", lambda _g: True)
    reverted = []
    monkeypatch.setattr(
        trc.git_service, "revert_tr_commit",
        lambda *_a, **_kw: reverted.append(True) or {"kind": "ok", "commit": "b" * 40},
    )

    result = trc.cancel_tr_commits("g", ["d"])

    assert result["blocked_reason"] == "history_recovery_required"
    assert reverted == []


def test_unresolved_recovery_group_tree_never_degrades_to_empty_managed_set(monkeypatch):
    monkeypatch.setattr(
        git_routes.git_service, "read_group_tree",
        lambda *_a: {"ok": True, "data": {
            "group_id": "g", "branch": "b", "commit": "a" * 40, "nodes": [],
        }},
    )
    monkeypatch.setattr(policy.db_recovery, "has_unresolved", lambda _g: True)

    with pytest.raises(policy.Tr2FilePolicyError) as exc:
        git_routes.get_group_branch_tree("p", "g", user={})

    assert exc.value.code == policy.TR_HISTORY_RECOVERY_REQUIRED


def _ownership_rows(state, *, terminal=False, restored_from_id=None, row_id=1):
    return [{
        "id": row_id, "group_id": "g", "doc_id": "tr2", "state": state,
        "restored_from_id": restored_from_id,
        "reopened_terminal_at": "2026-09-29T00:00:00+09:00" if terminal else None,
        "doc_type_code": "TR2",
    }]


def test_cancel_then_failed_new_tr2_keeps_ordinary_recovery_path_open(monkeypatch, tmp_path):
    root = tmp_path / "worktree"
    root.mkdir()
    (root / "a.py").write_text("old", encoding="utf-8")
    attempts = [{
        "attempt_id": "old-success", "ledger_row_id": 1,
        "commit_json": {"paths": ["a.py"]},
    }]
    # The new TR2 failure has no live ledger contribution. Durable old provenance
    # remains, but the old lineage is canceled and therefore inactive.
    rows = _ownership_rows("canceled")
    monkeypatch.setattr(policy.db_recovery, "has_unresolved", lambda _g: False)
    monkeypatch.setattr(policy.db_attempts, "successful_by_group", lambda _g: attempts)
    monkeypatch.setattr(policy.db_ledger, "ownership_rows", lambda _g: rows)
    assert policy.managed_paths("g") == set()

    monkeypatch.setattr(policy.git_service, "_acquire_lock", lambda *_a, **_kw: True)
    monkeypatch.setattr(policy.db_git, "release_lock", lambda *_a: None)
    monkeypatch.setattr(policy, "_group_root", lambda *_a: root)
    with policy.general_source_mutation("p", "g", exact_paths=["a.py"]):
        (root / "a.py").write_text("human recovery", encoding="utf-8")
    assert (root / "a.py").read_text(encoding="utf-8") == "human recovery"


def test_terminal_reopen_remains_managed_under_server_policy(monkeypatch):
    monkeypatch.setattr(policy.db_recovery, "has_unresolved", lambda _g: False)
    monkeypatch.setattr(
        policy.db_attempts, "successful_by_group",
        lambda _g: [{
            "attempt_id": "a", "ledger_row_id": 1,
            "commit_json": {"paths": ["server/a.py"]},
        }],
    )
    monkeypatch.setattr(
        policy.db_ledger, "ownership_rows",
        lambda _g: _ownership_rows("live", terminal=True),
    )
    assert policy.managed_paths("g") == {"server/a.py"}
