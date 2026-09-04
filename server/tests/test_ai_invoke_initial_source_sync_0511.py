"""Initial AI source-access worktree sync — invocation trigger (flowgate.default.0511 T0004).

The trigger judgement lives in ai_invoke_service: the group worktree must be
forced to the current base HEAD exactly once, right before the group's FIRST
raw source-capable (tool_registry.kind_for_token() in {read, read_write}) AI
invocation — never gated on resolve_registry()'s source_mode-adjusted
advertising value, which never gates permission (NR0003 v5 §3-9).

git_service.ensure_initial_group_source_sync() itself (the actual reset/clean/
verify/marker-persist sequence) is covered by test_git_initial_source_sync_0511.py
against real git repositories — these tests only prove the JUDGEMENT: which raw
kinds trigger a sync call, and which outcomes block versus pass through.
"""
from __future__ import annotations

import re

import pytest
from fastapi import HTTPException

from modules.flow_gate.services import ai_invoke_service as aiv


# ── _worker_source_kind: raw kind, independent of source_mode ────────────────

@pytest.mark.parametrize(
    "action_scope,expected",
    [
        ("review", "read"),
        ("workflow_decide", "read"),
        ("chat", "read"),
        ("resolve_conflict", "read"),
        ("resolve_base_dirty", "read_write"),
        ("test_run", "none"),
        ("sequence_edit", "none"),
    ],
)
def test_worker_source_kind_scope_only_branches(action_scope, expected):
    """None of these scopes needs a doc_ref lookup (kind_for_step's own early
    branches), so this also proves the judgement never touches source_mode —
    nothing here is mocked, and resolve_registry is never imported."""
    kind = aiv._worker_source_kind({"action_scope": action_scope, "doc_ref": "irrelevant"})
    assert kind == expected


def test_worker_source_kind_new_edit_delegates_to_step_type_lookup(monkeypatch):
    """new/edit needs the workflow step type — proven by observing the lookup is
    actually consulted, not assumed."""
    from modules.flow_gate.services import remote_tool_service

    monkeypatch.setattr(
        remote_tool_service, "_worker_token_step_type_result",
        lambda token_rec: ("TR", False),
    )
    assert aiv._worker_source_kind({"action_scope": "new", "doc_ref": "d"}) == "read_write"

    monkeypatch.setattr(
        remote_tool_service, "_worker_token_step_type_result",
        lambda token_rec: ("N", False),
    )
    assert aiv._worker_source_kind({"action_scope": "new", "doc_ref": "d"}) == "read"


# ── _ensure_initial_source_sync: trigger + block/pass judgement ──────────────

def test_none_kind_never_calls_the_sync(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("must not be called for a none-kind scope")

    monkeypatch.setattr(aiv.git_service, "ensure_initial_group_source_sync", _boom)
    aiv._ensure_initial_source_sync("p", "default", "g", "test_run", "d")  # no raise


@pytest.mark.parametrize("action_scope", ["review", "resolve_base_dirty"])
def test_read_and_read_write_kinds_call_the_sync(monkeypatch, action_scope):
    captured = {}

    def _fake(project_id, module, group_id):
        captured["args"] = (project_id, module, group_id)
        return {"performed": True, "reason": "ok", "sha": "abc123"}

    monkeypatch.setattr(aiv.git_service, "ensure_initial_group_source_sync", _fake)
    aiv._ensure_initial_source_sync("p", "default", "g", action_scope, "d")
    assert captured["args"] == ("p", "default", "g")


@pytest.mark.parametrize(
    "reason", ["git_disabled", "already_synced", "legacy_source_history"],
)
def test_safe_skip_reasons_never_block(monkeypatch, reason):
    monkeypatch.setattr(
        aiv.git_service, "ensure_initial_group_source_sync",
        lambda *_a: {"performed": False, "reason": reason, "sha": None},
    )
    aiv._ensure_initial_source_sync("p", "default", "g", "review", "d")  # no raise


@pytest.mark.parametrize(
    "reason",
    [
        "reset_failed", "head_mismatch", "marker_persist_failed",
        "worktree_missing", "git_busy", "project_name_missing", "error",
    ],
)
def test_unsafe_reasons_block_the_run(monkeypatch, reason):
    monkeypatch.setattr(
        aiv.git_service, "ensure_initial_group_source_sync",
        lambda *_a: {"performed": False, "reason": reason, "sha": None},
    )
    with pytest.raises(HTTPException) as exc:
        aiv._ensure_initial_source_sync("p", "default", "g", "review", "d")
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "initial_source_sync_failed"
    assert exc.value.detail["reason"] == reason
    assert exc.value.detail["group_id"] == "g"


@pytest.mark.parametrize(
    "locale,snippet",
    [("en", "was not started"), ("ja", "開始しません")],
)
def test_block_message_is_localized_and_has_no_korean(monkeypatch, locale, snippet):
    monkeypatch.setattr(
        aiv.git_service, "ensure_initial_group_source_sync",
        lambda *_a: {"performed": False, "reason": "reset_failed", "sha": None},
    )
    with pytest.raises(HTTPException) as exc:
        aiv._ensure_initial_source_sync("p", "default", "g", "review", "d", locale=locale)
    message = exc.value.detail["message"]
    assert not re.search(r"[가-힣]", message)
    assert snippet in message
    assert "reset_failed" in message


def test_ko_default_preserves_existing_meaning(monkeypatch):
    monkeypatch.setattr(
        aiv.git_service, "ensure_initial_group_source_sync",
        lambda *_a: {"performed": False, "reason": "reset_failed", "sha": None},
    )
    with pytest.raises(HTTPException) as exc:
        aiv._ensure_initial_source_sync("p", "default", "g", "review", "d")
    assert "동기화하지 못해" in exc.value.detail["message"]


# ── start_run wiring ──────────────────────────────────────────────────────────

def test_start_run_calls_sync_after_the_worktree_gate_before_admission(monkeypatch):
    """Pins both the ORDER (after _require_group_worktree, before lease/token
    admission — T0004 §9/§34: nothing must be created yet when this can still
    fail) and that action_scope/doc_ref/locale are forwarded correctly."""
    captured = {}

    monkeypatch.setattr(aiv, "_require_group_worktree", lambda *a, **k: None)

    def _fake_sync(project_id, module, group_id, action_scope, doc_ref, locale=None):
        captured["args"] = (project_id, module, group_id, action_scope, doc_ref, locale)
        raise HTTPException(
            status_code=409,
            detail={"code": "initial_source_sync_failed", "message": "stub"},
        )

    monkeypatch.setattr(aiv, "_ensure_initial_source_sync", _fake_sync)
    monkeypatch.setattr(
        aiv.ai_settings_service, "resolve_effective",
        lambda _pid: {"providers": [{"id": "prov1"}], "source": "configured", "registered_count": 1},
    )
    monkeypatch.setattr(aiv.db_group_ai_leases, "get_active", lambda _gid: None)
    monkeypatch.setattr(aiv.db_docs, "get_by_id", lambda _doc_ref: {"branch": "main"})

    # A spy proving admission (lease acquisition) never runs once the sync blocks.
    def _lease_boom(*_a, **_k):
        raise AssertionError("lease admission must not run after a blocked sync")

    monkeypatch.setattr(aiv.db_group_ai_leases, "acquire", _lease_boom)

    with pytest.raises(HTTPException) as exc:
        aiv.start_run(
            project_id="p", module="default", group_id="g", doc_ref="d",
            action_scope="new", mode="single",
            continuation_target_seq=None, continuation_review_mode=False,
            continuation_instruction_mode=None, continuation_locale="ja",
            issued_to="worker", api_base_url="http://x",
            mention_builder=lambda *_a, **_k: None,
        )

    assert exc.value.detail["code"] == "initial_source_sync_failed"
    assert captured["args"] == ("p", "default", "g", "new", "d", "ja")
