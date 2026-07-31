"""0370 T0012 — expose bounded document queries in every worker mention."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import invoke_mention_service  # noqa: E402
from modules.flow_gate.services import mention_service  # noqa: E402
from modules.flow_gate.services import q_answer_invoke_service  # noqa: E402

BASE = "http://host/flowgate/api/v1"
PROJECT = "flowgate"
GROUP_ID = "flowgate.default.0370"
DOC_ID = "flowgate.default.0370.0001-R"
TOKEN = "worker-token"


def _assert_lookup(text: str, doc_id: str = DOC_ID) -> None:
    assert "Efficient document lookup" in text or "[문서 조회 도구]" in text or "Document search and lookup rules" in text
    assert f"/document/{doc_id}/meta" in text
    assert f"/document/{doc_id}/outline" in text
    assert f"/document/{doc_id}/section?section_id=<section_id>" in text
    assert f"/document/{doc_id}/relations" in text
    assert "/search/documents/content?q=<keyword>&project=flowgate" in text
    assert "include_matches=true&context_lines=2&hits_per_doc=5" in text
    assert f"Authorization: Bearer {TOKEN}" in text


def test_document_creation_edit_and_workflow_mentions_expose_bounded_queries(monkeypatch):
    monkeypatch.setattr(mention_service, "_include_remote_source_crud", lambda _project: False)
    monkeypatch.setattr(mention_service.template_provision, "is_design_type", lambda _type: False)

    ordinary = mention_service.build_mention(
        project=PROJECT,
        module="default",
        group="0370",
        parent_type="R",
        parent_doc_number="R0001",
        parent_title="Document lookup",
        parent_doc_id=DOC_ID,
        parent_canonical_doc_id=DOC_ID,
        head_type="TR",
        head_status="pending",
        scratch_dir="C:/scratch",
        raw_token=TOKEN,
        api_base_url=BASE,
        group_id=GROUP_ID,
        ref_doc_ids=[DOC_ID],
    )
    assert ordinary is not None
    _assert_lookup(ordinary)
    assert "successful new/edit response includes `change_summary`" in ordinary

    token_rec = {"project": PROJECT, "group_id": GROUP_ID, "scratch_dir": "C:/scratch"}
    target_doc = {
        "doc_id": DOC_ID,
        "type_code": "R",
        "seq": 1,
        "title": "Document lookup",
        "module": "default",
    }
    decision = mention_service.build_workflow_decision_mention(
        token_rec=token_rec,
        target_doc=target_doc,
        api_base_url=BASE,
        raw_token=TOKEN,
    )
    _assert_lookup(decision)

    sequence_edit = mention_service.build_sequence_edit_mention(
        token_rec=token_rec,
        target_doc=target_doc,
        api_base_url=BASE,
        raw_token=TOKEN,
        sequence_items=[],
    )
    _assert_lookup(sequence_edit)

    review = mention_service.build_review_mention(
        token_rec=token_rec,
        target_doc=target_doc,
        api_base_url=BASE,
        raw_token=TOKEN,
    )
    assert review is not None
    _assert_lookup(review)


def test_chat_and_q_answer_mentions_expose_the_same_queries(monkeypatch):
    monkeypatch.setattr(mention_service, "_include_remote_source_crud", lambda _project: False)

    chat_sections = invoke_mention_service._chat_lookup_sections(
        base=BASE,
        raw_token=TOKEN,
        project=PROJECT,
        group_name=GROUP_ID,
    )
    chat_text = "\n".join(chat_sections)
    _assert_lookup(chat_text, "<doc_id>")

    monkeypatch.setattr(q_answer_invoke_service.db_groups, "get_by_id", lambda _gid: None)
    answer = q_answer_invoke_service.build_answer_mention(
        doc={
            "doc_id": DOC_ID,
            "group_id": GROUP_ID,
            "project_id": PROJECT,
            "title": "Document lookup",
        },
        item={"id": 7, "seq": 1, "title": "Question", "body": "What changed?", "options": []},
        raw_token=TOKEN,
        scratch_dir="C:/scratch",
        api_base_url=BASE,
    )
    _assert_lookup(answer)