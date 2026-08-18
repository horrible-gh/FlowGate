"""Regression coverage for flowgate.default.0434 T0004 handoff defects."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path

from modules.flow_gate.services import work_plan_apply_service as apply_svc
from modules.flow_gate.services import workflow_decision_service as decision_svc


OWNER = "flowgate.default.0434.0001-B"
PLAN = "flowgate.default.0434.0002-WP"


class _Store:
    @contextmanager
    def transaction(self):
        yield


def test_worker_shape_edit_rebuilds_report_with_instruction_metadata(monkeypatch):
    inserted: list[dict] = []

    def stored_rows(_sequence_id):
        return [
            {
                "id": index,
                "item_seq": row["item_seq"],
                "type": row["type_"],
                "label": row["label"],
                "doc_class": row["doc_class"],
                "sort_order": row["sort_order"],
                "status": "pending",
                "result_doc_id": None,
                "note": row.get("note"),
                "source_doc_id": row.get("source_doc_id"),
                "source_revision_no": row.get("source_revision_no"),
                "provider_id": row.get("provider_id"),
                "provider_display_name": row.get("provider_display_name"),
            }
            for index, row in enumerate(inserted, start=1)
        ]

    monkeypatch.setattr(decision_svc.db_wfseq, "get_sequence_by_doc_id", lambda _doc_id: {"id": 434})
    monkeypatch.setattr(decision_svc.db_wfseq, "get_sequence_items", stored_rows)
    monkeypatch.setattr(decision_svc.db_wfseq, "get_max_item_seq", lambda _sequence_id: 0)
    monkeypatch.setattr(decision_svc.db_wfseq, "delete_pending_items", lambda _sequence_id: None)
    monkeypatch.setattr(
        decision_svc.db_wfseq, "insert_sequence_item", lambda **kwargs: inserted.append(kwargs)
    )
    monkeypatch.setattr(decision_svc, "get_store", lambda: _Store())
    monkeypatch.setattr(decision_svc.db_documents, "update", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        decision_svc.db_documents,
        "get_by_id",
        lambda doc_id: {
            "doc_id": doc_id,
            "type_code": "B",
            "doc_review_status": "wf_in_progress",
            "project_id": "flowgate",
            "group_id": "flowgate.default.0434",
        },
    )
    monkeypatch.setattr(decision_svc.db_documents, "list_documents", lambda **_kwargs: [])

    decision_svc.edit_workflow_pending(
        OWNER,
        [
            {
                "type": "T",
                "label": "작업지시",
                "note": "구현 후 실제 응답을 확인",
                "source_doc_id": PLAN,
                "source_revision_no": 7,
                "provider_id": "codex",
                "provider_display_name": "Codex",
            }
        ],
    )

    print("F1_GREEN=" + json.dumps(inserted, ensure_ascii=False, sort_keys=True))
    assert [
        (
            row["type_"],
            row["note"],
            row["source_doc_id"],
            row["source_revision_no"],
            row["provider_id"],
            row["provider_display_name"],
        )
        for row in inserted
    ] == [
        ("T", "구현 후 실제 응답을 확인", PLAN, 7, "codex", "Codex"),
        ("TR", "구현 후 실제 응답을 확인", PLAN, 7, "codex", "Codex"),
    ]


def test_apply_inserts_projected_values_and_plan_provenance(monkeypatch, tmp_path):
    sequence = {"id": 434, "head_advanced_at": None}
    stored: list[dict] = []
    inserted: list[dict] = []

    def insert_item(**kwargs):
        inserted.append(kwargs)
        stored.append(
            {
                "item_seq": kwargs["item_seq"],
                "type": kwargs["type_"],
                "label": kwargs["label"],
                "sort_order": kwargs["sort_order"],
                "status": "pending",
                "result_doc_id": None,
                "note": kwargs.get("note"),
                "source_doc_id": kwargs.get("source_doc_id"),
                "source_revision_no": kwargs.get("source_revision_no"),
                "provider_id": kwargs.get("provider_id"),
                "provider_display_name": kwargs.get("provider_display_name"),
            }
        )

    monkeypatch.setattr(apply_svc, "_sequence", lambda _owner: (sequence, list(stored)))
    monkeypatch.setattr(apply_svc.db_wfseq, "insert_sequence_item", insert_item)
    monkeypatch.setattr(apply_svc, "get_store", lambda: _Store())
    monkeypatch.setattr(apply_svc, "append_application", lambda *_args, **_kwargs: None)

    plan = {
        "steps": [
            {
                "key": "D#1",
                "type": "D",
                "ordinal": 1,
                "provider_id": "codex",
                "provider_display_name": "Codex",
                "note": "설계 근거를 본문에 남길 것",
                "locked": False,
            }
        ],
        "defaults": {"note": ""},
    }
    result = apply_svc.apply(
        doc={"doc_id": PLAN, "revision_no": 7, "doc_review_status": "approved"},
        owner_doc={"doc_id": OWNER, "type_code": "B"},
        plan=plan,
        plan_path=Path(tmp_path) / "plan.json",
        providers=[{"id": "codex", "name": "Codex", "enabled": True}],
        instruction_mode="ai_direct",
        change_workflow=True,
        workflow_tag=apply_svc.build_workflow_tag(sequence, []),
        wp_revision_no=7,
        applied_by="test",
    )

    print(
        "F3_GREEN="
        + json.dumps(
            {"response": result, "stored_rows": inserted},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )
    assert result["workflow_changed"] is True
    assert len(inserted) == 1
    assert inserted[0]["note"] == "설계 근거를 본문에 남길 것"
    assert inserted[0]["provider_id"] == "codex"
    assert inserted[0]["provider_display_name"] == "Codex"
    assert inserted[0]["source_doc_id"] == PLAN
    assert inserted[0]["source_revision_no"] == 7


def test_sequence_freshness_changes_after_plan_edit_and_clears_after_repour(monkeypatch):
    plan_revision = {"value": 7}
    rows = [
        {
            "id": 1,
            "item_seq": 1,
            "type": "T",
            "label": "작업지시",
            "doc_class": "B",
            "sort_order": 0,
            "status": "pending",
            "note": "현재 계획의 멘트",
            "source_doc_id": PLAN,
            "source_revision_no": 7,
            "provider_id": None,
            "provider_display_name": None,
        },
        {
            "id": 2,
            "item_seq": 2,
            "type": "TR",
            "label": "작업레포트",
            "doc_class": "B",
            "sort_order": 1,
            "status": "pending",
            "note": "현재 계획의 멘트",
            "source_doc_id": PLAN,
            "source_revision_no": 7,
            "provider_id": None,
            "provider_display_name": None,
        },
    ]

    def document(doc_id):
        if doc_id == PLAN:
            return {"doc_id": PLAN, "type_code": "WP", "revision_no": plan_revision["value"]}
        return {"doc_id": OWNER, "type_code": "B", "project_id": "flowgate"}

    monkeypatch.setattr(decision_svc.db_documents, "get_by_id", document)
    monkeypatch.setattr(decision_svc.db_wfseq, "get_sequence_by_doc_id", lambda _doc_id: {"id": 434})
    monkeypatch.setattr(decision_svc.db_wfseq, "get_sequence_items", lambda _seq_id: rows)
    monkeypatch.setattr(
        decision_svc, "provider_view_of", lambda _project_id: {"readable": True, "providers": {}}
    )

    current = decision_svc.get_workflow_sequence(OWNER)
    assert current["has_stale_sources"] is False
    assert [row["source_freshness"] for row in current["items"]] == ["current", "current"]

    plan_revision["value"] = 8
    stale = decision_svc.get_workflow_sequence(OWNER)
    assert stale["has_stale_sources"] is True
    assert stale["stale_source_count"] == 2
    assert [row["source_current_revision_no"] for row in stale["items"]] == [8, 8]
    assert [row["source_freshness"] for row in stale["items"]] == ["stale", "stale"]

    for row in rows:
        row["source_revision_no"] = 8
    repoured = decision_svc.get_workflow_sequence(OWNER)
    print(
        "F2_GREEN="
        + json.dumps(
            {"before_edit": current, "after_edit": stale, "after_repour": repoured},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    assert repoured["has_stale_sources"] is False
    assert [row["source_freshness"] for row in repoured["items"]] == ["current", "current"]