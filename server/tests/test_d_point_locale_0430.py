"""T0009 work item 4 — locale-branch regression for the D coordinates (NR0008 §3.1, nine of them).

Each function below moved from a locale-free hardcoded ko literal to a ko/en/ja
dictionary. These tests assert, function by function: (1) the ko branch is
byte-identical to the original hardcode (no regression for existing ko-asserting
suites), and (2) en/ja never leak a Korean syllable.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

_HANGUL_RANGE = range(0xAC00, 0xD7A4)


def _assert_no_korean(text: str) -> None:
    leaked = [ch for ch in text if ord(ch) in _HANGUL_RANGE]
    assert not leaked, f"Korean syllable(s) leaked: {leaked!r} in {text!r}"


# ── tr_scope_service.parse_reported_files / _normalize_reported_path ─────────


def test_tr_scope_format_errors_ko_unchanged():
    from modules.flow_gate.services import tr_scope_service as trs

    many_items = "".join(f"- a/b{i}.py\n" for i in range(250))
    body = '## 변경 파일\n\n- ""\n- this is prose not a path\n' + many_items
    parsed = trs.parse_reported_files(body, "ko")
    assert any(err == "빈 항목" for err in parsed.format_errors)
    assert any(err.startswith("경로가 아니라 설명으로 보임:") for err in parsed.format_errors)
    assert any(err.startswith("항목이 ") and "최대 200개까지 받습니다." in err for err in parsed.format_errors)


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_tr_scope_format_errors_have_zero_korean(locale):
    from modules.flow_gate.services import tr_scope_service as trs

    body = "## 변경 파일\n\n \n- \n- not/a/valid path with spaces\nnot a list line\n" + "- a/b.py\n" * 250
    parsed = trs.parse_reported_files(body, locale)
    assert parsed.format_errors
    for err in parsed.format_errors:
        _assert_no_korean(err)


# ── conversation_turn_service._encoding_violation ─────────────────────────────


def test_encoding_violation_ko_unchanged():
    from modules.flow_gate.services import conversation_turn_service as cts

    msg = cts._encoding_violation(
        body_raw="hello", body_sha256="deadbeef", body_chars=None,
        force_encoding_reason=None, locale="ko",
    )
    assert msg is not None and msg.startswith("본문 지문이 어긋납니다:")


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_encoding_violation_has_zero_korean(locale):
    from modules.flow_gate.services import conversation_turn_service as cts

    fingerprint_msg = cts._encoding_violation(
        body_raw="hello", body_sha256="deadbeef", body_chars=999,
        force_encoding_reason=None, locale=locale,
    )
    _assert_no_korean(fingerprint_msg)

    corrupted_msg = cts._encoding_violation(
        body_raw="??????\n??????\n??????\n", body_sha256=None, body_chars=None,
        force_encoding_reason=None, locale=locale,
    )
    assert corrupted_msg is not None
    _assert_no_korean(corrupted_msg)


# ── workflow_decision_service.corrupted_label_message ─────────────────────────


def test_corrupted_label_message_ko_unchanged():
    from modules.flow_gate.services import workflow_decision_service as wfd

    msg = wfd.corrupted_label_message("??? label", "ko")
    assert msg.startswith("단계 이름이 깨진 글자(예: ??????)로 보입니다:")


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_corrupted_label_message_has_zero_korean(locale):
    from modules.flow_gate.services import workflow_decision_service as wfd

    _assert_no_korean(wfd.corrupted_label_message("??? label", locale))


# ── work_plan_service.change_summary / _quantities_line ───────────────────────


def test_work_plan_change_summary_ko_unchanged():
    from modules.flow_gate.services import work_plan_service as wps

    after = {
        "counted_types": ["T"], "quantities": {"T": {"count": 3}},
        "steps": [{"key": "s1", "provider_id": "p1", "note": "new"}],
    }
    before = {
        "quantities": {"T": {"count": 3}},
        "steps": [{"key": "s1", "provider_id": "p1", "note": "old"}],
    }
    summary = wps.change_summary(after, before, "ko")
    assert summary["quantities"] == "T 3세트"
    assert "steps[s1].note 변경" in summary["changed"]


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_work_plan_change_summary_has_zero_korean(locale):
    from modules.flow_gate.services import work_plan_service as wps

    after = {
        "counted_types": ["T"], "quantities": {"T": {"count": 3}},
        "steps": [{"key": "s1", "provider_id": "p1", "note": "new"}],
    }
    before = {
        "quantities": {"T": {"count": 2}},
        "steps": [{"key": "s1", "provider_id": "p1", "note": "old"}],
    }
    summary = wps.change_summary(after, before, locale)
    _assert_no_korean(summary["quantities"])
    for line in summary["changed"]:
        _assert_no_korean(line)


# ── pipeline_service approval-rejection messages ───────────────────────────────


def test_pipeline_approval_messages_ko_unchanged():
    from modules.flow_gate.workflow import pipeline_service as pipe

    assert pipe._empty_body_approval_message("ko") == (
        "본문이 비어 있어 승인할 수 없습니다. 문서 내용을 채운 뒤 다시 승인하십시오."
    )
    assert pipe._fileless_approval_structure_message("ko") == (
        "최종 승인이 현재 워크플로 헤드가 아니어서 승인할 수 없습니다."
    )


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_pipeline_approval_messages_have_zero_korean(locale):
    from modules.flow_gate.workflow import pipeline_service as pipe

    _assert_no_korean(pipe._empty_body_approval_message(locale))
    _assert_no_korean(pipe._fileless_approval_structure_message(locale))


# ── test_command_service prep-step label (corrected to English, not localized) ─


def test_reflect_prep_step_label_is_english_not_korean():
    """T0009 work item 4: this module's description literals are English-only by design
    (module docstring, L §2-5) — the stray Korean prep-step literal was corrected to match
    the existing convention, not given a ko/en/ja dictionary (see the code comment
    at the fix site and TR for the full rationale)."""
    import inspect

    from modules.flow_gate.services import test_command_service as tcs

    source = inspect.getsource(tcs.reflect_from_passed_run)
    _assert_no_korean(source)
    assert "Prep step (" in source


# ── test_run_service TSR report body ──────────────────────────────────────────


def _tsr_fixture():
    doc = {
        "doc_id": "sample.none.0001.0002-TS", "title": "Sample TS",
        "project_id": "sample", "branch": "main",
    }
    run = {
        "run_id": "run1", "revision_no": 1, "started_at": "2026-08-17T00:00:00",
        "port": 8123, "source_root": "C:/worktree", "source_root_kind": "worktree",
    }
    cases = [
        {"kind": "setup", "case_no": "S1", "cmd": "echo hi", "result": None, "duration_ms": 1000},
        {"kind": "service", "case_no": "S2", "cmd": "run svc", "result": None, "duration_ms": 500},
        {
            "kind": "case", "case_no": "C1", "case_title": "does a thing", "result": "pass",
            "exit_code": 0, "duration_ms": 200, "output_tail": "ok",
        },
        {"kind": "teardown", "case_no": "T1", "cmd": "cleanup", "result": None, "duration_ms": 100},
    ]
    return doc, run, cases


def test_tsr_content_ko_unchanged():
    from modules.flow_gate.services import test_run_service as trs

    doc, run, cases = _tsr_fixture()
    content = trs._tsr_content(doc, run, cases, "제목", "ko")
    assert "## 실행 환경" in content
    assert "## 준비 / 정리" in content
    assert "## 케이스별 결과" in content
    assert "## 케이스별 출력 발췌" in content
    assert "그룹 워크트리 `C:/worktree` — 작업 브랜치에서 실행됨" in content


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_tsr_content_has_zero_korean(locale):
    from modules.flow_gate.services import test_run_service as trs

    doc, run, cases = _tsr_fixture()
    content = trs._tsr_content(doc, run, cases, f"title-{locale}", locale)
    _assert_no_korean(content)


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_src_root_label_reason_branch_has_zero_korean(locale):
    from modules.flow_gate.services import test_run_service as trs

    run = {"source_root": "C:/base", "source_root_kind": "git_integration_off"}
    _assert_no_korean(trs._src_root_label(run, locale))

    run_no_record = {"source_root": None, "source_root_kind": None}
    _assert_no_korean(trs._src_root_label(run_no_record, locale))


# ── Caller wiring: the surfaces that actually select a locale branch ───────────
# A locale dictionary with no caller passing a locale is a dictionary nobody reads.
# These two tests pin the hops, one per surface: the human approve/reject route and
# the unmanned worker's auto-approve.


def _fileless_ac_with_a_remaining_head(monkeypatch):
    """Mocks for the cheapest approval rejection that reaches a localized message:
    a file-less AC whose group still has a real workflow head (pipeline_service
    raises the "not the current workflow head" message). Mirrors the fixture in
    tests/test_empty_body_approval_guard_0374.py so both suites reject for the same
    structural reason."""
    from unittest.mock import MagicMock

    from modules.flow_gate.workflow import pipeline_service as ps
    from modules.flow_gate.workflow.routers import workflow

    ac = {
        "id": 8,
        "doc_id": "flowgate.default.0430.9008-AC",
        "project_id": "flowgate",
        "group_id": "flowgate.default.0430",
        "type_code": "AC",
        "target_id": "flowgate.default.0430.0001-B",
        "seq": 8,
        "file_path": None,
        "doc_review_status": "pending_review",
    }
    root = {
        "doc_id": ac["target_id"],
        "type_code": "B",
        "doc_review_status": "approved",
    }
    docs = MagicMock()
    docs.get_by_id.side_effect = lambda doc_id: ac if doc_id == ac["doc_id"] else root
    docs.list_documents.return_value = [root, ac]
    monkeypatch.setattr(ps, "db_docs", docs)
    monkeypatch.setattr(workflow, "db_docs", docs)
    monkeypatch.setattr(ps, "log_state_changed", MagicMock())
    monkeypatch.setattr(ps.db_wfseq, "get_sequence_by_doc_id", lambda _root_id: {"id": 10})
    monkeypatch.setattr(
        ps.db_wfseq, "get_effective_head", lambda _sequence_id: {"id": 77, "type": "TR"}
    )
    monkeypatch.setattr(workflow.process_service, "is_group_disposed", lambda _gid: False)
    monkeypatch.setattr(workflow, "_guard_group_not_ai_running", lambda _doc, _doc_id: None)
    return ac, docs


class _FakeRequest:
    """Minimal stand-in for Starlette's Request — the route only reads a header."""

    def __init__(self, headers: dict):
        self.headers = headers


@pytest.mark.parametrize(
    "headers, expect_korean",
    [
        ({}, True),
        ({"x-locale": "ko"}, True),
        ({"x-locale": "en"}, False),
        ({"x-locale": "ja"}, False),
    ],
    ids=["no-header-stays-ko", "explicit-ko", "en", "ja"],
)
def test_review_transition_route_passes_x_locale_into_the_rejection(
    monkeypatch, headers, expect_korean
):
    """The human approve path: POST .../review_transitions/approve must render the
    approval-rejection message in the requester's x-locale, and must still answer in
    Korean when the header is absent (unchanged pre-T0009 behavior)."""
    import asyncio

    from fastapi import HTTPException

    from modules.flow_gate.workflow.routers import workflow

    ac, docs = _fileless_ac_with_a_remaining_head(monkeypatch)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            workflow.document_review_transition_endpoint(
                ac["doc_id"],
                "approve",
                workflow.DocumentTransitionRequest(),
                {"user_id": "reviewer", "is_admin": True},
                _FakeRequest(headers),
            )
        )

    assert excinfo.value.status_code == 409
    detail = str(excinfo.value.detail)
    if expect_korean:
        assert detail == "최종 승인이 현재 워크플로 헤드가 아니어서 승인할 수 없습니다."
    else:
        _assert_no_korean(detail)
        assert detail  # a localized message, not an empty string
    docs.update.assert_not_called()


def test_review_transition_route_still_works_without_a_request():
    """The git-action RPC and tests/test_empty_body_approval_guard_0374.py call this
    endpoint directly with no Request. The locale hop must not make `request`
    mandatory — a missing one means "ko", not a TypeError."""
    import inspect

    from modules.flow_gate.workflow.routers import workflow

    parameter = inspect.signature(
        workflow.document_review_transition_endpoint
    ).parameters["request"]
    assert parameter.default is None


def test_worker_auto_approve_passes_the_tokens_continuation_locale():
    """The unmanned path: inbox_routes._continuation_self_chain hands an auto-approve
    failure back to the worker in envelope["continuation_reason"], so it must select the
    branch with the token's own continuation_locale. AST-level wiring assertion (the
    surrounding function needs a consumed continuation token, a live workflow head and a
    real group to reach behaviorally); the message rendering itself is covered by
    test_pipeline_approval_messages_have_zero_korean above."""
    import ast
    import inspect

    from modules.flow_gate.api import inbox_routes

    tree = ast.parse(inspect.getsource(inbox_routes._continuation_self_chain))
    approve_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "transition_document_review"
        and any(
            keyword.arg == "action"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == "approve"
            for keyword in node.keywords
        )
    ]
    assert approve_calls, "the auto-approve call site disappeared — re-point this test"
    for call in approve_calls:
        locale_kwargs = [kw for kw in call.keywords if kw.arg == "locale"]
        assert locale_kwargs, "auto-approve must pass a locale"
        rendered = ast.dump(locale_kwargs[0].value)
        assert "continuation_locale" in rendered, rendered
        assert "normalize_locale" in rendered, rendered
