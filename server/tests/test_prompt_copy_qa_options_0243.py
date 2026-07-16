"""Worker ment Q&A block rendering with query options (group 0243 L0008 §2.5).

The ment is what tells a worker which options were offered and what came back. An
unanswered query lists its options WITH their ids, because a worker answering through the
ai-request path has to echo an id back in selected_option_ids — an id-less list would leave
it guessing. An answered one gets no such line: the pick is already in the answer body as
its label.
"""
from __future__ import annotations

import os
from unittest.mock import patch

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

DOC = "p.none.0001.0001-D"


def _render(rows: list[dict]) -> list[str]:
    from modules.flow_gate.workflow import prompt_copy_service
    from modules.flow_gate.services import q_service

    lines: list[str] = []
    with patch.object(q_service, "qa_bundle_by_doc", return_value=rows):
        prompt_copy_service._append_qa_block(lines, DOC)
    return lines


def test_unanswered_query_lists_options_with_ids():
    lines = _render([{
        "seq": 1, "title": "배포 방식", "body": "어느 쪽?", "asker_kind": "ai",
        "options": '[{"id":"o1","label":"무중단 배포"},{"id":"o2","label":"점검창 배포"}]',
        "author_kind": None, "answer_body": None, "answer_selected_options": None,
    }])

    assert "- [답변중]   Q1 배포 방식            (미정)" in lines
    assert "    보기: [o1] 무중단 배포 / [o2] 점검창 배포" in lines


def test_answered_query_has_no_options_line():
    lines = _render([{
        "seq": 1, "title": "배포 방식", "body": "어느 쪽?", "asker_kind": "ai",
        "options": '[{"id":"o1","label":"무중단 배포"},{"id":"o2","label":"점검창 배포"}]',
        "author_kind": "human", "answer_body": "점검창 배포",
        "answer_selected_options": '["o2"]',
    }])

    assert "- [답변완료] Q1 배포 방식" in lines
    assert "    답: 점검창 배포" in lines
    assert not any("보기:" in ln for ln in lines)
    # 기계 판독 값은 멘트에 노출하지 않는다 — 사람 판독 형식을 유지한다
    assert not any("o2" in ln for ln in lines)


def test_query_without_options_renders_pre_extension_format():
    lines = _render([{
        "seq": 1, "title": "범위", "body": "scope?", "asker_kind": "ai",
        "options": "[]", "author_kind": None, "answer_body": None,
        "answer_selected_options": "[]",
    }])

    assert lines == ["", "## 사용자 질의응답", "- [답변중]   Q1 범위            (미정)"]


def test_malformed_options_fall_back_to_plain_line():
    """파싱 실패(정상 경로에선 발생 불가)가 멘트 조립을 죽여서는 안 된다 (L0008 §5)."""
    lines = _render([{
        "seq": 1, "title": "범위", "body": "scope?", "asker_kind": "ai",
        "options": "{ not json", "author_kind": None, "answer_body": None,
        "answer_selected_options": None,
    }])

    assert "- [답변중]   Q1 범위            (미정)" in lines
    assert not any("보기:" in ln for ln in lines)
