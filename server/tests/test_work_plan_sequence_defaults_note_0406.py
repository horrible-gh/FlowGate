"""0406 T0009 — defaults.note fallback and note-source priority."""
from __future__ import annotations

import pytest

from modules.flow_gate.services import work_plan_sequence_service as wpseq

WP_DOC_ID = "flowgate.default.0406.0004-WP"
OWNER_DOC_ID = "flowgate.default.0406.0001-B"
REVISION = 9


def _rows(plan: dict):
    return wpseq.plan_to_rows(plan, WP_DOC_ID, REVISION)


@pytest.fixture
def empty_workflow(monkeypatch):
    monkeypatch.setattr(wpseq.db_wfseq, "get_sequence_by_doc_id", lambda _doc_id: None)
    monkeypatch.setattr(wpseq.db_wfseq, "get_sequence_items", lambda _sequence_id: [])
    monkeypatch.setattr(wpseq.db_wfseq, "get_item_by_result_doc_id", lambda _doc_id: None)


def _candidate(plan: dict):
    return wpseq.build_candidates(
        doc={"doc_id": WP_DOC_ID, "target_id": OWNER_DOC_ID, "revision_no": REVISION},
        plan=plan,
        mode="append",
    )


def test_defaults_only_fills_every_placeable_plan_row_with_normalized_shared_note():
    plan = {
        "defaults": {"note": "  Shared\x00 note\n  "},
        "steps": [
            {"key": "D#1", "type": "D", "note": ""},
            {"key": "L#1", "type": "L", "note": "   "},
            {"key": "T#1", "type": "T", "note": None},
        ],
    }
    rows, dropped, _ = _rows(plan)

    assert dropped == []
    assert len(rows) == 3
    assert {row["note"] for row in rows} == {"Shared note"}
    assert {row["note_source"] for row in rows} == {"defaults"}
    assert {row["origin"] for row in rows} == {"plan"}
    assert {row["source_doc_id"] for row in rows} == {WP_DOC_ID}
    assert {row["source_revision_no"] for row in rows} == {REVISION}


def test_step_note_has_priority_over_defaults_note():
    rows, _, _ = _rows({
        "defaults": {"note": "shared"},
        "steps": [{"key": "D#1", "type": "D", "note": "specific"}],
    })

    assert [(row["note"], row["note_source"]) for row in rows] == [("specific", "step")]


def test_pair_note_goes_to_the_result_row_and_defaults_still_fill_the_instruction():
    """0408 M0019 재반려 2 — 결과 단계의 멘트는 결과 줄에 남고, 지시 줄은 공통 멘트를 받는다."""
    rows, dropped, uid = _rows({
        "defaults": {"note": "shared"},
        "steps": [
            {"key": "T#1", "type": "T", "pair_key": "TR#1", "note": ""},
            {"key": "TR#1", "type": "TR", "pair_key": "T#1", "note": "paired"},
        ],
    })

    assert [(row["note"], row["note_source"]) for row in rows] == [("shared", "defaults")]
    assert "paired_note_dropped" not in {item["reason"] for item in dropped}
    attached, _ = wpseq.attach_auto_rows(rows, next_uid=uid)
    assert [(row["type"], row["note"], row["note_source"]) for row in attached] == [
        ("T", "shared", "defaults"),
        ("TR", "paired", "step"),
    ]


def test_both_steps_of_a_pair_keep_their_own_note_and_nothing_is_dropped():
    rows, dropped, uid = _rows({
        "defaults": {"note": "shared"},
        "steps": [
            {"key": "T#1", "type": "T", "pair_key": "TR#1", "note": "specific"},
            {"key": "TR#1", "type": "TR", "pair_key": "T#1", "note": "paired"},
        ],
    })

    assert [(row["note"], row["note_source"]) for row in rows] == [("specific", "step")]
    assert dropped == []
    attached, _ = wpseq.attach_auto_rows(rows, next_uid=uid)
    assert [(row["type"], row["note"], row["note_source"]) for row in attached] == [
        ("T", "specific", "step"),
        ("TR", "paired", "step"),
    ]


def test_attached_auto_row_takes_the_common_note_and_server_assembled_note_is_still_dropped():
    """The common note reaches the report row too — under [자동 승인] that is the running row."""
    rows, dropped, uid = _rows({
        "defaults": {"note": "shared"},
        "steps": [
            {"key": "T#1", "type": "T", "note": ""},
            {"key": "TSR#1", "type": "TSR", "note": "server-only"},
        ],
    })
    attached, _ = wpseq.attach_auto_rows(rows, next_uid=uid)

    assert [(row["type"], row["note"], row["note_source"]) for row in attached] == [
        ("T", "shared", "defaults"),
        ("TR", "shared", "defaults"),
    ]
    assert [item["reason"] for item in dropped] == ["server_assembled_note"]


@pytest.mark.parametrize("defaults", [None, "not-a-dict", {"note": " \t\n "}])
def test_missing_invalid_or_blank_defaults_preserves_empty_note_and_note_missing(
    empty_workflow, defaults,
):
    plan = {"steps": [{"key": "D#1", "type": "D", "note": ""}]}
    if defaults is not None:
        plan["defaults"] = defaults

    rows, _, _ = _rows(plan)
    result = _candidate(plan)

    assert rows[0]["note"] == ""
    assert rows[0]["note_source"] is None
    assert "note_missing" in {item["code"] for item in result["notifications"]}


def test_shared_note_reuses_normalization_and_control_character_removal():
    # 0406 T0022 작업 6: 후보 생성은 표시 경로다 — 자르지 않고 그대로 나른다. 상한을 넘긴
    # 값은 [저장]에서 note_too_long 으로 거절되며, 그 판정은 저장 경로가 한다.
    body = "가" * (wpseq.NOTE_MAX_CHARS + 50)
    rows, _, _ = _rows({
        "defaults": {"note": " \x00" + body + "\n"},
        "steps": [{"key": "D#1", "type": "D", "note": ""}],
    })

    assert rows[0]["note"] == body
    assert rows[0]["note_source"] == "defaults"


def test_candidates_publish_note_source_keep_the_row_contract_and_clear_note_missing(empty_workflow):
    result = _candidate({
        "defaults": {"note": "shared"},
        "steps": [{"key": "D#1", "type": "D", "note": ""}],
    })
    row = result["rows"][0]

    assert "note_missing" not in {item["code"] for item in result["notifications"]}
    assert row["note"] == "shared"
    assert row["note_source"] == "defaults"
    assert set(row) == {
        "type", "label", "status", "locked", "poured", "note", "note_source",
        "origin", "plan_key", "source_doc_id", "source_revision_no",
        "provider_id", "provider_display_name", "provider_registered",
    }