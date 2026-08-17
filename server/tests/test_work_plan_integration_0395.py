"""End-to-end work-plan acceptance flow for flowgate.default.0395 T0017."""
from __future__ import annotations

import json
from unittest.mock import patch

from tests.test_work_plan_0395 import (
    GROUP,
    ROOT_DOC,
    _client,
    _inbox_post,
    _plan,
    patch_store,
    seed,
    storage_root,
    tmp_db,
)


def _provider_registry():
    return [{
        "id": "aip_opus", "name": "Claude Opus", "enabled": True,
        "kind": "claude", "exec_type": "cli",
    }]


def test_human_ai_review_apply_conflict_and_history_flow(seed, storage_root, tmp_path):
    """Chain real response identifiers/revisions/tags through the complete WP flow."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.db.connection import FlowGateStore, get_store
    from modules.flow_gate.services import work_plan_service as wp
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    # The shared focused fixture intentionally disables registered SQL. Restore the
    # production fallback loader so this integration test exercises workflow CRUD.
    store = get_store()
    store._sql = FlowGateStore._sql.__get__(store, type(store))
    client = _client()
    providers = _provider_registry()
    with patch(
        "modules.flow_gate.documents.routers.work_plan._providers",
        return_value=providers,
    ), patch(
        "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
        return_value="0091-WP",
    ):
        created_response = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC,
            "title": "0395 integrated work plan",
            "counted_types": ["D", "T", "TS"],
            "provider_candidates": ["aip_opus"],
        })
        assert created_response.status_code == 201, created_response.text
        created = created_response.json()
        doc_id = created["doc_id"]
        assert created["doc_review_status"] == "pending_review"

        read_response = client.get(f"/api/v1/documents/{doc_id}/work-plan")
        assert read_response.status_code == 200, read_response.text
        read = read_response.json()
        body = read["body"]
        # NR0005 §6.4 rehearsal: two D sheets, four T/TR sets and one TS/TSR set.
        # flowgate.default.0423 T0005 item 15: the create request above omitted
        # quantities, so every count (including TS) now starts at 0 -- state all three
        # explicitly instead of relying on the old all-1 default for TS.
        body["quantities"]["D"]["count"] = 2
        body["quantities"]["T"]["count"] = 4
        body["quantities"]["TS"]["count"] = 1
        body["steps"] = wp.expand_steps(body["counted_types"], body["quantities"])
        for plan_step in body["steps"]:
            if plan_step["locked"]:
                continue
            plan_step["provider_id"] = "aip_opus"
            plan_step["provider_display_name"] = "Claude Opus"
            plan_step["note"] = f"execute {plan_step['key']} from the approved plan"
        saved_response = client.put(
            f"/api/v1/documents/{doc_id}/work-plan",
            json={"base_revision_no": read["revision_no"], "body": body},
        )
        assert saved_response.status_code == 200, saved_response.text
        saved = saved_response.json()
        revision_one = saved["revision_no"]

        # Human create auto-submits to the ordinary review queue; saving preserves it.
        assert saved["doc_review_status"] == "pending_review"
        approved = transition_document_review(
            doc_id=doc_id,
            action="approve",
            actor_user_id="usr_wp_001",
            user_permissions={"document.approve"},
        )
        assert approved["doc_review_status"] == "approved"

        # AI ingress rejects the three documented invalid bodies before accepting a
        # second advisory WP. The actual policy is intentionally not group-unique.
        invalid_json = _inbox_post(tmp_path, "{not-json", doc_code="0092-WP")
        assert invalid_json.status_code == 400
        invalid_provider = _plan(counted_types=["D"], counts={"D": 1})
        invalid_provider["steps"][0]["provider_id"] = "outside-candidates"
        assert _inbox_post(
            tmp_path, json.dumps(invalid_provider), doc_code="0093-WP",
        ).status_code == 400
        duplicate_key = _plan(counted_types=["D"], counts={"D": 2})
        duplicate_key["steps"][1]["key"] = duplicate_key["steps"][0]["key"]
        assert _inbox_post(
            tmp_path, json.dumps(duplicate_key), doc_code="0094-WP",
        ).status_code == 400
        ai_plan = _plan(
            counted_types=["D", "T", "TS"], counts={"D": 2, "T": 4, "TS": 1},
        )
        ai_response = _inbox_post(
            tmp_path, wp.dumps(wp.validate(ai_plan)), doc_code="0095-WP",
        )
        assert ai_response.status_code == 201, ai_response.text
        assert ai_response.json()["doc_id"] != doc_id

        first_preview_response = client.post(
            f"/api/v1/documents/{doc_id}/work-plan/apply/preview",
            json={"instruction_mode": "auto_approved"},
            headers={"X-Locale": "en"},
        )
        assert first_preview_response.status_code == 200, first_preview_response.text
        first_preview = first_preview_response.json()
        assert "workflow_not_decided" in {w["code"] for w in first_preview["warnings"]}
        assert first_preview["workflow"]["workflow_tag"] == "none"
        assert first_preview["comparison"]["added"]["count"] == 12

        # Decide the workflow from the preview response itself, not hard-coded steps.
        db_wfseq.insert_sequence(ROOT_DOC)
        sequence = db_wfseq.get_sequence_by_doc_id(ROOT_DOC)
        for index, item in enumerate(first_preview["comparison"]["added"]["items"], start=1):
            db_wfseq.insert_sequence_item(
                sequence_id=sequence["id"],
                item_seq=index,
                type_=item["type"],
                label=item["label"],
                doc_class="R",
                sort_order=index,
            )

        decided_preview_response = client.post(
            f"/api/v1/documents/{doc_id}/work-plan/apply/preview",
            json={"instruction_mode": "auto_approved"},
        )
        assert decided_preview_response.status_code == 200, decided_preview_response.text
        decided_preview = decided_preview_response.json()
        assert decided_preview["workflow"]["decided"] is True
        assert "workflow_not_decided" not in {w["code"] for w in decided_preview["warnings"]}

        auto_response = client.post(
            f"/api/v1/documents/{doc_id}/work-plan/apply",
            json={
                "instruction_mode": "auto_approved",
                "change_workflow": False,
                "workflow_tag": decided_preview["workflow"]["workflow_tag"],
                "wp_revision_no": decided_preview["wp_revision_no"],
            },
        )
        assert auto_response.status_code == 200, auto_response.text
        auto = auto_response.json()
        assert len(auto["fill"]["folded"]) == 4
        assert all(row["from_key"].startswith("T#") for row in auto["fill"]["folded"])
        assert 12 not in auto["fill"]["filled_item_seqs"]  # TSR is server-assembled.

        # Re-edit the pending tail so every item_seq moves, then grow it by T/TR. A
        # fresh preview must remap logical keys instead of retaining old numbers.
        old_mapping = {row["key"]: row["item_seq"] for row in auto["step_map"]}
        db_wfseq.delete_pending_items(sequence["id"])
        for offset, item in enumerate(first_preview["comparison"]["added"]["items"]):
            item_seq = 21 + offset
            db_wfseq.insert_sequence_item(
                sequence_id=sequence["id"], item_seq=item_seq,
                type_=item["type"], label=item["label"], doc_class="R", sort_order=item_seq,
            )
        for offset, code in enumerate(("T", "TR"), start=33):
            db_wfseq.insert_sequence_item(
                sequence_id=sequence["id"], item_seq=offset,
                type_=code, label=code, doc_class="R", sort_order=offset,
            )
        direct_preview = client.post(
            f"/api/v1/documents/{doc_id}/work-plan/apply/preview",
            json={"instruction_mode": "ai_direct"},
        ).json()
        new_mapping = {row["key"]: row["item_seq"] for row in direct_preview["step_map"]}
        assert new_mapping["T#1"] != old_mapping["T#1"]
        assert new_mapping["TSR#1"] == 32
        direct_response = client.post(
            f"/api/v1/documents/{doc_id}/work-plan/apply",
            json={
                "instruction_mode": "ai_direct",
                "change_workflow": False,
                "workflow_tag": direct_preview["workflow"]["workflow_tag"],
                "wp_revision_no": direct_preview["wp_revision_no"],
            },
        )
        assert direct_response.status_code == 200, direct_response.text
        direct = direct_response.json()
        assert direct["fill"]["folded"] == []
        assert new_mapping["T#1"] in direct["fill"]["filled_item_seqs"]
        assert new_mapping["TSR#1"] not in direct["fill"]["filled_item_seqs"]

        # Another session saves first. The stale request is rejected and its local
        # value remains unchanged because the server never returns/replaces a body.
        current_view = client.get(f"/api/v1/documents/{doc_id}/work-plan").json()
        other_body = current_view["body"]
        other_body["steps"][0]["note"] = "saved by the other session"
        second_save = client.put(
            f"/api/v1/documents/{doc_id}/work-plan",
            json={"base_revision_no": revision_one, "body": other_body},
        )
        assert second_save.status_code == 200, second_save.text
        mine = json.loads(json.dumps(current_view["body"]))
        mine["steps"][0]["note"] = "my unsaved screen value"
        stale = client.put(
            f"/api/v1/documents/{doc_id}/work-plan",
            json={"base_revision_no": revision_one, "body": mine},
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "wp_revision_conflict"
        assert mine["steps"][0]["note"] == "my unsaved screen value"
        assert "body" not in stale.json()

        history_response = client.get(
            f"/api/v1/documents/{doc_id}/work-plan/applications"
        )
        assert history_response.status_code == 200, history_response.text
        history = history_response.json()
        assert history["total"] == 2
        assert [row["instruction_mode"] for row in history["items"]] == [
            "ai_direct", "auto_approved",
        ]
        assert history["items"][0]["workflow_tag_after"] == direct["workflow_tag"]


def test_unavailable_snapshot_provider_is_readable_and_warned(seed, storage_root):
    """An old candidate remains readable but is never substituted into a run."""
    from modules.flow_gate.db.connection import FlowGateStore, get_store

    store = get_store()
    store._sql = FlowGateStore._sql.__get__(store, type(store))
    client = _client()
    available = _provider_registry()
    with patch(
        "modules.flow_gate.documents.routers.work_plan._providers",
        return_value=available,
    ), patch(
        "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
        return_value="0093-WP",
    ):
        created = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC,
            "counted_types": ["T"],
            "provider_candidates": ["aip_opus"],
            # flowgate.default.0423 T0005 item 15: this test needs a real T step to
            # assign a provider to below; an omitted quantity now defaults to 0.
            "quantities": {"T": 1},
        }).json()
        doc_id = created["doc_id"]
        body = created["body"]
        body["steps"][0]["provider_id"] = "aip_opus"
        body["steps"][0]["provider_display_name"] = "Claude Opus"
        saved = client.put(
            f"/api/v1/documents/{doc_id}/work-plan",
            json={"base_revision_no": created["revision_no"], "body": body},
        )
        assert saved.status_code == 200, saved.text

    with patch("modules.flow_gate.documents.routers.work_plan._providers", return_value=[]):
        view = client.get(f"/api/v1/documents/{doc_id}/work-plan")
        assert view.status_code == 200, view.text
        assert view.json()["body"]["steps"][0]["provider_display_name"] == "Claude Opus"
        preview = client.post(
            f"/api/v1/documents/{doc_id}/work-plan/apply/preview",
            json={"instruction_mode": "ai_direct"},
        ).json()
        assert "provider_not_registered" in {w["code"] for w in preview["warnings"]}
        assert preview["fill_preview"]["provider_overrides"] == {}