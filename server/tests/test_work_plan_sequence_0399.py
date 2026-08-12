"""flowgate.default.0399 T0014 set 1 — 저장 구조 · 변환기 · 두 갈래 리스트.

The scenarios below are the ones P0013 wrote out by hand, replayed against the code: the
same example group, the same plan, the same expected rows, counts and notifications. If the
converter ever drifts from that document, these fail with the number that drifted.
"""
from __future__ import annotations

import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "sql" / "migrations" / "sqlite"


# ── DB0012 §3 — migration 079 ────────────────────────────────────────────────

def test_migration_079_adds_the_three_columns_it_claims():
    """Applied for real, one statement at a time, with errors raised rather than printed.

    The shared migration fixture swallows a broken migration and prints a warning, so a
    syntax error in this file would show up as "the column is missing" somewhere far away
    instead of here.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        db_path = handle.name
    conn = sqlite3.connect(db_path)
    try:
        for migration in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            conn.executescript(migration.read_text(encoding="utf-8"))
        columns = {
            row[1]: row for row in conn.execute("PRAGMA table_info(workflow_sequence_items)")
        }
        assert set(columns) >= {"note", "source_doc_id", "source_revision_no"}
        # note is NOT NULL with an empty default: L0011 §2.2 keeps "no value" and "empty
        # text" as one state, so a row can never come back as None.
        assert columns["note"][3] == 1
        assert columns["note"][4] == "''"
        assert columns["source_doc_id"][3] == 0
        assert columns["source_revision_no"][3] == 0

        # A row written before 079 reads as "no plan poured this row", not as a mystery.
        conn.execute(
            "INSERT INTO workflow_sequences (id, doc_id, created_at, updated_at)"
            " VALUES (1, 'flowgate.default.0450.0001-R', 'now', 'now')"
        )
        conn.execute(
            "INSERT INTO workflow_sequence_items"
            " (sequence_id, item_seq, type, label, doc_class, sort_order, created_at, updated_at)"
            " VALUES (1, 1, 'D', '기본설계', 'R', 0, 'now', 'now')"
        )
        row = conn.execute(
            "SELECT note, source_doc_id, source_revision_no FROM workflow_sequence_items"
        ).fetchone()
        assert row == ("", None, None)
    finally:
        conn.close()
        Path(db_path).unlink(missing_ok=True)


def test_the_registered_queries_carry_the_three_columns_both_ways():
    """queries.json is the only place SQL may live (workflow_sequences.py docstring), so
    both the insert and the read have to name the columns there — a save that writes them
    but a read that never selects them loses the note on the very next open."""
    import json

    queries = json.loads(
        (Path(__file__).resolve().parents[1] / "sql" / "queries" / "queries.json")
        .read_text(encoding="utf-8")
    )["workflow_sequences"]
    for column in ("note", "source_doc_id", "source_revision_no"):
        assert column in queries["insert_sequence_item"]
        assert f"wsi.{column}" in queries["get_sequence_items"]
    assert queries["insert_sequence_item"].count("?") == 11

from modules.flow_gate.services import work_plan_apply_service as wpa
from modules.flow_gate.services import work_plan_sequence_service as wpseq
from modules.flow_gate.services import workflow_decision_service as wds


# ── P0013 §0 — the example group every scenario shares ───────────────────────

WP_DOC_ID = "flowgate.default.0450.0002-WP"
OWNER_DOC_ID = "flowgate.default.0450.0001-R"

WP_DOC = {
    "doc_id": WP_DOC_ID,
    "target_id": OWNER_DOC_ID,
    "revision_no": 1,
    "doc_review_status": "approved",
}

SEQUENCE = {"id": 451, "head_advanced_at": None}


def _item(seq, code, label, status, *, note="", result=None, source=None, revision=None):
    return {
        "item_seq": seq, "type": code, "label": label, "status": status,
        "sort_order": seq - 1, "result_doc_id": result, "note": note,
        "source_doc_id": source, "source_revision_no": revision,
    }


ITEMS = [
    _item(1, "R", "요건정의", "done", result="flowgate.default.0450.0001-R"),
    _item(2, "WP", "작업계획", "done", result=WP_DOC_ID),
    _item(3, "DS", "설계지시", "done", note="화면 초안 확인",
          result="flowgate.default.0450.0003-DS"),
    _item(4, "D", "기본설계", "in_progress", result="flowgate.default.0450.0004-D"),
    _item(5, "P", "프로토콜설계", "pending"),
    _item(6, "N", "조사지시", "pending", note="공급자 이슈 확인"),
]

PLAN = {
    "steps": [
        {"key": "P#1", "type": "P", "ordinal": 1, "pair_key": None, "pair_role": "single",
         "note": "레거시 API 호환 확인", "locked": False, "origin": "human"},
        {"key": "L#1", "type": "L", "ordinal": 1, "pair_key": None, "pair_role": "single",
         "note": "", "locked": False, "origin": "human"},
        {"key": "T#1", "type": "T", "ordinal": 1, "pair_key": "TR#1", "pair_role": "instruction",
         "note": "테스트 포함 구현", "locked": False, "origin": "human"},
        {"key": "TR#1", "type": "TR", "ordinal": 1, "pair_key": "T#1", "pair_role": "result",
         "note": "완료 후 확인", "locked": False, "origin": "human"},
    ],
}


@pytest.fixture
def wired(monkeypatch):
    """Point the converter at the example group without touching a database."""
    monkeypatch.setattr(wpseq.db_wfseq, "get_sequence_by_doc_id",
                        lambda doc_id: SEQUENCE if doc_id == OWNER_DOC_ID else None)
    monkeypatch.setattr(wpseq.db_wfseq, "get_sequence_items",
                        lambda seq_id: [dict(it) for it in ITEMS])
    monkeypatch.setattr(wpseq.db_wfseq, "get_item_by_result_doc_id",
                        lambda doc_id: dict(ITEMS[1]) if doc_id == WP_DOC_ID else None)


def codes(result):
    return [n["code"] for n in result["notifications"]]


def notification(result, code):
    return next(n for n in result["notifications"] if n["code"] == code)


# ── P0013 ① [정상 · 뒤에 이어 붙이기] ────────────────────────────────────────

def test_append_reproduces_the_protocol_scenario(wired):
    out = wpseq.build_candidates(doc=WP_DOC, plan=PLAN, mode="append")

    assert out["wp_doc_id"] == WP_DOC_ID
    assert out["workflow_doc_id"] == OWNER_DOC_ID
    # TR#1 makes no row of its own — the dialog attaches the report itself, so counting the
    # plan's result step would produce two of them (L0011 §2.3).
    assert out["plan_step_count"] == 3
    assert [(r["type"], r["poured"]) for r in out["rows"]] == [
        ("R", False), ("WP", False), ("DS", False), ("D", False),
        ("P", False), ("N", False),
        ("P", True), ("L", True), ("T", True), ("TR", True),
    ]
    assert out["row_count_change"] == {"before": 6, "after": 10, "deleted": 0, "added": 4}


def test_append_carries_plan_notes_and_their_origin(wired):
    rows = wpseq.build_candidates(doc=WP_DOC, plan=PLAN, mode="append")["rows"]
    poured = [r for r in rows if r["poured"]]

    assert [(r["plan_key"], r["note"]) for r in poured] == [
        ("P#1", "레거시 API 호환 확인"),
        ("L#1", ""),
        ("T#1", "테스트 포함 구현"),
        (None, "완료 후 확인"),            # the attached report row — TR#1's own note
    ]
    assert {r["source_doc_id"] for r in poured if r["origin"] == "plan"} == {WP_DOC_ID}
    assert {r["source_revision_no"] for r in poured if r["origin"] == "plan"} == {1}
    # 0408 M0019 재반려 2: the automatic row carries the note the plan wrote for TR#1 — the
    # step it IS. It still names no plan step of its own, because no plan step made the row.
    assert poured[-1] == {
        "type": "TR", "label": poured[-1]["label"], "status": "pending", "locked": False,
        "poured": True, "note": "완료 후 확인", "note_source": "step", "origin": "auto",
        "plan_key": None, "source_doc_id": None, "source_revision_no": None,
        "provider_id": None, "provider_display_name": None, "provider_registered": None,
    }


def test_append_notifications_match_the_protocol(wired):
    out = wpseq.build_candidates(doc=WP_DOC, plan=PLAN, mode="append")

    # 0408 M0019 재반려 2: nothing is dropped any more — TR#1's note reached TR#1's row.
    assert codes(out) == ["type_overlap", "note_missing"]
    # A P row survived from before and a P#1 row came in — that is the overlap. TR is an
    # automatic row and never counts (L0011 §2.9).
    assert notification(out, "type_overlap")["types"] == ["P"]
    assert notification(out, "note_missing")["row_indexes"] == [4, 7]


def test_locked_rows_are_not_counted_as_missing_a_note(wired):
    """Row 3 (DS, done) has a note and row 1/2/4 have none — none of them may be reported.

    Telling somebody a finished step has no note asks them to do something there is nothing
    to do about (P0013 ① 주석).
    """
    out = wpseq.build_candidates(doc=WP_DOC, plan=PLAN, mode="append")
    for index in notification(out, "note_missing")["row_indexes"]:
        assert out["rows"][index]["locked"] is False


# ── P0013 ① [정상 · 이후 단계 교체] ──────────────────────────────────────────

def test_replace_after_cuts_the_whole_editable_list(wired):
    out = wpseq.build_candidates(doc=WP_DOC, plan=PLAN, mode="replace_after")

    # The plan's own row (item_seq 2) is locked, so it is not in the editable list and the
    # cut lands at 0 — which makes the whole editable list "the rows after the plan".
    assert [(r["type"], r["locked"]) for r in out["rows"]] == [
        ("R", True), ("WP", True), ("DS", True), ("D", True),
        ("P", False), ("L", False), ("T", False), ("TR", False),
    ]
    assert out["row_count_change"] == {"before": 6, "after": 8, "deleted": 2, "added": 4}


def test_replace_after_reports_only_deleted_rows_that_had_a_note(wired):
    out = wpseq.build_candidates(doc=WP_DOC, plan=PLAN, mode="replace_after")

    assert codes(out) == ["notes_discarded", "note_missing"]
    # Both row 5 (P, no note) and row 6 (N, "공급자 이슈 확인") were deleted; only the one
    # that actually lost something written on it is reported.
    assert notification(out, "notes_discarded")["items"] == [
        {"type": "N", "label": "조사지시", "note": "공급자 이슈 확인"},
    ]
    assert notification(out, "note_missing")["row_indexes"] == [5]


def test_replace_after_has_no_overlap_because_the_old_rows_are_gone(wired):
    out = wpseq.build_candidates(doc=WP_DOC, plan=PLAN, mode="replace_after")
    assert "type_overlap" not in codes(out)


def test_both_modes_report_the_same_pre_pour_fingerprint(wired):
    """The fingerprint describes the sequence BEFORE pouring, so the mode cannot change it."""
    append = wpseq.build_candidates(doc=WP_DOC, plan=PLAN, mode="append")
    replace = wpseq.build_candidates(doc=WP_DOC, plan=PLAN, mode="replace_after")

    assert append["workflow_tag"] == replace["workflow_tag"]
    assert append["workflow_tag"] == wpa.build_workflow_tag(SEQUENCE, ITEMS)


# ── P0013 ① [실패 · mode 값이 둘 중 하나가 아님] ─────────────────────────────

def test_an_unknown_mode_is_refused_rather_than_defaulted(wired):
    """L0011 §4.2: defaulting would let a mode nobody chose rewrite the sequence."""
    with pytest.raises(wpseq.InvalidMode):
        wpseq.build_candidates(doc=WP_DOC, plan=PLAN, mode="replace")


# ── P0013 ① [엣지] ──────────────────────────────────────────────────────────

def test_a_plan_attached_to_no_row_still_pours(monkeypatch, wired):
    monkeypatch.setattr(wpseq.db_wfseq, "get_item_by_result_doc_id", lambda doc_id: None)
    out = wpseq.build_candidates(doc=WP_DOC, plan=PLAN, mode="replace_after")
    assert out["row_count_change"] == {"before": 6, "after": 8, "deleted": 2, "added": 4}


def test_an_unapproved_plan_pours_exactly_like_an_approved_one(wired):
    """0399 M0020 "승인체크같은거 안해도 되니까".

    예전에는 승인 전이면 plan_not_approved 알림을 붙였고, 화면은 그걸 보고 단추를
    잠그고 "승인된 작업계획만 적용할 수 있습니다"를 적었다. 이젠 검토 상태를 아예
    보지 않는다 — 결과가 승인된 계획과 한 글자도 다르지 않은지를 단언한다.
    """
    approved = wpseq.build_candidates(doc=WP_DOC, plan=PLAN, mode="append")
    pending = wpseq.build_candidates(
        doc={**WP_DOC, "doc_review_status": "pending_review"}, plan=PLAN, mode="append",
    )
    assert "plan_not_approved" not in codes(pending)
    assert codes(pending) == codes(approved)
    assert pending["rows"] == approved["rows"]
    assert len([r for r in pending["rows"] if r["poured"]]) == 4


def test_an_undecided_workflow_makes_the_two_modes_agree(monkeypatch):
    monkeypatch.setattr(wpseq.db_wfseq, "get_sequence_by_doc_id", lambda doc_id: None)
    monkeypatch.setattr(wpseq.db_wfseq, "get_sequence_items", lambda seq_id: [])
    monkeypatch.setattr(wpseq.db_wfseq, "get_item_by_result_doc_id", lambda doc_id: None)

    append = wpseq.build_candidates(doc=WP_DOC, plan=PLAN, mode="append")
    replace = wpseq.build_candidates(doc=WP_DOC, plan=PLAN, mode="replace_after")
    assert append["row_count_change"] == replace["row_count_change"]
    assert append["rows"] == replace["rows"]


# ── L0011 §2.2 · §2.3 · §2.4 — the conversion rules on their own ─────────────

def test_normalize_note_strips_control_characters_and_cuts_at_the_limit():
    assert wpseq.normalize_note("  a\tb\n  ") == "ab"
    assert wpseq.normalize_note(None) == ""
    assert wpseq.normalize_note(1234) == ""
    assert len(wpseq.normalize_note("가" * 400)) == wpseq.NOTE_MAX_CHARS


def test_a_result_step_note_lands_on_its_own_row_and_never_touches_the_instruction():
    """0408 M0019 재반려 2 — 두 줄은 서로 다른 말을 갖는다.

    앞선 규칙은 결과 단계의 멘트를 지시 줄로 옮기고, 지시 줄에 이미 말이 있으면 그것을
    버렸다. [자동 승인]에서 AI 워커가 도는 줄은 결과 줄(NR/TR)이므로, 그 규칙 아래에서는
    사람이 결과 단계에 적은 말이 한 글자도 전달되지 않거나 남의 말로 바뀌었다.
    """
    plan = {"steps": [
        {"key": "T#1", "type": "T", "pair_key": "TR#1", "note": ""},
        {"key": "TR#1", "type": "TR", "pair_key": "T#1", "note": "완료 후 확인"},
    ]}
    rows, dropped, uid = wpseq.plan_to_rows(plan, WP_DOC_ID, 1)
    assert [r["note"] for r in rows] == [""]
    assert dropped == []
    attached, _ = wpseq.attach_auto_rows(rows, "ko", uid)
    assert [(r["type"], r["note"]) for r in attached] == [("T", ""), ("TR", "완료 후 확인")]

    plan["steps"][0]["note"] = "사람이 적어 둔 말"
    rows, dropped, uid = wpseq.plan_to_rows(plan, WP_DOC_ID, 1)
    assert [r["note"] for r in rows] == ["사람이 적어 둔 말"]
    assert dropped == []
    attached, _ = wpseq.attach_auto_rows(rows, "ko", uid)
    assert [(r["type"], r["note"]) for r in attached] == [
        ("T", "사람이 적어 둔 말"), ("TR", "완료 후 확인"),
    ]


def test_attaching_report_rows_twice_changes_nothing():
    plan = {"steps": [{"key": "T#1", "type": "T", "note": "a"},
                      {"key": "N#1", "type": "N", "note": "b"}]}
    rows, _, uid = wpseq.plan_to_rows(plan, WP_DOC_ID, 1)
    once, uid = wpseq.attach_auto_rows(rows, "ko", uid)
    twice, _ = wpseq.attach_auto_rows(once, "ko", uid)
    assert [(r["type"], r["origin"], r["note"]) for r in once] == \
           [(r["type"], r["origin"], r["note"]) for r in twice]
    assert [r["type"] for r in once] == ["T", "TR", "N", "NR"]


def test_a_type_no_workflow_can_hold_is_dropped_and_reported():
    plan = {"steps": [{"key": "AC#1", "type": "AC", "note": ""},
                      {"key": "D#1", "type": "D", "note": "x"}]}
    rows, dropped, _ = wpseq.plan_to_rows(plan, WP_DOC_ID, 1)
    assert [r["type"] for r in rows] == ["D"]
    assert dropped == [{"plan_key": "AC#1", "type": "AC", "reason": "type_not_placeable"}]


def test_a_note_on_a_server_assembled_step_is_dropped_and_reported():
    plan = {"steps": [{"key": "TSR#1", "type": "TSR", "note": "쓸모없는 말"}]}
    rows, dropped, _ = wpseq.plan_to_rows(plan, WP_DOC_ID, 1)
    assert rows == []
    assert [d["reason"] for d in dropped] == ["server_assembled_note"]


def test_the_cut_lands_after_the_plan_row_when_that_row_is_editable():
    pending = [
        {"uid": 1, "item_seq_before": 7, "type": "WP"},
        {"uid": 2, "item_seq_before": 8, "type": "D"},
    ]
    assert wpseq.cut_index(pending, 7) == 1
    assert wpseq.cut_index(pending, None) == 0
    assert wpseq.cut_index(pending, 99) == 0


def test_the_pairing_table_is_read_from_the_decision_service_not_copied():
    """L0011 §1.2: two tables that can drift would pour rows the save path rewrites."""
    assert wpseq.AUTO_ROW_MAP == wds.AUTO_REPORT_MAP


# ── P0013 ② — 멘트·출처를 실어 저장 ──────────────────────────────────────────

class _FakeStore:
    @contextmanager
    def transaction(self):
        yield


@pytest.fixture
def save_wired(monkeypatch):
    """Run edit_workflow_pending against recorded calls instead of a database."""
    inserted: list[dict] = []
    stored = [
        _item(1, "R", "요건정의", "done", result=OWNER_DOC_ID),
        _item(2, "WP", "작업계획", "done", result=WP_DOC_ID),
        _item(5, "P", "프로토콜설계", "pending"),
    ]
    for row in stored:
        row["doc_class"] = "R"

    monkeypatch.setattr(wds.db_wfseq, "get_sequence_by_doc_id", lambda doc_id: SEQUENCE)
    monkeypatch.setattr(wds.db_wfseq, "get_sequence_items", lambda seq_id: [dict(r) for r in stored])
    monkeypatch.setattr(wds.db_wfseq, "get_max_item_seq", lambda seq_id: 5)
    monkeypatch.setattr(wds.db_wfseq, "delete_pending_items", lambda seq_id: None)
    monkeypatch.setattr(wds.db_wfseq, "insert_sequence_item",
                        lambda **kwargs: inserted.append(kwargs))
    monkeypatch.setattr(wds, "get_store", lambda: _FakeStore())
    monkeypatch.setattr(wds.db_documents, "update", lambda *a, **k: None)
    monkeypatch.setattr(wds.db_documents, "get_by_id", lambda doc_id: {
        "doc_id": doc_id, "doc_review_status": "wf_in_progress",
        "project_id": "flowgate", "group_id": "flowgate.default.0450",
    })
    monkeypatch.setattr(wds.db_documents, "list_documents", lambda **kwargs: [])
    return inserted, stored


def test_saving_stores_the_note_and_where_the_row_came_from(save_wired):
    inserted, _ = save_wired
    wds.edit_workflow_pending(OWNER_DOC_ID, [
        {"type": "P", "label": "프로토콜설계", "note": "레거시 API 호환 확인",
         "source_doc_id": WP_DOC_ID, "source_revision_no": 1},
        {"type": "T", "label": "작업지시", "note": "테스트 포함 구현",
         "source_doc_id": WP_DOC_ID, "source_revision_no": 1},
    ])

    assert [(row["type_"], row["note"], row["source_doc_id"], row["source_revision_no"])
            for row in inserted] == [
        ("P", "레거시 API 호환 확인", WP_DOC_ID, 1),
        ("T", "테스트 포함 구현", WP_DOC_ID, 1),
        # The report row the server attaches itself carries neither.
        ("TR", "", None, None),
    ]


def test_saving_retrims_the_note_it_is_handed(save_wired):
    inserted, _ = save_wired
    wds.edit_workflow_pending(OWNER_DOC_ID, [
        {"type": "D", "label": "기본설계", "note": "  긴\t말  " + "가" * 400},
    ])
    note = inserted[0]["note"]
    assert note.startswith("긴 말") is False and note.startswith("긴말")
    assert len(note) == wpseq.NOTE_MAX_CHARS


def test_an_ordinary_save_stores_an_empty_note_and_no_source(save_wired):
    """Every caller that predates this change keeps working and says the truth: no plan
    poured these rows."""
    inserted, _ = save_wired
    wds.edit_workflow_pending(OWNER_DOC_ID, [{"type": "AC", "label": "최종승인"}])
    assert inserted[0]["note"] == ""
    assert inserted[0]["source_doc_id"] is None
    assert inserted[0]["source_revision_no"] is None


def test_a_revision_number_without_its_document_is_refused(save_wired):
    inserted, _ = save_wired
    with pytest.raises(ValueError) as exc:
        wds.edit_workflow_pending(OWNER_DOC_ID, [
            {"type": "P", "label": "프로토콜설계", "note": "",
             "source_doc_id": None, "source_revision_no": 1},
        ])
    assert str(exc.value).startswith("invalid_sequence_item:0:")
    assert inserted == []          # refused before anything was written


def test_a_stale_fingerprint_stops_the_save(save_wired):
    inserted, _ = save_wired
    with pytest.raises(wds.SequenceChanged) as exc:
        wds.edit_workflow_pending(
            OWNER_DOC_ID, [{"type": "P", "label": "프로토콜설계"}],
            expected_workflow_tag="seq451-r00000-i3",
        )
    assert exc.value.expected == "seq451-r00000-i3"
    assert exc.value.current != exc.value.expected
    assert inserted == []


def test_the_matching_fingerprint_lets_the_save_through(save_wired):
    inserted, stored = save_wired
    wds.edit_workflow_pending(
        OWNER_DOC_ID, [{"type": "P", "label": "프로토콜설계"}],
        expected_workflow_tag=wpa.build_workflow_tag(SEQUENCE, stored),
    )
    assert [row["type_"] for row in inserted] == ["P"]


def test_a_save_with_no_fingerprint_is_not_compared(save_wired):
    """The check exists for the pour path; demanding it everywhere would break the plain
    [시퀀스 수정] save, which never took a snapshot to be stale against."""
    inserted, _ = save_wired
    wds.edit_workflow_pending(OWNER_DOC_ID, [{"type": "P", "label": "프로토콜설계"}])
    assert [row["type_"] for row in inserted] == ["P"]
