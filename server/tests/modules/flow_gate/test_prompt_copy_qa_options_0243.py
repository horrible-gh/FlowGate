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

import pytest

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

    assert "- [미답변]   Q1 배포 방식" in lines
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

    assert lines == ["", "## 사용자 질의응답", "- [미답변]   Q1 범위"]


def test_malformed_options_fall_back_to_plain_line():
    """파싱 실패(정상 경로에선 발생 불가)가 멘트 조립을 죽여서는 안 된다 (L0008 §5)."""
    lines = _render([{
        "seq": 1, "title": "범위", "body": "scope?", "asker_kind": "ai",
        "options": "{ not json", "author_kind": None, "answer_body": None,
        "answer_selected_options": None,
    }])

    assert "- [미답변]   Q1 범위" in lines
    assert not any("보기:" in ln for ln in lines)


def _context_graph(monkeypatch):
    from modules.flow_gate.services import q_service
    docs = {
        "tr": {"triggered_by": "b", "target_id": "n"},
        "b": {"target_id": "tr"},  # cycle, and shared ancestor
        "n": {"triggered_by": "b", "target_id": "foreign"},
        "prior": {}, "later": {}, "foreign": {"group_id": "other", "target_id": "secret"},
        "secret": {},
    }
    docs = {key: {"doc_id": key, "project_id": "p", "group_id": "g", **value}
            for key, value in docs.items()}
    monkeypatch.setattr(q_service.db_documents, "get_by_id", docs.get)
    monkeypatch.setattr(q_service.db_wfseq, "get_sequence_for_member_doc", lambda _: None)
    monkeypatch.setattr(q_service, "qa_bundle_by_doc", lambda did: [{
        "id": {"tr": 521, "b": 518, "n": 519, "prior": 520}.get(did, 999),
        "seq": 1, "title": did + " title", "body": did + " full question",
        "answer_body": None if did == "tr" else did + " stored answer", "options": "[]",
    }])
    return q_service


def test_context_chain_keeps_item_ids_sources_cycles_and_unanswered(monkeypatch):
    from modules.flow_gate.workflow.prompt_copy_service import _append_qa_block
    q = _context_graph(monkeypatch)
    rows = q.qa_bundle_for_context("tr")
    assert [(r["doc_id"], r["id"]) for r in rows] == [("tr", 521), ("b", 518), ("n", 519)]
    lines = []
    _append_qa_block(lines, "tr", include_context=True)
    text = "\n".join(lines)
    assert text.count("## 기존 질의·답변") == 1
    assert "[미답변]   Q1 #521" in text
    assert "#518 b title (문서: b)" in text
    assert "b full question" in text and "답: b stored answer" in text
    assert "답: tr" not in text
    assert "foreign" not in text and "secret" not in text


def test_context_only_includes_sequence_predecessors_of_the_target(monkeypatch):
    q = _context_graph(monkeypatch)
    monkeypatch.setattr(q.db_wfseq, "get_sequence_for_member_doc", lambda _: {"id": 7, "doc_id": "b"})
    monkeypatch.setattr(q.db_wfseq, "get_sequence_items", lambda _: [
        {"id": 1, "sort_order": 0, "result_doc_id": "prior"},
        {"id": 2, "sort_order": 1, "result_doc_id": "tr"},
        {"id": 3, "sort_order": 2, "result_doc_id": "later"},
    ])
    ids = q.qa_context_doc_ids("tr")
    assert set(ids) == {"tr", "b", "n", "prior"}
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("target", ["b", "orphan"])
def test_context_root_or_unknown_slot_excludes_later_sequence_results(monkeypatch, target):
    q = _context_graph(monkeypatch)
    docs = {
        key: {"doc_id": key, "project_id": "p", "group_id": "g"}
        for key in ("b", "orphan", "prior", "later")
    }
    monkeypatch.setattr(q.db_documents, "get_by_id", docs.get)
    monkeypatch.setattr(q.db_wfseq, "get_sequence_for_member_doc", lambda _: {"id": 7, "doc_id": "b"})
    items = [
        {"id": 1, "sort_order": 0, "result_doc_id": "prior"},
        {"id": 2, "sort_order": 1, "result_doc_id": "later"},
    ]
    monkeypatch.setattr(q.db_wfseq, "get_sequence_items", lambda _: items)
    head = patch.object(q.db_wfseq, "get_effective_head", return_value=items[1])
    with head as effective_head:
        expected = ["b"] if target == "b" else ["orphan", "b"]
        assert q.qa_context_doc_ids(target) == expected
        assert [row["doc_id"] for row in q.qa_bundle_for_context(target)] == expected
        from modules.flow_gate.workflow.prompt_copy_service import _append_qa_block
        lines = []
        _append_qa_block(lines, target, include_context=True)
        rendered = "\n".join(lines)
        assert "prior title" not in rendered and "later title" not in rendered
        effective_head.assert_not_called()


def test_context_does_not_merge_same_local_sequence_or_drop_multiple_answers(monkeypatch):
    from modules.flow_gate.services import q_service
    from modules.flow_gate.workflow.prompt_copy_service import _append_qa_block
    rows = [
        {"doc_id": "b", "id": 518, "seq": 1, "body": "scope", "answer_body": "first"},
        {"doc_id": "b", "id": 518, "seq": 1, "body": "scope", "answer_body": "second"},
        {"doc_id": "tr", "id": 521, "seq": 1, "body": "pending", "answer_body": None},
    ]
    monkeypatch.setattr(q_service, "qa_bundle_for_context", lambda _: rows)
    lines = []
    _append_qa_block(lines, "tr", include_context=True)
    text = "\n".join(lines)
    assert text.count("#518") == 1 and text.count("#521") == 1
    assert "답: first" in text and "답: second" in text
    assert "[미답변]   Q1 #521 pending" in text
