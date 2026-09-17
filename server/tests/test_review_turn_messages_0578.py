"""flowgate.default.0578 T0006 — merge-review turns store MEANING, not a Korean sentence.

NR0003 F1 measured that every server-authored turn in the merge-review conversation was
assembled as a finished Korean sentence and frozen into ``context['conversation']``: the
screen could never show it in another language, and an en/ja worker replaying the history
read Korean. F2 measured the other half — a review-message turn runs asynchronously and
the locale the reviewer started it in was not written down anywhere, so nothing downstream
could honour it.

This file pins both halves as a contract (T0006 §2):

  * the nine server-authored events are stored as ``message_code`` + ``message_params``,
    with ``message`` holding only what a human or a model actually wrote;
  * ``apply_errors`` keeps the causes of a failed apply verbatim and separate from both
    the body copy and ``context['last_error']``;
  * the start locale is pinned on the pending row, travels to the run's token, and is
    recorded on the finished turn as ``source_locale`` (diagnostic only);
  * both shapes — coded and legacy — replay into an AI history with no empty line, in
    ko/en/ja, asserted against each locale's expected words rather than "no Hangul".
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services.git import review_messages  # noqa: E402

GROUP = "flowgate.default.0578"
MERGE_ID = 9


def _has_hangul(text: str) -> bool:
    return any("가" <= ch <= "힣" or "ㄱ" <= ch <= "ㆎ" for ch in text)


# ── the in-memory review session (same shape as 0481 T0010's harness) ──────────────

def _context(**overrides) -> dict:
    context = {
        "review_state": "resolved_pending_review",
        "review_fingerprint": "fp-1",
        "instruction_generation": 0,
        "changes": [], "conflict_origins": [], "held_test_operations": [],
        "conversation": [{
            "turn_id": "h1", "role": "human", "message": "왜 그렇게 고쳤어?",
            "provider_id": "p1", "status": "accepted",
            "created_at": "2026-09-17T00:00:00+09:00",
        }],
        "pending_conversation_run_id": "run-1",
        "pending_conversation_write_requested": False,
        "pending_conversation_allow_test_edits": False,
        "pending_conversation_start_fingerprint": "fp-1",
        "pending_conversation_start_generation": 0,
        "pending_conversation_locale": "ko",
    }
    context.update(overrides)
    return context


def _session(monkeypatch, context: dict):
    from modules.flow_gate.services import git_service

    session = {"group_id": GROUP, "kind": "merge"}
    monkeypatch.setattr(
        git_service, "_merge_review_session",
        lambda g, m: (session, context, "flowgate", Path("."), "main"),
    )
    monkeypatch.setattr(git_service.db_git, "get_session", lambda _m: session)
    monkeypatch.setattr(git_service.db_git, "session_context", lambda _s: context)
    monkeypatch.setattr(
        git_service.db_git, "set_session_context", lambda _m, c: context.update(c),
    )
    monkeypatch.setattr(git_service, "_acquire_lock", lambda *a, **k: True)
    monkeypatch.setattr(git_service.db_git, "release_lock", lambda *a, **k: None)
    return git_service


def _run_detail(monkeypatch, result):
    from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics

    def _detail(_run_id):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(ai_diagnostics, "get_run_detail", _detail)


def _lost(monkeypatch):
    from fastapi import HTTPException

    _run_detail(monkeypatch, HTTPException(status_code=404, detail={"code": "run_not_found"}))


def _materialize(monkeypatch, context: dict):
    """Run one materialization and return the turn it appended."""
    git_service = _session(monkeypatch, context)
    try:
        git_service.get_merge_review(GROUP, MERGE_ID)
    except git_service.GitServiceError:
        pass  # a settled review refuses the read, but still materializes (0481 T0010 rev4)
    return context["conversation"][-1]


def _apply(monkeypatch, context: dict, apply_result):
    """Materialize through the write-plan apply branch with a canned apply result."""
    from modules.flow_gate.services import git_service

    monkeypatch.setattr(
        git_service, "_apply_write_plan_locked",
        lambda *a, **k: apply_result() if callable(apply_result) else apply_result,
    )
    _run_detail(monkeypatch, {
        "run_id": "run-1", "status": "finished", "succeeded": True, "provider_id": "p1",
        "write_plan": {"schema_version": "flowgate.write-plan.v1", "operations": []},
    })
    return _materialize(monkeypatch, context)


# ── §2.2 — the nine codes ─────────────────────────────────────────────────────────

def test_run_lost_stores_a_code_and_no_sentence(monkeypatch):
    context = _context()
    _lost(monkeypatch)
    turn = _materialize(monkeypatch, context)

    assert turn["status"] == "run_lost"
    assert turn["message_code"] == "review_run_lost"
    assert turn["message_params"] == {}
    assert turn["message"] == ""


def test_cancelled_stores_a_code_and_no_sentence(monkeypatch):
    context = _context()
    _run_detail(monkeypatch, {
        "run_id": "run-1", "status": "finished", "succeeded": False,
        "end_reason": "cancelled", "last_message": None, "provider_id": "p1",
    })
    turn = _materialize(monkeypatch, context)

    assert turn["status"] == "cancelled"
    assert turn["message_code"] == "review_cancelled"
    assert turn["message"] == ""


def test_an_empty_success_is_a_code_not_the_english_literal(monkeypatch):
    # Until this change the turn read "(no answer)" — an English literal, on a Korean
    # screen, which no locale could ever change.
    context = _context()
    _run_detail(monkeypatch, {
        "run_id": "run-1", "status": "finished", "succeeded": True,
        "last_message": "", "provider_id": "p1",
    })
    turn = _materialize(monkeypatch, context)

    assert turn["status"] == "accepted"
    assert turn["message_code"] == "review_no_answer"
    assert turn["message"] == ""


def test_a_failed_run_is_a_code_not_the_english_literal(monkeypatch):
    context = _context()
    _run_detail(monkeypatch, {
        "run_id": "run-1", "status": "failed", "succeeded": False,
        "last_message": None, "provider_id": "p1",
    })
    turn = _materialize(monkeypatch, context)

    assert turn["status"] == "failed"
    assert turn["message_code"] == "review_run_failed"
    assert turn["message"] == ""


def test_a_real_answer_is_content_and_carries_no_code(monkeypatch):
    # The model's own words are never replaced by a code: only the server's sentences are.
    context = _context()
    _run_detail(monkeypatch, {
        "run_id": "run-1", "status": "finished", "succeeded": True,
        "last_message": "ko.ts 에 새 키를 넣었습니다.", "provider_id": "p1",
    })
    turn = _materialize(monkeypatch, context)

    assert turn["message"] == "ko.ts 에 새 키를 넣었습니다."
    assert "message_code" not in turn


def test_apply_re_review_stores_paths_and_counts(monkeypatch):
    context = _context(pending_conversation_write_requested=True)
    turn = _apply(monkeypatch, context, {
        "status": "re_review", "review_state": "re_review",
        "changed_paths": ["client/shared/i18n/ko.ts", "server/app.py"],
        "held_test_operations": [{"operation_id": "o1", "path": "server/tests/x.py",
                                  "purpose": "add a case"}],
    })

    assert turn["status"] == "accepted"
    assert turn["message_code"] == "review_apply_re_review"
    assert turn["message_params"]["paths"] == ["client/shared/i18n/ko.ts", "server/app.py"]
    assert turn["message_params"]["path_count"] == 2
    assert turn["message_params"]["held_count"] == 1
    assert turn["message"] == ""
    # The held PATHS are not re-listed in a sentence — they live on the session field the
    # screen already renders in full.
    assert "apply_errors" not in turn


def test_apply_held_only_keeps_only_the_count(monkeypatch):
    context = _context(pending_conversation_write_requested=True)
    turn = _apply(monkeypatch, context, {
        "status": "held_only",
        "held_test_operations": [{"operation_id": "o1", "path": "server/tests/x.py",
                                  "purpose": "add a case"}],
    })

    assert turn["message_code"] == "review_apply_held_only"
    assert turn["message_params"] == {"held_count": 1}


def test_rollback_verification_failed_needs_no_params(monkeypatch):
    context = _context(pending_conversation_write_requested=True)
    turn = _apply(monkeypatch, context, {"status": "rollback_verification_failed"})

    assert turn["status"] == "failed"
    assert turn["message_code"] == "review_apply_rollback_verification_failed"
    assert turn["message_params"] == {}


def test_apply_failure_keeps_causes_in_apply_errors_not_in_the_body(monkeypatch):
    # §2.3 — the old code spliced `json.dumps(errors)` onto a Korean sentence, so a raw
    # stderr line became user-facing product copy in a language it was never written for.
    errors = [
        {"path": "server/app.py", "validator": "python-compile", "line": 12,
         "message": "SyntaxError: invalid syntax"},
        {"message": "fatal: pathspec 'x' did not match any files"},
    ]
    context = _context(pending_conversation_write_requested=True,
                       last_error={"code": "rollback_verification_failed"})
    turn = _apply(monkeypatch, context, {"status": "apply_failed", "errors": errors})

    assert turn["message_code"] == "review_apply_failed"
    assert turn["message_params"] == {"held_count": 0, "error_count": 2}
    assert turn["apply_errors"] == errors            # verbatim, item for item
    assert turn["message"] == ""
    # NOT merged with the session-level coordination field, which means something else.
    assert context["last_error"] == {"code": "rollback_verification_failed"}
    assert turn["apply_errors"] is not context["last_error"]


def test_a_git_service_error_during_apply_splits_code_from_message(monkeypatch):
    # §2.3: `{"message": f"{code}: {message}"}` made the stable code unreadable to anything
    # but a substring match. Code and diagnostic sentence are separate fields now.
    from modules.flow_gate.services import git_service

    def _raise(*_a, **_k):
        raise git_service.GitServiceError(409, "git_busy", "another git operation is running")

    context = _context(pending_conversation_write_requested=True)
    monkeypatch.setattr(git_service, "_apply_write_plan_locked", _raise)
    _run_detail(monkeypatch, {
        "run_id": "run-1", "status": "finished", "succeeded": True, "provider_id": "p1",
        "write_plan": {"schema_version": "flowgate.write-plan.v1", "operations": []},
    })
    turn = _materialize(monkeypatch, context)

    assert turn["message_code"] == "review_apply_failed"
    assert turn["apply_errors"] == [
        {"code": "git_busy", "message": "another git operation is running"}
    ]


def test_no_server_authored_sentence_is_stored_for_any_branch(monkeypatch):
    """Every coded turn's `message` is either empty or something a model wrote."""
    produced = []

    context = _context()
    _lost(monkeypatch)
    produced.append(_materialize(monkeypatch, context))

    for detail in (
        {"status": "finished", "succeeded": False, "end_reason": "cancelled",
         "last_message": None, "provider_id": "p1"},
        {"status": "finished", "succeeded": True, "last_message": "", "provider_id": "p1"},
        {"status": "failed", "succeeded": False, "last_message": None, "provider_id": "p1"},
    ):
        context = _context()
        _run_detail(monkeypatch, detail)
        produced.append(_materialize(monkeypatch, context))

    for result in (
        {"status": "re_review", "changed_paths": ["a.py"], "held_test_operations": []},
        {"status": "held_only", "held_test_operations": [{"operation_id": "o", "path": "t.py",
                                                          "purpose": "p"}]},
        {"status": "rollback_verification_failed"},
        {"status": "apply_failed", "errors": [{"message": "boom"}]},
    ):
        context = _context(pending_conversation_write_requested=True)
        produced.append(_apply(monkeypatch, context, result))

    context = _context(review_state="completed")
    _run_detail(monkeypatch, {"status": "finished", "succeeded": True,
                              "last_message": "고쳤습니다.", "provider_id": "p1"})
    produced.append(_materialize(monkeypatch, context))

    assert len(produced) == 9
    codes = {t.get("message_code") for t in produced}
    assert codes == set(review_messages.TURN_MESSAGE_CODES)
    for turn in produced:
        if turn["message"]:
            # The only non-empty body in the set is the model's own answer.
            assert turn["message"] == "고쳤습니다."
        # No Hangul anywhere in the stored payload other than that answer.
        payload = dict(turn)
        payload.pop("message", None)
        assert not _has_hangul(json.dumps(payload, ensure_ascii=False))


# ── §2.2 — stale_run's three reasons ──────────────────────────────────────────────

def _stale(monkeypatch, **context_overrides):
    context = _context(**context_overrides)
    _run_detail(monkeypatch, {
        "run_id": "run-1", "status": "finished", "succeeded": True,
        "last_message": "수정했습니다.", "provider_id": "p1",
        "write_plan": {"schema_version": "flowgate.write-plan.v1", "operations": []},
    })
    return context, _materialize(monkeypatch, context)


def test_stale_run_names_which_check_fired_approval(monkeypatch):
    _context_used, turn = _stale(monkeypatch, review_state="completed")

    assert turn["status"] == "stale_run"
    assert turn["message_code"] == "review_stale_run"
    assert turn["message_params"]["changed"] == ["approval_settled"]
    assert turn["message_params"]["review_state"] == "completed"
    # The answer survives, verbatim — 0481 T0010 rev6's point, kept.
    assert turn["message"] == "수정했습니다."


def test_stale_run_names_which_check_fired_refreeze(monkeypatch):
    _context_used, turn = _stale(monkeypatch, review_fingerprint="fp-2")

    assert turn["message_params"]["changed"] == ["candidate_refrozen"]
    assert "review_state" not in turn["message_params"]


def test_stale_run_names_which_check_fired_generation(monkeypatch):
    _context_used, turn = _stale(monkeypatch, instruction_generation=1)

    assert turn["message_params"]["changed"] == ["instruction_generation_bumped"]


def test_stale_run_reports_the_discarded_plan_and_never_applies_it(monkeypatch):
    from modules.flow_gate.services import git_service

    applied = []
    monkeypatch.setattr(
        git_service, "_apply_write_plan_locked",
        lambda *a, **k: applied.append(a) or {"status": "re_review", "changed_paths": []},
    )
    context, turn = _stale(
        monkeypatch, review_state="completed", pending_conversation_write_requested=True,
    )

    assert turn["message_params"]["plan_discarded"] is True
    assert applied == []                      # L0007 §2.9 unchanged: never applied
    assert context.get("pending_conversation_run_id") is None


def test_stale_run_without_a_plan_says_so(monkeypatch):
    _context_used, turn = _stale(monkeypatch, review_state="completed")

    assert turn["message_params"]["plan_discarded"] is False


# ── §3 work item 3 — the locale's life cycle ──────────────────────────────────────

@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_send_review_message_pins_the_start_locale_on_the_pending_row(monkeypatch, locale):
    context = _context(pending_conversation_run_id=None, pending_conversation_locale=None)
    git_service = _session(monkeypatch, context)

    git_service.send_review_message(
        GROUP, MERGE_ID, message="이거 왜 이래?", provider_id="p1", provider_pinned=True,
        apply_requested=False, start_run=lambda: "run-2", locale=locale,
    )

    assert context["pending_conversation_locale"] == locale


def test_an_unsupported_locale_folds_to_the_default(monkeypatch):
    context = _context(pending_conversation_run_id=None, pending_conversation_locale=None)
    git_service = _session(monkeypatch, context)

    git_service.send_review_message(
        GROUP, MERGE_ID, message="질문", provider_id="p1", provider_pinned=True,
        apply_requested=False, start_run=lambda: "run-2", locale="zh",
    )

    assert context["pending_conversation_locale"] == "ko"


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_the_finished_turn_records_the_locale_the_run_started_in(monkeypatch, locale):
    # The two halves are deliberately separate REQUEST boundaries: the message is accepted
    # in one call and materialized in a later poll that knows nothing about the first.
    context = _context(pending_conversation_run_id=None, pending_conversation_locale=None)
    git_service = _session(monkeypatch, context)
    git_service.send_review_message(
        GROUP, MERGE_ID, message="질문", provider_id="p1", provider_pinned=True,
        apply_requested=False, start_run=lambda: "run-2", locale=locale,
    )
    _lost(monkeypatch)

    turn = _materialize(monkeypatch, context)

    assert turn["source_locale"] == locale
    # …and the pending bookkeeping is cleared with everything else it was stored beside.
    assert "pending_conversation_locale" not in context


def test_a_legacy_pending_without_a_locale_reads_as_the_default(monkeypatch):
    context = _context()
    context.pop("pending_conversation_locale")
    _lost(monkeypatch)

    turn = _materialize(monkeypatch, context)

    assert turn["source_locale"] == "ko"


def test_materializing_never_rewrites_an_earlier_turn(monkeypatch):
    legacy = {
        "turn_id": "old", "role": "ai",
        "message": "이 지시를 맡은 실행의 기록이 남아 있지 않아 답을 받지 못했습니다(run_lost).",
        "provider_id": None, "status": "run_lost", "created_at": "2026-09-01T00:00:00+09:00",
    }
    context = _context(conversation=[dict(legacy)])
    _lost(monkeypatch)

    _materialize(monkeypatch, context)

    assert context["conversation"][0] == legacy   # not re-coded, not re-translated
    assert "source_locale" not in context["conversation"][0]


def test_materializing_touches_no_other_context_key(monkeypatch):
    context = _context(
        base_head="abc", merge_head="def", resolver_provider="p1",
        held_test_operations=[{"operation_id": "o", "path": "t.py", "purpose": "p"}],
    )
    before = {k: json.dumps(v, ensure_ascii=False)
              for k, v in context.items()
              if not k.startswith("pending_conversation") and k != "conversation"}
    _lost(monkeypatch)

    _materialize(monkeypatch, context)

    after = {k: json.dumps(v, ensure_ascii=False)
             for k, v in context.items()
             if not k.startswith("pending_conversation") and k != "conversation"}
    assert after == before


def test_the_route_normalizes_the_header_once_for_both_halves(monkeypatch):
    """§3 work item 3-1: one value reaches the pending row AND the run start."""
    from modules.flow_gate.api.v1 import git_routes

    seen: dict = {}
    monkeypatch.setattr(git_routes, "_check_group_permission", lambda *a, **k: None)
    monkeypatch.setattr(
        git_routes.git_service, "send_review_message",
        lambda *a, **kw: seen.update(service_locale=kw["locale"], started=kw["start_run"]()) or {},
    )
    monkeypatch.setattr(
        git_routes, "_start_resolve_conflict_run",
        lambda **kw: seen.update(run_locale=kw["locale"]) or "run-9",
    )

    class _Request:
        headers = {"x-locale": "ja"}

    body = git_routes.ReviewMessageBody(message="q", provider_id="p1", provider_pinned=True)
    git_routes.post_merge_review_message(
        GROUP, MERGE_ID, body, _Request(), user={"user_id": "u"},
    )

    assert seen["service_locale"] == "ja"
    assert seen["run_locale"] == "ja"


def test_the_run_start_folds_an_unsupported_header_to_the_default(monkeypatch):
    """The [Reject] path passes no locale of its own and still gets a normalized value."""
    from modules.flow_gate.api.v1 import git_routes

    seen: dict = {}
    monkeypatch.setattr(git_routes, "_review_group_parts", lambda g: ("flowgate", "default"))
    monkeypatch.setattr(
        git_routes, "_resolve_conflict_mention_builder",
        lambda **kw: seen.update(mention_locale=kw["locale"]) or (lambda *a: "m"),
    )

    import modules.flow_gate.api.token_routes as token_routes
    from modules.flow_gate.services import ai_invoke_service

    monkeypatch.setattr(token_routes, "_build_api_base", lambda _r: "http://x/api/v1")
    monkeypatch.setattr(
        ai_invoke_service, "start_run",
        lambda **kw: seen.update(continuation_locale=kw["continuation_locale"]) or {"run_id": "r"},
    )

    class _Request:
        headers = {"x-locale": "zh"}

    git_routes._start_resolve_conflict_run(
        group_id=GROUP, merge_id=MERGE_ID, request=_Request(), user_id="u",
        provider_id="p1", provider_pinned=True, messages=["first"],
    )

    assert seen["continuation_locale"] == "ko"
    assert seen["mention_locale"] == "ko"


# ── §3 work item 3-4 — the token and the run row carry the start locale ───────────

@pytest.fixture
def invoke_world(monkeypatch, tmp_path):
    """start_run's collaborators, faked (the 0481 T0010 rev5 fixture's shape)."""
    from modules.flow_gate.services import ai_invoke_service as svc

    issued: list[dict] = []
    monkeypatch.setattr(svc, "ORACLE_SETTLE_SEC", 0)
    monkeypatch.setattr(svc, "_runs", {})
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda doc_id: None)
    monkeypatch.setattr(svc.db_docs, "get_group_max_seq", lambda group_id: 5)
    monkeypatch.setattr(svc.db_docs, "get_documents_by_group_id", lambda group_id: [])
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda pid: {"project_name": "testproj"})
    monkeypatch.setattr(
        svc.ai_settings_service, "resolve_effective",
        lambda pid: {"ok": True, "source": "test",
                     "providers": [{"id": "p1", "name": "Claude Haiku 4.5", "exec_type": "cli"}]},
    )
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda scope, pid: None)

    def _issue(**kw):
        issued.append(kw)
        return {"raw_token": "tok_raw_test", "token_id": "tok_20260917_000001",
                "expires_at": "2026-09-18T00:00:00+00:00",
                "scratch_dir": str(tmp_path / "tokwork")}

    monkeypatch.setattr(svc.token_service, "issue", _issue)
    monkeypatch.setattr(svc.token_service, "revoke", lambda *a, **kw: None)
    monkeypatch.setattr(svc.storage_paths, "get_storage_root", lambda *a, **kw: tmp_path / "storage")
    monkeypatch.setattr(svc.storage_paths, "resolve_project_src_root",
                        lambda pid, branch, *, group_id: None)
    monkeypatch.setattr(svc.storage_paths, "to_storage_relative", lambda path, project=None: str(path))
    monkeypatch.setattr(svc, "_broadcast", lambda run, event_type, payload: None)
    return svc, issued


def _start_invoke(svc, *, action_scope, group_id, merge_id, locale):
    import time
    import unittest.mock as mock

    with mock.patch.object(svc, "_cli_execute", side_effect=lambda p, prompt, run: ("ok", None)):
        response = svc.start_run(
            project_id="test2", module="default", group_id=group_id, doc_ref="",
            action_scope=action_scope, mode="single",
            continuation_target_seq=None, continuation_review_mode=False,
            continuation_instruction_mode=None, continuation_locale=locale,
            issued_to="usr_admin", api_base_url="http://127.0.0.1:1/flowgate/api/v1",
            mention_builder=lambda raw, scratch: "## prompt\nanswer the reviewer\n",
            merge_id=merge_id,
        )
        for _ in range(500):
            record = svc.get_run_record(response["run_id"])
            if record and record["status"] == "finished":
                break
            time.sleep(0.02)
    return response


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_a_merge_review_run_hands_its_locale_to_the_token_and_the_run_row(invoke_world, locale):
    svc, issued = invoke_world

    response = _start_invoke(
        svc, action_scope="resolve_conflict", group_id="test2.default.0005",
        merge_id=MERGE_ID, locale=locale,
    )

    # The token is what the worker calls /help and the source tools with.
    assert issued[0]["continuation_locale"] == locale
    assert svc.get_run_record(response["run_id"])["continuation_locale"] == locale


def test_other_single_mode_scopes_still_store_no_locale(invoke_world):
    # §3 work item 3-4: only the LOCALE widens, and only for resolve_conflict. A single run
    # in any other scope must keep the NULL it has always had.
    svc, issued = invoke_world

    response = _start_invoke(
        svc, action_scope="resolve_base_dirty", group_id="test2.none.0000",
        merge_id=None, locale="ja",
    )

    assert issued[0]["continuation_locale"] is None
    assert svc.get_run_record(response["run_id"])["continuation_locale"] is None


# ── §2.1 — the extension survives a real DB round trip, with no new column ────────

class _MockDB:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql: str, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params=None):
        return [dict(row) for row in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        self._conn.execute("BEGIN")
        txn = _MockTxn(self._conn)
        try:
            yield txn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self):
        self._conn.close()


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._last_cursor = None

    def execute(self, sql: str, params=None):
        self._last_cursor = self._conn.execute(sql, params or [])

    def fetch_one(self):
        row = self._last_cursor.fetchone() if self._last_cursor is not None else None
        return dict(row) if row else None

    def fetch_all(self):
        if self._last_cursor is None:
            return []
        return [dict(row) for row in self._last_cursor.fetchall()]


@pytest.fixture
def session_store(tmp_path, monkeypatch):
    mock_db = _MockDB(str(tmp_path / "review-turns-0578.db"))
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
    mock_db._conn.commit()

    from modules.flow_gate.db import connection as conn_mod

    original = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

    conn_mod.STORE = _PatchedStore()
    yield mock_db
    conn_mod.STORE = original
    mock_db.close()


def test_the_new_turn_fields_round_trip_through_the_session_context(session_store):
    from modules.flow_gate.db import git_integration as db_git

    session_store.execute(
        "INSERT INTO projects(project_id, project_name, is_active, created_at, updated_at) "
        "VALUES ('flowgate', 'FlowGate', 1, datetime('now'), datetime('now'))"
    )
    session_store.execute(
        "INSERT INTO group_git_state(group_id, project_id, branch, worktree_registered, "
        "status, created_at, updated_at) "
        "VALUES (?, 'flowgate', 'flowgate_default_0578', 1, 'conflict', "
        "datetime('now'), datetime('now'))",
        [GROUP],
    )
    merge_id = db_git.create_session(GROUP, ["shared.py"], "merge")

    context = {
        "review_state": "resolved_pending_review",
        "review_fingerprint": "fp-1",
        "held_test_operations": [{"operation_id": "o1", "path": "t.py", "purpose": "p"}],
        "conversation": [
            {"turn_id": "legacy", "role": "ai", "message": "예전 문장 그대로",
             "provider_id": "p1", "status": "accepted", "created_at": "2026-09-01T00:00:00+09:00"},
            {"turn_id": "new", "role": "ai", "message": "",
             "message_code": "review_apply_failed",
             "message_params": {"error_count": 1, "held_count": 0},
             "apply_errors": [{"path": "a.py", "message": "SyntaxError: invalid syntax"}],
             "source_locale": "ja",
             "provider_id": "p1", "status": "failed", "created_at": "2026-09-17T00:00:00+09:00"},
        ],
    }
    db_git.set_session_context(merge_id, context)

    read_back = db_git.session_context(db_git.get_session(merge_id))

    assert read_back == context                      # every field, no new column
    columns = {
        row["name"] for row in
        session_store.fetch_all("PRAGMA table_info(git_merge_session)", [])
    }
    assert "source_locale" not in columns and "message_code" not in columns
    assert "context" in columns


# ── §3 work item 4 — the AI history replays both shapes, in three languages ───────

def _history(monkeypatch, conversation, locale):
    from modules.flow_gate.api import token_routes

    monkeypatch.setattr(
        token_routes.git_service, "review_conversation_brief",
        lambda g, m: {
            "review_state": "resolved_pending_review", "base_head": "a", "merge_head": "b",
            "resolver_provider": "p1", "changes": [{"status": "M", "path": "shared.py"}],
            "conversation": conversation, "last_error": None, "held_test_operations": [],
        },
    )
    mention = token_routes._build_review_conversation_mention(
        group_id=GROUP, project_id="flowgate", merge_id=MERGE_ID,
        raw_token="tok", api_base_url="http://x/api/v1", locale=locale,
    )
    assert mention
    body = mention.split("## Conversation so far", 1)[-1]
    return mention, body


def _coded(turn_id, code, params, *, message="", status="failed", errors=None):
    turn = {"turn_id": turn_id, "role": "ai", "message": message, "message_code": code,
            "message_params": params, "provider_id": "p1", "status": status,
            "created_at": "2026-09-17T00:00:00+09:00", "source_locale": "ko"}
    if errors is not None:
        turn["apply_errors"] = errors
    return turn


EXPECTED_RUN_LOST = {
    "ko": "이 지시를 맡은 실행의 기록이 남아 있지 않아",
    "en": "No record is left of the run that took this instruction",
    "ja": "この指示を担当した実行の記録が残っておらず",
}
EXPECTED_STALE_REASON = {
    "ko": "승인 대상이 새 후보로 바뀌었습니다",
    "en": "the approval target moved to a new candidate",
    "ja": "承認対象が新しい候補に変わりました",
}


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_the_history_renders_coded_turns_in_this_runs_locale(monkeypatch, locale):
    conversation = [
        {"turn_id": "h1", "role": "human", "message": "왜 이렇게 했어?", "provider_id": "p1",
         "status": "accepted", "created_at": "2026-09-17T00:00:00+09:00"},
        _coded("a1", "review_run_lost", {}, status="run_lost"),
        _coded("a2", "review_stale_run",
               {"changed": ["candidate_refrozen"], "plan_discarded": False},
               message="README 의 Features 절이 문제였습니다.", status="stale_run"),
    ]

    _mention, body = _history(monkeypatch, conversation, locale)

    assert EXPECTED_RUN_LOST[locale] in body
    assert EXPECTED_STALE_REASON[locale] in body
    # The human's words and the model's own answer are content: never translated, never
    # replaced by a code.
    assert "왜 이렇게 했어?" in body
    assert "README 의 Features 절이 문제였습니다." in body
    if locale != "ko":
        # …and the SERVER's sentences in that locale carry none of the other language's
        # words (checked positively above, negatively here).
        assert EXPECTED_RUN_LOST["ko"] not in body


def test_the_history_replays_legacy_turns_exactly_as_stored(monkeypatch):
    legacy_text = "이 답을 만드는 동안 승인 대상이 새 후보로 바뀌었습니다. (stale_run)"
    conversation = [
        {"turn_id": "old", "role": "ai", "message": legacy_text, "provider_id": "p1",
         "status": "stale_run", "created_at": "2026-09-01T00:00:00+09:00"},
    ]

    _mention, body = _history(monkeypatch, conversation, "en")

    assert legacy_text in body


def test_the_history_never_produces_an_empty_turn(monkeypatch):
    conversation = [
        # A turn with neither a code nor text — the shape that used to render "[1] you (AI): ".
        {"turn_id": "blank", "role": "ai", "message": "", "provider_id": "p1",
         "status": "failed", "created_at": "2026-09-17T00:00:00+09:00"},
        # An unregistered code — a newer server talking to this renderer.
        _coded("future", "review_something_new_0999", {"x": 1}, status="cancelled"),
    ]

    _mention, body = _history(monkeypatch, conversation, "en")

    assert "This turn ended in the 'failed' state." in body
    assert "This turn ended in the 'cancelled' state." in body
    for line in body.splitlines():
        if line.startswith("[") and "]" in line:
            assert line.split(": ", 1)[-1].strip(), line


def test_apply_errors_reach_the_worker_structured_and_out_of_the_copy(monkeypatch):
    errors = [{"path": "server/app.py", "message": "SyntaxError: invalid syntax"}]
    conversation = [
        _coded("f1", "review_apply_failed", {"error_count": 1, "held_count": 0}, errors=errors),
    ]

    _mention, body = _history(monkeypatch, conversation, "en")

    notice = next(line for line in body.splitlines() if line.startswith("[1]"))
    assert "1 cause(s)" in notice
    assert "SyntaxError" not in notice                     # not in the product sentence
    assert f"apply_errors: {json.dumps(errors, ensure_ascii=False)}" in body


# ── the renderer's own contract ───────────────────────────────────────────────────

@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_the_renderer_never_raises_on_bad_input(locale):
    for code, params in (
        (None, None),
        ("review_something_new_0999", {"x": 1}),
        ("review_stale_run", {}),                         # required `changed` missing
        ("review_stale_run", {"changed": "not-a-list"}),
        ("review_stale_run", {"changed": ["approval_settled"]}),   # review_state missing
        ("review_apply_held_only", {}),                   # required count missing
        ("review_apply_failed", {"error_count": "two"}),  # wrong type
        ("review_apply_re_review", {"paths": None}),
    ):
        text = review_messages.render_turn_message(code, params, locale, "failed")
        assert text and isinstance(text, str)


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_the_renderer_leaks_no_korean_in_the_other_locales(locale):
    for code in review_messages.TURN_MESSAGE_CODES:
        params = {
            "changed": list(review_messages.STALE_REASON_CODES),
            "review_state": "completed", "plan_discarded": True,
            "paths": ["a.py"], "path_count": 1, "held_count": 2, "error_count": 3,
        }
        assert not _has_hangul(review_messages.render_turn_message(code, params, locale))
    assert not _has_hangul(review_messages.render_turn_message(None, None, locale, "failed"))


def test_an_empty_path_list_is_a_translated_word_not_a_stored_string():
    ko = review_messages.render_turn_message(
        "review_apply_re_review", {"paths": [], "path_count": 0, "held_count": 0}, "ko",
    )
    en = review_messages.render_turn_message(
        "review_apply_re_review", {"paths": [], "path_count": 0, "held_count": 0}, "en",
    )
    assert "(없음)" in ko
    assert "(none)" in en


# ── review finding — schema validation was incomplete, exact fallback assertions ──

@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_apply_re_review_with_empty_params_falls_back_to_generic_exactly(locale):
    # Reproduced by the review: `{}` used to report "changes were applied" with an empty
    # path list (missing `paths`/`path_count` were silently treated as an empty array)
    # instead of the generic fallback. It must now render EXACTLY the generic sentence.
    expected = review_messages.render_turn_message(None, None, locale, "accepted")
    actual = review_messages.render_turn_message("review_apply_re_review", {}, locale, "accepted")
    assert actual == expected


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_apply_re_review_with_null_paths_and_mismatched_count_falls_back_to_generic_exactly(locale):
    # `{paths: null, path_count: 1, held_count: 0}`: `path_count` claims one changed file
    # while `paths` is missing entirely -- an inconsistent shape, not "no paths".
    expected = review_messages.render_turn_message(None, None, locale, "accepted")
    actual = review_messages.render_turn_message(
        "review_apply_re_review",
        {"paths": None, "path_count": 1, "held_count": 0},
        locale, "accepted",
    )
    assert actual == expected


def test_apply_re_review_rejects_a_path_count_paths_mismatch():
    # `paths` is well-formed but `path_count` disagrees with its length -- the schema
    # requires exact consistency, not "count clamped to len(paths)".
    expected = review_messages.render_turn_message(None, None, "en", "accepted")
    actual = review_messages.render_turn_message(
        "review_apply_re_review",
        {"paths": ["a.py"], "path_count": 2, "held_count": 0},
        "en", "accepted",
    )
    assert actual == expected


@pytest.mark.parametrize("code,params", [
    ("review_apply_held_only", {"held_count": -1}),
    ("review_apply_failed", {"error_count": -1}),
    ("review_apply_failed", {"error_count": 1.5}),
    ("review_apply_failed", {"error_count": 1, "held_count": -1}),
    ("review_apply_re_review", {"paths": ["a.py"], "path_count": 1, "held_count": -1}),
    ("review_apply_re_review", {"paths": ["a.py"], "path_count": 1.0, "held_count": 0}),
])
def test_negative_and_fractional_counts_fall_back_to_generic_exactly(code, params):
    # Counts accepted negative values before this fix (client also accepted fractions);
    # a count outside its valid domain invalidates the whole code's params.
    expected = review_messages.render_turn_message(None, None, "en", "failed")
    actual = review_messages.render_turn_message(code, params, "en", "failed")
    assert actual == expected


def test_stale_run_rejects_a_non_boolean_plan_discarded():
    # A `plan_discarded` present but not a bool used to be silently treated as falsy
    # (no discard clause appended) instead of invalidating the shape.
    expected = review_messages.render_turn_message(None, None, "en", "stale_run")
    actual = review_messages.render_turn_message(
        "review_stale_run",
        {"changed": ["candidate_refrozen"], "plan_discarded": "yes"},
        "en", "stale_run",
    )
    assert actual == expected


def test_generic_status_is_not_a_registered_inbound_code():
    # `message_code: "generic_status"` is the fallback TEMPLATE key, not a stored code --
    # treating it as registered returned the literal, unformatted "'{status}' 상태로
    # 끝났습니다" placeholder instead of falling back.
    text = review_messages.render_turn_message("generic_status", {}, "ko", "accepted")
    assert "{status}" not in text
    assert text == review_messages.render_turn_message(None, {}, "ko", "accepted")


# ── review finding — status/review_state are identifiers, not copy ────────────────

def test_generic_status_localizes_the_status_word_per_locale():
    ko = review_messages.render_turn_message(None, None, "ko", "failed")
    en = review_messages.render_turn_message(None, None, "en", "failed")
    ja = review_messages.render_turn_message(None, None, "ja", "failed")
    assert "실패" in ko and "failed" not in ko
    assert "failed" in en
    assert "失敗" in ja and "failed" not in ja


def test_generic_status_of_an_unknown_status_uses_a_localized_fallback_word():
    ko = review_messages.render_turn_message(None, None, "ko", "some_future_status")
    en = review_messages.render_turn_message(None, None, "en", "some_future_status")
    assert "some_future_status" not in ko and "알 수 없는" in ko
    assert "some_future_status" not in en and "unknown" in en


def test_settled_approval_localizes_the_review_state_word_per_locale():
    # Review finding: "ja generic output contains failed" / "settled-approval output
    # contains completed" -- `review_state` used to be interpolated raw.
    ko = review_messages.render_turn_message(
        "review_stale_run", {"changed": ["approval_settled"], "review_state": "completed"},
        "ko", "stale_run",
    )
    en = review_messages.render_turn_message(
        "review_stale_run", {"changed": ["approval_settled"], "review_state": "completed"},
        "en", "stale_run",
    )
    ja = review_messages.render_turn_message(
        "review_stale_run", {"changed": ["approval_settled"], "review_state": "completed"},
        "ja", "stale_run",
    )
    assert "완료" in ko and "completed" not in ko
    assert "completed" in en
    assert "完了" in ja and "completed" not in ja and not _has_hangul(ja)


def test_settled_approval_of_an_unknown_review_state_uses_a_localized_fallback_word():
    en = review_messages.render_turn_message(
        "review_stale_run", {"changed": ["approval_settled"], "review_state": "some_new_state"},
        "en", "stale_run",
    )
    assert "some_new_state" not in en and "unknown" in en


# ── review finding — rendered notices must be bounded like the old stored message ──

def test_the_history_bounds_a_large_rendered_notice_like_the_old_message_was(monkeypatch):
    # T0006 §4-5: only `message`/`body` was ever bounded by
    # `_REVIEW_CONVERSATION_MAX_TURN_CHARS`; the rendered notice was appended unbounded,
    # so a large write plan's `paths` (interpolated verbatim) produced an unbounded line
    # where the former stored sentence would have been truncated.
    from modules.flow_gate.api import token_routes

    many_paths = [f"server/modules/flow_gate/very/long/generated/path/file_{i:04d}.py"
                  for i in range(200)]
    conversation = [
        _coded("big", "review_apply_re_review",
               {"paths": many_paths, "path_count": len(many_paths), "held_count": 0},
               status="accepted"),
    ]

    _mention, body = _history(monkeypatch, conversation, "en")

    notice_line = next(line for line in body.splitlines() if line.startswith("[1]"))
    assert "...(truncated)" in notice_line
    assert len(notice_line) < token_routes._REVIEW_CONVERSATION_MAX_TURN_CHARS + 100


def test_the_history_still_carries_a_separate_bounded_answer_line(monkeypatch):
    # The truncation on the notice must not swallow the original-content line: they stay
    # two separate, independently bounded lines.
    from modules.flow_gate.api import token_routes

    long_answer = "고쳤습니다. " * 500
    conversation = [
        _coded("stale", "review_stale_run",
               {"changed": ["candidate_refrozen"], "plan_discarded": False},
               message=long_answer, status="stale_run"),
    ]

    _mention, body = _history(monkeypatch, conversation, "en")

    answer_line = next(line for line in body.splitlines() if line.strip().startswith("answer:"))
    assert "...(truncated)" in answer_line
    assert len(answer_line) < token_routes._REVIEW_CONVERSATION_MAX_TURN_CHARS + 100
