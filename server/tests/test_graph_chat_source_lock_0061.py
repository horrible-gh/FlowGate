"""flowgate.v02.0061 T#14 (WP answer 2): a chat's edit / edit-once source access is refused while the Flow's Graph run
is running, paused or stopping (409 ``graph_run_locked``); a read chat and an idle / absent run are not.

The judgement is ``flowgate.graph_run.editing.chat_source_refusal`` (registered as the ``chat_source_refusal`` Graph
execution hook); ``token_service.issue`` asks it for a chat token only when the token would carry edit access, before
the token row is written or an edit-once claim is taken. No database: the run read and the token storage are stubbed.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException

from flowgate.graph_run import editing
from modules.flow_gate.graph_workflow.api import graph_messages
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services import graph_execution_hooks, token_scratch, token_service

FLOW = "flowgate.v02.0061"
PROJECT = "project-0061"


def _run(status):
    return lambda flow_id: {"run_id": "run_0061", "group_id": flow_id, "status": status}


# ── the judgement ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["running", "paused", "stopping"])
@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_a_locked_run_refuses_the_edit_source_access(monkeypatch, status, locale):
    monkeypatch.setattr(run_store, "active_run", _run(status))
    with pytest.raises(HTTPException) as refused:
        editing.chat_source_refusal(FLOW, locale)
    assert refused.value.status_code == 409
    detail = refused.value.detail
    assert detail["code"] == "graph_run_locked"
    assert detail["message"] == graph_messages.CHAT_SOURCE_LOCKED[locale]
    assert (detail["flow_id"], detail["run_id"], detail["status"]) == (FLOW, "run_0061", status)


@pytest.mark.parametrize("run", [None, {"run_id": "run_0061", "status": "idle"}])
def test_an_idle_or_absent_run_lets_it_go(monkeypatch, run):
    monkeypatch.setattr(run_store, "active_run", lambda _flow_id: run)
    assert editing.chat_source_refusal(FLOW, "ko") is None


def test_a_chat_without_a_group_is_not_judged(monkeypatch):
    monkeypatch.setattr(run_store, "active_run", lambda _flow_id: pytest.fail("no run read without a group"))
    assert editing.chat_source_refusal(None) is None


def test_an_unknown_locale_falls_back_to_korean(monkeypatch):
    monkeypatch.setattr(run_store, "active_run", _run("running"))
    with pytest.raises(HTTPException) as refused:
        editing.chat_source_refusal(FLOW, "fr")
    assert refused.value.detail["message"] == graph_messages.CHAT_SOURCE_LOCKED["ko"]


def test_the_judgement_is_the_registered_hook():
    from flowgate.graph_run import execution
    assert execution._ACTIONS["chat_source_refusal"] is editing.chat_source_refusal


# ── where token_service asks it ──────────────────────────────────────────────────────────────

class _Store:
    @contextmanager
    def transaction(self):
        yield


@pytest.fixture
def issuing(monkeypatch, tmp_path):
    """token_service.issue with its storage stubbed; returns (written rows, hook calls)."""
    monkeypatch.setattr(token_scratch.storage_paths, "get_storage_root", lambda: tmp_path)
    monkeypatch.setattr(token_service, "get_storage_root", lambda: tmp_path)
    monkeypatch.setattr(token_scratch.db_projects, "get_by_id", lambda _: {"project_name": "Project"})
    monkeypatch.setattr(token_scratch.storage_paths, "resolve_storage_dir", lambda value, _: Path(value))
    monkeypatch.setattr(token_service, "_next_token_id", lambda: "tok_20261014_000061")
    monkeypatch.setattr(token_service, "_active_pepper", lambda: ("v1", "pepper"))
    monkeypatch.setattr(token_service, "to_storage_relative", lambda path, _: str(path))
    monkeypatch.setattr(token_service, "get_store", lambda: _Store())
    monkeypatch.setattr(token_scratch, "sweep_on_issue", lambda _: None)
    written, calls = [], []
    monkeypatch.setattr(token_service.db_tokens, "create", lambda data: written.append(dict(data)) or {})
    monkeypatch.setattr(token_service.db_events, "create", lambda _: None)

    def hooks(action, *args, **_kwargs):
        calls.append((action, *args))
        if action == "chat_source_refusal":
            return editing.chat_source_refusal(*args)
        return None

    monkeypatch.setattr(graph_execution_hooks, "call", hooks)
    return tmp_path, written, calls


@pytest.mark.parametrize("resolved", [("read_write", False), (None, True)], ids=["edit", "edit_once"])
def test_an_edit_chat_of_a_locked_flow_is_refused_before_anything_is_written(issuing, monkeypatch, resolved):
    root, written, calls = issuing
    monkeypatch.setattr(token_service, "_resolve_chat_source_access", lambda *_: resolved)
    monkeypatch.setattr(run_store, "active_run", _run("paused"))
    from modules.flow_gate.db import user_chat_source_access
    monkeypatch.setattr(user_chat_source_access, "claim", lambda *_: pytest.fail("no edit-once claim"))

    with pytest.raises(HTTPException) as refused:
        token_service.issue(PROJECT, FLOW, "chat", f"{FLOW}.0002-CH", "user", continuation_locale="en")

    assert refused.value.detail["code"] == "graph_run_locked"
    assert refused.value.detail["message"] == graph_messages.CHAT_SOURCE_LOCKED["en"]
    assert calls == [("chat_source_refusal", FLOW, "en")]
    assert written == []
    assert not (root / "work" / "Project" / "tok_20261014_000061").exists()


def test_a_read_chat_of_a_locked_flow_is_issued(issuing, monkeypatch):
    _root, written, calls = issuing
    monkeypatch.setattr(token_service, "_resolve_chat_source_access", lambda *_: ("read", False))
    monkeypatch.setattr(run_store, "active_run", _run("running"))

    issued = token_service.issue(PROJECT, FLOW, "chat", f"{FLOW}.0002-CH", "user")

    assert issued["source_access"] == "read"
    assert calls == []
    assert [row["source_access"] for row in written] == ["read"]


def test_an_edit_chat_of_an_idle_flow_is_issued(issuing, monkeypatch):
    _root, written, calls = issuing
    monkeypatch.setattr(token_service, "_resolve_chat_source_access", lambda *_: ("read_write", False))
    monkeypatch.setattr(run_store, "active_run", _run("idle"))

    issued = token_service.issue(PROJECT, FLOW, "chat", f"{FLOW}.0002-CH", "user")

    assert issued["source_access"] == "read_write"
    assert calls == [("chat_source_refusal", FLOW, None)]
    assert [row["source_access"] for row in written] == ["read_write"]


def test_a_token_that_is_no_chat_is_not_judged(issuing, monkeypatch):
    _root, _written, calls = issuing
    monkeypatch.setattr(run_store, "active_run", _run("running"))
    token_service.issue(PROJECT, FLOW, "edit", f"{FLOW}.0030-T", "user")
    assert calls == []
