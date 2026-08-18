"""Regression coverage for group 0437 continuous-work and CH-reference prompts."""
from __future__ import annotations

import pytest

from modules.flow_gate.services import mention_service


_BASE = "http://host/flowgate/api/v1"
_CH_WITH_TURNS = "verify.default.0437.0001-CH"
_CH_EMPTY = "verify.default.0437.0002-CH"
_NON_CH = "verify.default.0437.0003-T"


@pytest.fixture(autouse=True)
def _reference_data(monkeypatch):
    doc_types = {
        _CH_WITH_TURNS: "CH",
        _CH_EMPTY: "CH",
        _NON_CH: "T",
    }
    turns = {
        _CH_WITH_TURNS: [
            {
                "seq": 1,
                "display_name": "Alice",
                "created_at": "2026-08-18T00:00:00+09:00",
                "body": "hello there",
            },
            {
                "seq": 2,
                "display_name": "AI",
                "created_at": "2026-08-18T00:00:01+09:00",
                "body": "general kenobi",
            },
        ],
        _CH_EMPTY: [],
    }

    monkeypatch.setattr(
        mention_service.document_service,
        "get_document",
        lambda doc_id: {"doc_id": doc_id, "type_code": doc_types[doc_id]},
    )
    monkeypatch.setattr(
        mention_service.conversation_query_service,
        "_ensure_readable_rows",
        lambda _doc_id: None,
    )
    monkeypatch.setattr(
        mention_service.turn_store,
        "list_turns",
        lambda doc_id: turns[doc_id],
    )
    monkeypatch.setattr(mention_service, "turn_wire", lambda row: row)
    monkeypatch.setattr(
        mention_service, "_include_remote_source_crud", lambda _project: False
    )


def _build_new(*, locale="ko", continuous=False, ref_doc_ids=None):
    return mention_service.build_mention(
        project="verify",
        module="default",
        group="0437",
        parent_type="T",
        parent_doc_number="T0005",
        parent_title="instruction",
        parent_doc_id="B0001",
        head_type="TR",
        head_status="pending",
        scratch_dir="S",
        raw_token="RAW",
        api_base_url=_BASE,
        locale=locale,
        continuous=continuous,
        ref_doc_ids=ref_doc_ids,
    )


def _build_review(*, ref_doc_ids=None):
    return mention_service.build_review_mention(
        token_rec={
            "project": "verify",
            "group_id": "verify.default.0437",
            "scratch_dir": "S",
        },
        target_doc={
            "doc_id": _NON_CH,
            "type_code": "T",
            "seq": 3,
            "title": "instruction",
            "module": "default",
            "project_id": "verify",
        },
        api_base_url=_BASE,
        raw_token="RAW",
        locale="ko",
        ref_doc_ids=ref_doc_ids,
    )


def _reference_section(text: str) -> str:
    return text.split("## Reference documents\n---\n", 1)[1].split("\n\n## ", 1)[0]


@pytest.mark.parametrize(
    ("locale", "required"),
    [
        ("ko", "동기적으로(synchronously) 끝까지 실행"),
        ("ja", "(synchronously)最後まで実行"),
        ("en", "completion synchronously within the current process"),
    ],
)
def test_continuous_prompt_requires_synchronous_verification(locale, required):
    guide = mention_service._continuous_guide_body(locale)
    rendered = _build_new(locale=locale, continuous=True)

    assert guide.count("\n- ") == 5
    assert "background" in guide
    assert required in guide
    assert guide in rendered


def _assert_reference_behavior(section: str) -> None:
    with_turns_link = f"{_CH_WITH_TURNS}: GET {_BASE}/document/{_CH_WITH_TURNS}"
    empty_link = f"{_CH_EMPTY}: GET {_BASE}/document/{_CH_EMPTY}"
    non_ch_link = f"{_NON_CH}: GET {_BASE}/document/{_NON_CH}"

    assert with_turns_link in section
    assert "--- turn 1 [Alice] 2026-08-18T00:00:00+09:00 ---" in section
    assert "hello there" in section
    assert "--- turn 2 [AI] 2026-08-18T00:00:01+09:00 ---" in section
    assert "general kenobi" in section
    assert empty_link in section
    assert "(이 CH 문서에는 아직 턴이 없습니다)" in section
    assert section.count(non_ch_link) == 1


def test_build_mention_inlines_ch_turns_and_preserves_non_ch_link():
    output = _build_new(ref_doc_ids=[_CH_WITH_TURNS, _CH_EMPTY, _NON_CH])
    _assert_reference_behavior(_reference_section(output))


def test_build_review_mention_inlines_ch_turns_and_preserves_target_link():
    output = _build_review(ref_doc_ids=[_CH_WITH_TURNS, _CH_EMPTY])
    _assert_reference_behavior(_reference_section(output))