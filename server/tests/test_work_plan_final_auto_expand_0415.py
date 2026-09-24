"""T0005 final-WP expansion regressions.

These tests keep the review-final hook and the shared sequence-edit boundary tied together.
They intentionally use small fakes: the production services remain the only persistence path.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest

from modules.flow_gate.db import connection as db_connection
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.services import work_plan_attachment_service as wpa_svc
from modules.flow_gate.services import work_plan_sequence_service as wpseq
from modules.flow_gate.services.ai_invoke import admission
from modules.flow_gate.services import workflow_decision_service as wds
from modules.flow_gate.workflow import pipeline_service as pipeline

WP_ID = "flowgate.default.0415.0004-WP"
OWNER_ID = "flowgate.default.0415.0001-R"
DOC = {
    "doc_id": WP_ID,
    "target_id": OWNER_ID,
    "revision_no": 2,
    "doc_review_status": "approved",
}
CANDIDATE = {
    "workflow_tag": "before-tag",
    "plan_step_count": 1,
    "rows": [{
        "status": "pending", "type": "T", "label": "implement", "note": "",
        "source_doc_id": WP_ID, "source_revision_no": 2,
        "provider_id": None, "provider_display_name": None,
    }],
}


def _wire_expansion(monkeypatch, *, existing=None, candidate=CANDIDATE):
    monkeypatch.setattr(wpseq.db_wfseq, "get_sequence_by_doc_id", lambda _id: {"id": 11})
    monkeypatch.setattr(wpseq.db_wfseq, "get_sequence_items", lambda _id: list(existing or []))
    monkeypatch.setattr(wpseq, "build_candidates", lambda **_kwargs: candidate)


def test_tc_1_final_wp_expands_through_shared_edit_ssot(monkeypatch):
    _wire_expansion(monkeypatch)
    seen = {}
    monkeypatch.setattr(wds, "edit_workflow_pending", lambda *args, **kwargs: seen.update(args=args, kwargs=kwargs) or {"status": "updated"})
    result = wpseq.expand_final_work_plan(doc=DOC, plan={"steps": []})
    assert result["status"] == "expanded"
    # 880712d: expand_final_work_plan now forwards the work plan execution-setting keys
    # (review_count/reviewer/pre-instruction) alongside the original row shape, defaulting
    # to None for a candidate row that never set them.
    expected_row = {
        key: CANDIDATE["rows"][0].get(key)
        for key in (
            "type", "label", "note", "source_doc_id", "source_revision_no",
            "provider_id", "provider_display_name", "review_count",
            "reviewer_provider_id", "reviewer_provider_display_name",
            "pre_instruction_text", "pre_instruction_attachment",
        )
    }
    assert seen["args"] == (OWNER_ID, [expected_row])
    assert seen["kwargs"]["expected_workflow_tag"] == "before-tag"
    assert seen["kwargs"]["expected_plan"] == {"wp_doc_id": WP_ID, "wp_revision_no": 2}
    assert seen["kwargs"]["applied_by"] == "wp_final_auto_expand"


def test_tc_2_rework_revision_is_the_only_revision_sent(monkeypatch):
    _wire_expansion(monkeypatch)
    seen = {}
    monkeypatch.setattr(wds, "edit_workflow_pending", lambda *a, **k: seen.update(k) or {})
    wpseq.expand_final_work_plan(doc={**DOC, "revision_no": 3}, plan={"steps": []})
    assert seen["expected_plan"]["wp_revision_no"] == 3


@pytest.mark.parametrize("status", ["pending_review", "rejected", "revised"])
def test_tc_3_tc_4_nonfinal_states_do_not_mutate(status, monkeypatch):
    monkeypatch.setattr(wpseq, "build_candidates", lambda **_kwargs: pytest.fail("candidate mutation"))
    assert wpseq.expand_final_work_plan(doc={**DOC, "doc_review_status": status}, plan={}) == {
        "status": "skipped", "reason": "not_final"
    }


def test_tc_5_tc_8_uses_edit_ssot_and_preserves_provenance(monkeypatch):
    _wire_expansion(monkeypatch)
    seen = {}
    monkeypatch.setattr(wds, "edit_workflow_pending", lambda _owner, rows, **kwargs: seen.update(rows=rows, kwargs=kwargs) or {})
    wpseq.expand_final_work_plan(doc=DOC, plan={})
    assert seen["rows"][0]["source_doc_id"] == WP_ID
    assert seen["rows"][0]["source_revision_no"] == 2
    assert seen["kwargs"]["applied_by"] == "wp_final_auto_expand"


@pytest.mark.parametrize("error", [wds.SequenceChanged(OWNER_ID, "old", "new"), wds.PlanRevisionChanged(WP_ID, 2, 3)])
def test_tc_6_tc_7_conflicts_propagate_without_retry_or_overwrite(monkeypatch, error):
    _wire_expansion(monkeypatch)
    calls = []
    def conflict(*_args, **_kwargs):
        calls.append(1)
        raise error
    monkeypatch.setattr(wds, "edit_workflow_pending", conflict)
    with pytest.raises(type(error)):
        wpseq.expand_final_work_plan(doc=DOC, plan={})
    assert len(calls) == 1


def test_tc_9_tc_10_reentry_skips_an_already_applied_revision(monkeypatch):
    _wire_expansion(monkeypatch, existing=[{"source_doc_id": WP_ID, "source_revision_no": 2}])
    monkeypatch.setattr(wpseq, "build_candidates", lambda **_kwargs: pytest.fail("duplicate candidate"))
    assert wpseq.expand_final_work_plan(doc=DOC, plan={}) == {
        "status": "skipped", "reason": "already_applied", "revision_no": 2
    }


def test_tail_requires_mode_selection_and_never_saves(monkeypatch):
    tail_candidate = {
        **CANDIDATE,
        "mode": "replace_after",
        "row_count_change": {"before": 3, "after": 3, "deleted": 1, "added": 1},
    }
    _wire_expansion(monkeypatch, candidate=tail_candidate)
    monkeypatch.setattr(
        wds, "edit_workflow_pending", lambda *_args, **_kwargs: pytest.fail("tail auto-save")
    )
    assert wpseq.expand_final_work_plan(doc=DOC, plan={}) == {
        "status": "needs_selection",
        "reason": "editable_tail_exists",
        "revision_no": 2,
    }


def test_tc_11_placeable_policy_does_not_create_wp_child_rows():
    rows, _dropped, _uid = wpseq.plan_to_rows(
        {"steps": [{"key": "WP#1", "type": "WP", "note": "nested"}]}, WP_ID, 2
    )
    assert rows == []


def test_tc_12_tc_13_existing_sequence_edit_api_remains_available():
    assert callable(wds.edit_workflow_pending)
    assert "expected_workflow_tag" in wds.edit_workflow_pending.__code__.co_varnames
    assert "expected_plan" in wds.edit_workflow_pending.__code__.co_varnames


def test_tc_14_expansion_does_not_touch_continuation_state(monkeypatch):
    _wire_expansion(monkeypatch)
    monkeypatch.setattr(wds, "edit_workflow_pending", lambda *_args, **_kwargs: {"status": "updated"})
    result = wpseq.expand_final_work_plan(doc=DOC, plan={})
    assert result["status"] == "expanded"
    assert "continuation" not in result


def test_approval_hook_returns_success_when_final_expansion_fails(monkeypatch):
    """A durable approval stays successful when final expansion raises a CAS conflict."""
    stored = {**DOC, "type_code": "WP", "doc_review_status": "pending_review", "project_id": "p"}
    plan_body = {"steps": []}
    calls = []

    def update(_doc_id, fields):
        stored.update(fields)
        return dict(stored)

    def raise_sequence_changed(**kwargs):
        calls.append(kwargs)
        raise wds.SequenceChanged(OWNER_ID, "a", "b")

    monkeypatch.setattr(pipeline.db_docs, "get_by_id", lambda _doc_id: dict(stored))
    monkeypatch.setattr(pipeline.db_docs, "update", update)
    monkeypatch.setattr(pipeline, "_require_document_body_for_approval", lambda *_args: None)
    monkeypatch.setattr(pipeline, "log_state_changed", lambda **_kwargs: None)
    # Make the canonical-body reload succeed so the test reaches expand_final_work_plan.
    monkeypatch.setattr(
        "modules.flow_gate.services.work_plan_service.plan_path_for_doc", lambda _doc: "ignored.json"
    )
    monkeypatch.setattr(
        "modules.flow_gate.services.work_plan_service.load_body", lambda _path, **_kwargs: plan_body
    )
    monkeypatch.setattr(wpseq, "expand_final_work_plan", raise_sequence_changed)

    result = pipeline.transition_document_review(
        doc_id=WP_ID, action="approve", actor_user_id="u", user_permissions={"document.approve"}
    )

    assert calls == [{"doc": stored, "plan": plan_body, "locale": "ko"}]
    assert result["doc_review_status"] == "approved"
    assert result["work_plan_expansion"] == {
        "status": "failed", "reason": "SequenceChanged",
    }
    assert stored["doc_review_status"] == "approved"

def test_final_expansion_snapshots_each_instruction_on_its_paired_worker(monkeypatch):
    """The approval path persists T/N pre-instruction only on the TR/NR worker rows."""
    attachments = {
        "T#1": {"doc_id": WP_ID, "filename": "t.txt", "content_sha256": "a" * 64},
        "T#2": {"doc_id": WP_ID, "filename": "t2.txt", "content_sha256": "b" * 64},
        "N#1": {"doc_id": WP_ID, "filename": "n.txt", "content_sha256": "c" * 64},
    }
    steps = []
    for instruction, result, text in (
        ("T#1", "TR#1", "first task"),
        ("T#2", "TR#2", "second task"),
        ("N#1", "NR#1", "research task"),
    ):
        instruction_type = instruction.split("#", 1)[0]
        result_type = result.split("#", 1)[0]
        steps.extend([
            {
                "key": instruction, "type": instruction_type, "pair_key": result,
                "pair_role": "instruction", "note": "", "pre_instruction_text": text,
                "pre_instruction_attachment": attachments[instruction],
            },
            {
                "key": result, "type": result_type, "pair_key": instruction,
                "pair_role": "result", "note": "",
            },
        ])

    monkeypatch.setattr(wpseq.db_wfseq, "get_sequence_by_doc_id", lambda _id: {"id": 11})
    monkeypatch.setattr(wpseq.db_wfseq, "get_sequence_items", lambda _id: [])
    monkeypatch.setattr(wpseq.db_wfseq, "get_item_by_result_doc_id", lambda _id: None)
    monkeypatch.setattr(wpseq, "provider_view_of", lambda _project_id: {"readable": False})
    seen = {}
    monkeypatch.setattr(
        wds, "edit_workflow_pending",
        lambda _owner, rows, **kwargs: seen.update(rows=rows, kwargs=kwargs) or {"status": "updated"},
    )

    result = wpseq.expand_final_work_plan(doc=DOC, plan={"steps": steps})

    assert result["status"] == "expanded"
    rows = seen["rows"]
    assert [row["type"] for row in rows] == ["T", "TR", "T", "TR", "N", "NR"]
    assert [row["pre_instruction_text"] for row in rows] == [
        None, "first task", None, "second task", None, "research task",
    ]
    assert [row["pre_instruction_attachment"] for row in rows] == [
        None, attachments["T#1"], None, attachments["T#2"], None, attachments["N#1"],
    ]
    assert all(row["source_doc_id"] == WP_ID for row in rows)
    assert all(row["source_revision_no"] == DOC["revision_no"] for row in rows)

def test_c6_auto_row_instruction_snapshot_is_idempotent():
    attachment = {"doc_id": WP_ID, "filename": "brief.txt", "content_sha256": "d" * 64}
    rows, _dropped, uid = wpseq.plan_to_rows(
        {"steps": [
            {
                "key": "T#1", "type": "T", "pair_key": "TR#1",
                "pair_role": "instruction", "pre_instruction_text": "durable",
                "pre_instruction_attachment": attachment,
            },
            {"key": "TR#1", "type": "TR", "pair_key": "T#1", "pair_role": "result"},
        ]},
        WP_ID,
        DOC["revision_no"],
    )

    once, uid = wpseq.attach_auto_rows(rows, next_uid=uid)
    twice, _uid = wpseq.attach_auto_rows(once, next_uid=uid)

    assert once == twice
    assert once[0]["pre_instruction_text"] is None
    assert once[1]["pre_instruction_text"] == "durable"
    assert once[1]["pre_instruction_attachment"] == attachment


_CONNECTED_GROUP = "flowgate.default.0415"
_COMPLETED_DOCS = tuple(
    f"{_CONNECTED_GROUP}.{seq:04d}-{type_}"
    for seq, type_ in ((101, "T"), (102, "TR"), (103, "T"), (104, "TR"))
)
_CONNECTED_SEED_SQL = f"""
INSERT OR IGNORE INTO projects(project_id, project_name, is_active, created_at, updated_at)
    VALUES('flowgate', 'FlowGate', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO groups(group_id, project_id, module, title, status, created_at, updated_at)
    VALUES('{_CONNECTED_GROUP}', 'flowgate', 'default', 'final expansion connected',
           'OPEN', datetime('now'), datetime('now'));
INSERT OR IGNORE INTO documents(
        doc_id, project_id, module, group_id, type_code, seq, title, status,
        doc_review_status, revision_no, created_at, updated_at)
    VALUES('{OWNER_ID}', 'flowgate', 'default', '{_CONNECTED_GROUP}', 'R', 1,
           'workflow owner', 'open', 'wf_in_progress', 0, datetime('now'), datetime('now')),
          ('{WP_ID}', 'flowgate', 'default', '{_CONNECTED_GROUP}', 'WP', 4,
           'approved work plan', 'open', 'approved', 2, datetime('now'), datetime('now')),
          ('{_COMPLETED_DOCS[0]}', 'flowgate', 'default', '{_CONNECTED_GROUP}', 'T', 101,
           'completed T1', 'open', 'approved', 0, datetime('now'), datetime('now')),
          ('{_COMPLETED_DOCS[1]}', 'flowgate', 'default', '{_CONNECTED_GROUP}', 'TR', 102,
           'completed TR1', 'open', 'approved', 0, datetime('now'), datetime('now')),
          ('{_COMPLETED_DOCS[2]}', 'flowgate', 'default', '{_CONNECTED_GROUP}', 'T', 103,
           'completed T2', 'open', 'approved', 0, datetime('now'), datetime('now')),
          ('{_COMPLETED_DOCS[3]}', 'flowgate', 'default', '{_CONNECTED_GROUP}', 'TR', 104,
           'completed TR2', 'open', 'approved', 0, datetime('now'), datetime('now'));
"""


class _ConnectedSqliteStore:
    """Minimal store that executes the production query registry against migrated sqlite."""

    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def _execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def _fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, params=None):
        return [dict(row) for row in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def transaction(self):
        yield self


@pytest.fixture
def connected_sequence_store(migrated_sqlite_db):
    path = migrated_sqlite_db(
        "work_plan_final_auto_expand_connected_0415.db", seed_sql=_CONNECTED_SEED_SQL,
    )
    store = _ConnectedSqliteStore(path)
    previous = db_connection.STORE
    db_connection.STORE = store
    db_wfseq.insert_sequence(OWNER_ID)
    try:
        yield store
    finally:
        db_connection.STORE = previous
        store._conn.close()


def test_c1_c5_c7_final_approval_persists_nt_payload_only_on_instruction_rows(
    connected_sequence_store, monkeypatch,
):
    """Run final expansion through real sequence SQL, then production worker prompt assembly."""
    attachments = {
        "T#1": {
            "doc_id": WP_ID, "filename": "__wp_pre_instruction__T-1__first.txt",
            "original_filename": "first-brief.txt", "content_sha256": "a" * 64,
        },
        "T#2": {
            "doc_id": WP_ID, "filename": "__wp_pre_instruction__T-2__second.txt",
            "original_filename": "second-brief.txt", "content_sha256": "b" * 64,
        },
        "N#1": {
            "doc_id": WP_ID, "filename": "__wp_pre_instruction__N-1__research.txt",
            "original_filename": "research-brief.txt", "content_sha256": "c" * 64,
        },
    }
    steps = []
    for instruction, result, text in (
        ("T#1", "TR#1", "first task"),
        ("T#2", "TR#2", "second task"),
        ("N#1", "NR#1", "research task"),
    ):
        instruction_type = instruction.split("#", 1)[0]
        result_type = result.split("#", 1)[0]
        steps.extend([
            {
                "key": instruction, "type": instruction_type, "pair_key": result,
                "pair_role": "instruction", "note": "", "pre_instruction_text": text,
                "pre_instruction_attachment": attachments[instruction],
            },
            {
                "key": result, "type": result_type, "pair_key": instruction,
                "pair_role": "result", "note": "",
            },
        ])

    # Provider registry and application-journal I/O are outside this persistence boundary.
    # The expansion and edit_workflow_pending functions themselves remain unmocked.
    monkeypatch.setattr(wpseq, "provider_view_of", lambda _project_id: {"readable": False})
    monkeypatch.setattr(wds, "_record_plan_application", lambda **_kwargs: True)
    result = wpseq.expand_final_work_plan(
        doc={**DOC, "project_id": "flowgate"}, plan={"steps": steps},
    )

    assert result["status"] == "expanded"
    assert result["result"]["status"] == "updated"
    sequence = db_wfseq.get_sequence_by_doc_id(OWNER_ID)
    stored = db_wfseq.get_sequence_items(sequence["id"])
    assert [row["type"] for row in stored] == ["T", "TR", "T", "TR", "N", "NR"]
    assert [row["pre_instruction_text"] for row in stored] == [
        None, "first task", None, "second task", None, "research task",
    ]
    assert [
        db_wfseq.decode_pre_instruction_attachment(row["pre_instruction_attachment_json"])
        for row in stored
    ] == [
        None, attachments["T#1"], None, attachments["T#2"], None, attachments["N#1"],
    ]

    # Keep reference validation at its attachment-storage boundary. Resolution, row folding,
    # section formatting, and final prompt composition below are all production functions.
    monkeypatch.setattr(wpa_svc, "validate_reference", lambda _doc_id, _reference: None)
    base_prompt = "## 지시\n작업을 수행하세요.\n"
    tr_prompt = admission._inject_hop_notes(
        base_prompt, OWNER_ID, default_note=None, note_overrides=None,
        instruction_mode="auto_approved", locale="ko",
    )
    assert "first task" in tr_prompt
    assert attachments["T#1"]["filename"] in tr_prompt
    assert "second task" not in tr_prompt and "research task" not in tr_prompt
    assert attachments["T#2"]["filename"] not in tr_prompt
    assert attachments["N#1"]["filename"] not in tr_prompt

    # Advance the real DB head past both completed T/TR pairs, then build the N->NR prompt.
    for row, result_doc_id in zip(stored[:4], _COMPLETED_DOCS):
        db_wfseq.set_item_result_doc_id(row["id"], result_doc_id)
    assert db_wfseq.get_effective_head(sequence["id"])["item_seq"] == stored[4]["item_seq"]

    nr_prompt = admission._inject_hop_notes(
        base_prompt, OWNER_ID, default_note=None, note_overrides=None,
        instruction_mode="auto_approved", locale="ko",
    )
    assert "research task" in nr_prompt
    assert attachments["N#1"]["filename"] in nr_prompt
    assert "first task" not in nr_prompt and "second task" not in nr_prompt
    assert attachments["T#1"]["filename"] not in nr_prompt
    assert attachments["T#2"]["filename"] not in nr_prompt