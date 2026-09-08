"""flowgate.default.0481 T0010 — the two AI entrances the operator said were dead.

T0010 reported, in order: [AI에게 맡기기] only ever answered "기준 브랜치 AI 정리를 시작하지
못했습니다.", the conflict dialog offered no action at all, the conflict AI call did not seem
to fire, and the resolution that finally came back was wrong. This file covers the server
half of #1 and #5; the group-worktree admission gates behind #1 are exercised against a real
repository in test_git_integration_0115.py (TestBaseDirtyDelegationAdmission0481,
test_base_dirty_belongs_to_the_merge_while_a_conflict_is_open), and the dialog half of #2/#3
lives in client/tests/main/GitConflictResolverDialog.spec.ts.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import project_ai_leases  # noqa: E402


class _Request:
    headers = {"x-locale": "ko"}
    url = type("URL", (), {"scheme": "http", "hostname": "localhost", "port": 80})()


def _start(monkeypatch, base_dirty: dict):
    from modules.flow_gate.api.v1 import ai_invoke_routes

    monkeypatch.setattr(ai_invoke_routes, "_require_user", lambda _r: {"issued_to": "u", "is_admin": True})
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id", lambda _p: {"id": "flowgate"})
    monkeypatch.setattr(
        ai_invoke_routes.git_service, "project_git_status",
        lambda _p: {"status": {"base_dirty": base_dirty}},
    )
    monkeypatch.setattr(project_ai_leases, "get_active", lambda _p: None)
    body = ai_invoke_routes.AiInvokeStartRequest(
        project="flowgate", module="none", action_scope="resolve_base_dirty", mode="single",
    )
    response = ai_invoke_routes.start_ai_invoke(body, _Request())
    return response, json.loads(response.body)


def test_base_dirty_delegation_is_refused_by_name_while_a_merge_is_open(monkeypatch):
    # T0010 #1: during a stopped merge the base checkout's dirty set IS the merge, so
    # committing or discarding it would destroy the merge. Before this, the request fell
    # through to start_run and died on the phantom group's worktree gate with a generic
    # `worktree_unavailable` — the operator only ever saw "…시작하지 못했습니다.".
    response, payload = _start(monkeypatch, {
        "dirty": True,
        "files": ["shared.py"],
        "merge_in_progress": {"merge_id": 42, "group_id": "flowgate.default.0481"},
    })
    assert response.status_code == 409
    assert payload["code"] == "base_dirty_merge_in_progress"
    # The refusal carries where to go instead, so the panel can open that resolver.
    assert payload["merge_id"] == 42
    assert payload["group_id"] == "flowgate.default.0481"


def test_base_dirty_delegation_is_not_refused_when_no_merge_is_open(monkeypatch):
    # The guard is about a merge, not about base dirt: with merge_in_progress absent the
    # request gets past this check and on into ordinary admission.
    # The next gate after this one is the project run lease; refusing there proves the merge
    # guard let the request through.
    monkeypatch.setattr(project_ai_leases, "acquire", lambda *a, **k: None)
    response, payload = _start(monkeypatch, {
        "dirty": True, "files": ["shared.py"], "merge_in_progress": None,
    })
    assert payload["code"] == "base_dirty_run_in_progress"


def test_empty_still_wins_over_an_open_merge(monkeypatch):
    # L0008 §4's ordering is unchanged: nothing to delegate outranks every other reason.
    response, payload = _start(monkeypatch, {
        "dirty": False, "files": [],
        "merge_in_progress": {"merge_id": 42, "group_id": "flowgate.default.0481"},
    })
    assert response.status_code == 409
    assert payload["code"] == "base_dirty_empty"


# ── T0010 #5: what the conflict worker is actually told to do ────────────────

def _merge_task() -> str:
    from modules.flow_gate.api import token_routes

    return token_routes._conflict_task_section("merge", {})


def test_general_merge_task_states_the_human_approval_gate():
    # T0008 moved a general merge's endpoint from "commit" to `resolved_pending_review`, but
    # the merge branch of the task section still described the old world — only the tr_*
    # branch told its worker a person reads the diff afterwards. A worker that believes its
    # submission ships behaves differently from one that knows it will be read.
    task = _merge_task()
    assert "resolved_pending_review" in task
    assert "not at a commit" in task


def test_general_merge_task_defines_a_correct_resolution_not_just_a_marker_free_file():
    # "Strip the markers" is a description of a syntactically finished file. T0010 #5 was
    # about the answer being wrong, so the task has to say what right means: both sides'
    # intent, the zdiff3 base as the ancestor, and read around the chunk before deciding.
    task = _merge_task()
    assert "intent" in task
    assert "base" in task
    assert "Keeping both blocks verbatim" in task


def test_tr_conflict_task_is_untouched():
    from modules.flow_gate.api import token_routes

    tr_task = token_routes._conflict_task_section("tr_revert", {"doc_code": "0001-TR", "subject": "s"})
    assert "This is NOT a branch merge." in tr_task
    assert "UNDOING" in tr_task


# ── T0010 #1: the live card has to be filed where the browser looks for it ───

def test_run_event_payloads_carry_the_project_scoped_identity():
    """A resolve_base_dirty run's rows are keyed by the synthetic `<project>.none.0000`
    group, but the browser files its card under `project:<id>`. Both identities have to be
    on the wire or the card never appears and the button never re-enables."""
    from collections import defaultdict

    from modules.flow_gate.services.ai_invoke import finalize

    # finished_payload reads a long tail of run keys that have nothing to do with this
    # assertion; a None-defaulting mapping keeps the test about the two fields it is for.
    run = defaultdict(lambda: None)
    run.update({
        "run_id": "run-1", "group_id": "flowgate.none.0000", "project_id": "flowgate",
        "action_scope": "resolve_base_dirty", "doc_ref": "flowgate", "outcome": "complete",
        "docs_reached": 0, "docs_target": 0, "reached_doc_ids": [], "end_reason": "exited",
        "exit_code": 0, "last_message_received": False, "last_message": None,
        "provider_id": "p1", "provider": {"name": "P"}, "attempt_no": 1, "fallback_history": [],
        "source_dirty": False, "duration_ms": 0,
    })
    payload = finalize.finished_payload(run)
    assert payload["project_id"] == "flowgate"
    assert payload["action_scope"] == "resolve_base_dirty"


def test_started_event_payload_carries_the_project_scoped_identity():
    # The started event is built inline in worker._run_worker; assert on the source so the
    # two payloads cannot drift apart again.
    from modules.flow_gate.services.ai_invoke import worker

    source = Path(worker.__file__).read_text(encoding="utf-8")
    started = source.split('_broadcast(run, "ai_invoke_started"', 1)[1].split("})", 1)[0]
    assert '"project_id": run["project_id"]' in started
    assert '"action_scope": run.get("action_scope")' in started


# ── T0010 rev1 (2026-09-08 반려) ────────────────────────────────────────────────
# "난 채팅을 치면 기다렸다가 바로 답장 받는걸 원했는데 아예 다이얼로그 밖으로 빠져나가서
# 기본 AI실행 다이얼로그 보는걸 원하지 않는다." 승인 대기 화면이 제자리에서 기다리려면
# 서버가 "지금 이 대화 턴의 실행이 돌고 있다"를 말해 줘야 하고, 영영 오지 않을 답을
# 기다리게 두어서도 안 된다.

def _review_session(monkeypatch, context: dict):
    """Drive get_merge_review against an in-memory session context."""
    from pathlib import Path as _Path

    from modules.flow_gate.services import git_service

    session = {"group_id": "flowgate.default.0481", "kind": "merge"}
    monkeypatch.setattr(
        git_service, "_merge_review_session",
        lambda g, m: (session, context, "flowgate", _Path("."), "main"),
    )
    monkeypatch.setattr(git_service.db_git, "get_session", lambda _m: session)
    monkeypatch.setattr(git_service.db_git, "session_context", lambda _s: context)
    monkeypatch.setattr(
        git_service.db_git, "set_session_context",
        lambda _m, c: context.update(c),
    )
    monkeypatch.setattr(git_service, "_acquire_lock", lambda *a, **k: True)
    monkeypatch.setattr(git_service.db_git, "release_lock", lambda *a, **k: None)
    return git_service


def _pending_context(**overrides) -> dict:
    context = {
        "review_state": "resolved_pending_review",
        "review_fingerprint": "fp-1",
        "instruction_generation": 0,
        "changes": [], "conflict_origins": [], "held_test_operations": [],
        "conversation": [{"turn_id": "h1", "role": "human", "message": "왜 그렇게 고쳤어?",
                          "provider_id": "p1", "status": "accepted", "created_at": "2026-09-08T00:00:00+09:00"}],
        "pending_conversation_run_id": "run-1",
        "pending_conversation_write_requested": False,
        "pending_conversation_start_fingerprint": "fp-1",
        "pending_conversation_start_generation": 0,
    }
    context.update(overrides)
    return context


def _patch_run_detail(monkeypatch, result):
    from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics

    def _detail(_run_id):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(ai_diagnostics, "get_run_detail", _detail)


def test_review_payload_names_the_chat_turn_still_in_flight(monkeypatch):
    # Without this block the screen had no way to know a run was working, so the only
    # place to find out was the generic AI-run dialog — the very thing the rejection
    # refuses. It must survive a reload too: this is server truth, not local state.
    context = _pending_context()
    git_service = _review_session(monkeypatch, context)
    _patch_run_detail(monkeypatch, {
        "run_id": "run-1", "status": "running", "elapsed_ms": 65_000,
        "provider": {"id": "p1", "name": "Claude Sonnet 5"},
    })

    result = git_service.get_merge_review("flowgate.default.0481", 9)["result"]

    assert result["pending_conversation"] == {
        "run_id": "run-1", "status": "running", "provider": "Claude Sonnet 5",
        "started_at": None, "elapsed_ms": 65_000,
        "write_requested": False, "allow_test_edits": False,
    }
    # A run still working is never folded in — the human turn is still the last one.
    assert [t["role"] for t in result["conversation"]] == ["human"]


def test_review_payload_reports_no_pending_turn_once_the_answer_landed(monkeypatch):
    context = _pending_context()
    git_service = _review_session(monkeypatch, context)
    _patch_run_detail(monkeypatch, {
        "run_id": "run-1", "status": "finished", "succeeded": True,
        "provider_id": "p1", "last_message": "ko.ts 에 새 키를 넣었습니다.",
    })

    result = git_service.get_merge_review("flowgate.default.0481", 9)["result"]

    assert result["pending_conversation"] is None
    assert result["conversation"][-1]["role"] == "ai"
    assert result["conversation"][-1]["message"] == "ko.ts 에 새 키를 넣었습니다."


def test_a_run_whose_record_is_gone_answers_instead_of_waiting_forever(monkeypatch):
    # A 404 on the run id means no later poll can EVER observe it finishing. Before
    # this the pending marker stayed set forever: the chat waited for a reply that
    # could not arrive and every further message was refused `re_instruction_busy`.
    from fastapi import HTTPException

    context = _pending_context()
    git_service = _review_session(monkeypatch, context)
    _patch_run_detail(monkeypatch, HTTPException(status_code=404, detail={"code": "run_not_found"}))

    result = git_service.get_merge_review("flowgate.default.0481", 9)["result"]

    assert result["pending_conversation"] is None
    assert result["conversation"][-1]["status"] == "run_lost"
    assert "다시 보내" in result["conversation"][-1]["message"]
    # The chat is free again — nothing is left claiming the conversation slot.
    assert context.get("pending_conversation_run_id") is None


def test_a_transient_lookup_failure_keeps_the_turn_pending(monkeypatch):
    # Only a 404 is decidable. A DB hiccup must NOT be mistaken for a lost run, or a
    # perfectly good answer would be thrown away and replaced with a false failure.
    context = _pending_context()
    git_service = _review_session(monkeypatch, context)
    _patch_run_detail(monkeypatch, RuntimeError("database is locked"))

    result = git_service.get_merge_review("flowgate.default.0481", 9)["result"]

    assert result["pending_conversation"]["status"] == "unknown"
    assert [t["role"] for t in result["conversation"]] == ["human"]
    assert context.get("pending_conversation_run_id") == "run-1"
