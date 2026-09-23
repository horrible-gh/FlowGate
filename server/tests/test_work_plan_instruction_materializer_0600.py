"""WorkPlan N/T instruction materialization contract — flowgate.default.0600 T#1."""
from __future__ import annotations

import pytest

from modules.flow_gate.documents.routers import documents as docs
from modules.flow_gate.services import workflow_decision_service as workflow
from modules.flow_gate.services import work_plan_sequence_service as wpseq
from modules.flow_gate.services.ai_invoke import chain, review


WP_ID = "flowgate.default.0600.0004-WP"
ATTACHMENT = {
    "doc_id": WP_ID,
    "filename": "__wp_pre_instruction__T-1__brief.txt",
    "original_filename": "brief.txt",
    "content_sha256": "a" * 64,
}


def _head(type_code="T", *, item_id=11, item_seq=1, result_doc_id=None):
    return {
        "id": item_id,
        "item_seq": item_seq,
        "type": type_code,
        "label": type_code,
        "note": "Implement the materializer from the approved WorkPlan.",
        "source_doc_id": WP_ID,
        "source_revision_no": 7,
        "pre_instruction_text": "Preserve attachment provenance.",
        "pre_instruction_attachment": ATTACHMENT,
        "result_doc_id": result_doc_id,
        "result_doc_review_status": None,
    }


@pytest.mark.parametrize(("type_code", "item_seq", "expected_key"), [
    ("T", 3, "T#2"),
    ("N", 5, "N#1"),
])
def test_descriptor_preserves_wp_revision_step_and_payload(
    monkeypatch, type_code, item_seq, expected_key,
):
    head = _head(type_code, item_id=item_seq + 100, item_seq=item_seq)
    rows = [
        _head("T", item_id=101, item_seq=1),
        {"id": 102, "item_seq": 2, "type": "TR", "source_doc_id": WP_ID,
         "source_revision_no": 7},
        _head("T", item_id=103, item_seq=3),
        {"id": 104, "item_seq": 4, "type": "TR", "source_doc_id": WP_ID,
         "source_revision_no": 7},
        _head("N", item_id=105, item_seq=5),
    ]
    head["id"] = next(row["id"] for row in rows if row["item_seq"] == item_seq)
    monkeypatch.setattr(
        docs.document_service,
        "get_document",
        lambda doc_id: {"doc_id": doc_id, "type_code": "WP"},
    )
    monkeypatch.setattr(docs.db.workflow_sequences, "get_sequence_items", lambda _sid: rows)

    descriptor = docs._work_plan_instruction_descriptor(9, head)

    assert descriptor["source_wp_doc_id"] == WP_ID
    assert descriptor["source_wp_revision_no"] == 7
    assert descriptor["source_wp_step_key"] == expected_key
    assert descriptor["idempotency_key"] == f"{WP_ID}:7:{expected_key}"
    assert descriptor["instruction_note"] == head["note"]
    assert descriptor["pre_instruction_text"] == head["pre_instruction_text"]
    assert descriptor["pre_instruction_attachment"] == ATTACHMENT


def test_c7_materialized_instruction_preserves_attachment_and_provenance():
    materialization = {
        "source_wp_doc_id": WP_ID,
        "source_wp_revision_no": 7,
        "source_wp_step_key": "T#1",
        "idempotency_key": f"{WP_ID}:7:T#1",
        "instruction_note": "Implement the materializer.",
        "pre_instruction_text": "Keep the original attachment reference.",
        "pre_instruction_attachment": ATTACHMENT,
    }

    body = docs._build_work_plan_instruction_content(
        project_id="flowgate",
        module="default",
        group_id="flowgate.default.0600",
        type_code="T",
        doc_code="0006-T",
        title="작업지시 — T#1",
        target_id="flowgate.default.0600.0001-B",
        next_type="TR",
        materialization=materialization,
        locale="ko",
    )

    assert "source_wp_doc_id: \"flowgate.default.0600.0004-WP\"" in body
    assert "source_wp_revision_no: 7" in body
    assert "source_wp_step_key: \"T#1\"" in body
    assert f"materialization_key: \"{WP_ID}:7:T#1\"" in body
    assert "Implement the materializer." in body
    assert "Keep the original attachment reference." in body
    assert ATTACHMENT["filename"] in body
    assert ATTACHMENT["content_sha256"] in body


def test_c6_same_wp_revision_step_reentry_reuses_one_instruction_document(monkeypatch):
    materialization = {
        "source_wp_doc_id": WP_ID,
        "source_wp_revision_no": 7,
        "source_wp_step_key": "T#1",
        "idempotency_key": f"{WP_ID}:7:T#1",
    }
    existing = {
        "doc_id": "flowgate.default.0600.0006-T",
        "file_path": "documents/flowgate/main/default/0600/0006-T_document.md",
    }
    monkeypatch.setattr(
        docs, "_work_plan_instruction_descriptor", lambda _sid, _head: materialization,
    )
    monkeypatch.setattr(docs.document_service, "get_document", lambda _doc_id: existing)
    monkeypatch.setattr(
        docs, "_materialized_document_matches", lambda doc, descriptor: True,
    )
    monkeypatch.setattr(
        docs,
        "create_next_approved_core",
        lambda **_kwargs: pytest.fail("same key must not create another document"),
    )

    result = docs.materialize_work_plan_instruction(
        project_id="flowgate",
        group_id="flowgate.default.0600",
        module="default",
        prev_doc_id="flowgate.default.0600.0001-B",
        sequence_id=9,
        head=_head(result_doc_id=existing["doc_id"]),
        actor_user_id="pm",
        approver_perms={"document.approve"},
    )

    assert result["doc_id"] == existing["doc_id"]
    assert result["idempotent_reuse"] is True
    assert result["materialization"]["idempotency_key"] == f"{WP_ID}:7:T#1"


def test_c8_legacy_generic_nt_keeps_fixed_template_auto_approval(monkeypatch):
    legacy_head = {"id": 11, "item_seq": 1, "type": "N", "note": "legacy"}
    seen = {}
    monkeypatch.setattr(
        docs,
        "create_next_approved_core",
        lambda **kwargs: seen.update(kwargs) or {"doc_id": "legacy-N"},
    )

    result = docs.materialize_work_plan_instruction(
        project_id="flowgate",
        group_id="flowgate.default.0600",
        module="default",
        prev_doc_id="flowgate.default.0600.0001-B",
        sequence_id=9,
        head=legacy_head,
        actor_user_id="pm",
        approver_perms={"document.approve"},
    )

    assert result == {"doc_id": "legacy-N"}
    assert seen["type_code"] == "N"
    assert "_work_plan_materialization" not in seen


def test_c1_wp_t_without_reviewer_materializes_and_moves_to_paired_tr(monkeypatch):
    from modules.flow_gate.db import users as db_users

    state = {"head": _head()}
    monkeypatch.setattr(db_users, "get_by_id", lambda user_id: {"user_id": user_id, "is_admin": 1})
    monkeypatch.setattr(
        workflow.db_wfseq, "get_effective_head", lambda _sid: state["head"],
    )
    calls = []

    def materialize(**kwargs):
        calls.append(kwargs)
        state["head"] = {
            "id": 12, "item_seq": 2, "type": "TR", "label": "TR",
            "result_doc_id": None, "result_doc_review_status": None,
        }
        return {"doc_id": "flowgate.default.0600.0006-T", "idempotent_reuse": False}

    monkeypatch.setattr(docs, "materialize_work_plan_instruction", materialize)

    completed = workflow._auto_complete_instruction_heads(
        spine_doc={
            "doc_id": "flowgate.default.0600.0001-B",
            "project_id": "flowgate",
            "group_id": "flowgate.default.0600",
            "module": "default",
        },
        seq={"id": 9},
        actor_user_id="pm",
        locale="ko",
        target_seq=2,
    )

    assert completed == [1]
    assert calls[0]["head"]["source_doc_id"] == WP_ID
    assert calls[0]["sequence_id"] == 9
    assert state["head"]["type"] == "TR"


def test_c9_ts_stays_ai_written_and_outside_nt_materializer():
    assert "TS" not in workflow.INSTRUCTION_AUTO_TYPES
    assert docs._work_plan_instruction_descriptor(9, _head("TS")) is None

def test_c2_wp_t_with_reviewer_materializes_pending_instruction(monkeypatch):
    head = _head()
    head["reviewer_provider_id"] = "reviewer-T"
    head["review_count"] = 2
    seen = {}
    monkeypatch.setattr(
        docs, "_work_plan_instruction_descriptor",
        lambda _sid, _head: {"source_wp_doc_id": WP_ID, "source_wp_revision_no": 7,
                             "source_wp_step_key": "T#1", "idempotency_key": f"{WP_ID}:7:T#1"},
    )
    monkeypatch.setattr(
        docs, "create_next_approved_core",
        lambda **kwargs: seen.update(kwargs) or {"doc_id": "instruction-T", "data": {"doc_review_status": "pending_review"}},
    )

    result = docs.materialize_work_plan_instruction(
        project_id="flowgate", group_id="flowgate.default.0600", module="default",
        prev_doc_id="flowgate.default.0600.0001-B", sequence_id=9, head=head,
        actor_user_id="pm", approver_perms={"document.approve"},
    )

    assert result["data"]["doc_review_status"] == "pending_review"
    assert seen["_approve_immediately"] is False


def test_c2_pending_instruction_stops_auto_completion_without_auto_handled_record(monkeypatch):
    from modules.flow_gate.db import users as db_users

    head = _head()
    head["reviewer_provider_id"] = "reviewer-T"
    head["result_doc_id"] = None
    state = {"head": head}
    monkeypatch.setattr(db_users, "get_by_id", lambda user_id: {"user_id": user_id, "is_admin": 1})
    monkeypatch.setattr(workflow.db_wfseq, "get_effective_head", lambda _sid: state["head"])

    def materialize(**_kwargs):
        state["head"] = {**head, "result_doc_id": "instruction-T",
                         "result_doc_review_status": "pending_review"}
        return {"doc_id": "instruction-T", "data": {"doc_review_status": "pending_review"},
                "idempotent_reuse": False}

    monkeypatch.setattr(docs, "materialize_work_plan_instruction", materialize)
    completed = workflow._auto_complete_instruction_heads(
        spine_doc={"doc_id": "root", "project_id": "flowgate",
                   "group_id": "flowgate.default.0600", "module": "default"},
        seq={"id": 9}, actor_user_id="pm", locale="ko", target_seq=2,
    )

    assert completed == []
    assert state["head"]["type"] == "T"
    assert state["head"]["result_doc_review_status"] == "pending_review"


def test_c3_reject_reworks_same_t_document_revision_then_rereviews(monkeypatch):
    slot = {
        "doc_id": "flowgate.default.0600.0006-T", "item_seq": 1,
        "revision_no": 0, "review_status": "pending_review",
    }
    reviews = [{"id": 1, "verdict": "issues", "revision_no": 0, "findings": "[]"}]
    monkeypatch.setattr(review, "_pending_review_slot", lambda _doc_ref: dict(slot))
    monkeypatch.setattr(review, "resolve_review_count", lambda *_args: 2)
    monkeypatch.setattr(review.db_reviews, "list_by_doc", lambda doc_id: list(reviews))
    monkeypatch.setattr(review, "_review_already_rejected", lambda *_args: False)

    rejected = review.resolve_review_gate({"doc_ref": "root"})
    assert rejected["stage"] == review.REWORK_HOP_KIND
    assert rejected["slot"]["doc_id"] == slot["doc_id"]
    assert rejected["reject_first"] is True

    slot.update(revision_no=1, review_status="revised")
    rereview = review.resolve_review_gate({"doc_ref": "root"})
    assert rereview["stage"] == review.REVIEW_HOP_KIND
    assert rereview["round_no"] == 2
    assert rereview["slot"]["doc_id"] == "flowgate.default.0600.0006-T"
    assert rereview["slot"]["revision_no"] == 1


@pytest.mark.parametrize(("instruction_type", "result_type"), [("T", "TR"), ("N", "NR")])
def test_c4_c5_instruction_and_result_provider_reviewer_policies_stay_independent(
    instruction_type, result_type,
):
    instruction = {
        "uid": 1, "type": instruction_type, "status": "pending", "locked": False,
        "label": instruction_type, "note": "materialize me",
        "source_doc_id": WP_ID, "source_revision_no": 7,
        "provider_id": "instruction-executor",
        "provider_display_name": "Instruction executor",
        "review_count": 2,
        "reviewer_provider_id": "instruction-reviewer",
        "reviewer_provider_display_name": "Instruction reviewer",
        "pair_provider_id": "result-worker",
        "pair_provider_display_name": "Result worker",
        "pair_review_count": 3,
        "pair_reviewer_provider_id": "result-reviewer",
        "pair_reviewer_provider_display_name": "Result reviewer",
        "pre_instruction_text": "canonical instruction",
        "pre_instruction_attachment": ATTACHMENT,
    }

    rows, _uid = wpseq.attach_auto_rows([instruction], next_uid=1)
    materialized, paired = rows
    assert materialized["type"] == instruction_type
    assert materialized["provider_id"] == "instruction-executor"
    assert materialized["reviewer_provider_id"] == "instruction-reviewer"
    assert materialized["review_count"] == 2
    assert materialized["pre_instruction_text"] == "canonical instruction"
    assert paired["type"] == result_type
    assert paired["provider_id"] == "result-worker"
    assert paired["reviewer_provider_id"] == "result-reviewer"
    assert paired["review_count"] == 3
    assert paired["pre_instruction_text"] is None
    assert paired["pre_instruction_attachment"] is None


def test_connected_wp_materialize_review_reject_rework_rereview_pass_handoff(monkeypatch):
    """The live handoff gate sees materialized N/T before deciding its next AI hop."""
    from modules.flow_gate.db import users as db_users

    instruction_doc_id = "flowgate.default.0600.0006-T"
    root = {"doc_id": "root", "project_id": "flowgate",
            "group_id": "flowgate.default.0600", "module": "default"}
    head = _head()
    head.update(reviewer_provider_id="instruction-reviewer", review_count=2)
    state = {"head": head, "revision_no": 0, "review_status": None, "reviews": []}
    hops = []

    monkeypatch.setattr(review.db_docs, "get_by_id", lambda doc_id: root if doc_id == "root" else None)
    monkeypatch.setattr(review.db_wfseq, "get_sequence_for_member_doc", lambda _doc_id: {"id": 9})
    monkeypatch.setattr(workflow.db_wfseq, "get_effective_head", lambda _sid: state["head"])
    monkeypatch.setattr(db_users, "get_by_id", lambda user_id: {"user_id": user_id, "is_admin": 1})
    monkeypatch.setattr(
        docs, "_work_plan_instruction_descriptor",
        lambda _sid, _head: {"source_wp_doc_id": WP_ID, "source_wp_revision_no": 7,
                             "source_wp_step_key": "T#1",
                             "idempotency_key": f"{WP_ID}:7:T#1"},
    )

    def create_instruction(**kwargs):
        assert kwargs["_approve_immediately"] is False
        state["review_status"] = "pending_review"
        state["head"] = {**state["head"], "result_doc_id": instruction_doc_id,
                         "result_doc_review_status": "pending_review"}
        return {"doc_id": instruction_doc_id,
                "data": {"doc_review_status": "pending_review"}}

    monkeypatch.setattr(docs, "create_next_approved_core", create_instruction)

    def pending_slot(_doc_ref):
        if state["review_status"] == "approved":
            return None
        return {"doc_id": instruction_doc_id, "item_seq": 1,
                "revision_no": state["revision_no"],
                "review_status": state["review_status"]}

    monkeypatch.setattr(review, "_pending_review_slot", pending_slot)
    monkeypatch.setattr(review, "resolve_review_count", lambda *_args: 2)
    monkeypatch.setattr(review.db_reviews, "list_by_doc", lambda _doc_id: list(state["reviews"]))
    monkeypatch.setattr(review, "_review_already_rejected", lambda *_args: False)
    monkeypatch.setattr(review.db_reviews, "get_latest_by_doc",
                        lambda _doc_id: state["reviews"][0] if state["reviews"] else None)
    monkeypatch.setattr(review._svc(), "_spawn_review_hop",
                        lambda _group, _bundle, gate: hops.append(("review", gate["round_no"])))
    monkeypatch.setattr(review._svc(), "_spawn_rework_hop",
                        lambda _group, _bundle, gate: hops.append(("rework", gate["slot"]["doc_id"])))
    monkeypatch.setattr(review, "_queue_gate_bundle", lambda *_args: None)
    monkeypatch.setattr(review._svc(), "_auto_reject",
                        lambda *_args: state.update(review_status="rejected") or {"ok": True})

    bundle = {"doc_ref": "root", "issued_to": "pm", "locale": "ko",
              "target_seq": 2, "instruction_mode": "auto_approved"}
    run = {"group_id": "flowgate.default.0600", "end_reason": "exited",
           "run_id": "aiv_wp_final_handoff"}
    monkeypatch.setattr(chain, "peek_auto_resume", lambda _group: bundle)
    monkeypatch.setattr(chain._svc(), "_write_handoff_row", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chain._svc(), "_pop_auto_resume_if_same", lambda *_args: True)
    monkeypatch.setattr(chain._svc(), "_clear_handoff_row", lambda *_args: None)

    chain._maybe_auto_resume_hop(run)
    assert run["_handoff_succeeded"] is True
    assert state["head"]["result_doc_id"] == instruction_doc_id
    assert hops == [("review", 1)]

    state["reviews"] = [{"id": 1, "verdict": "issues", "revision_no": 0,
                          "findings": "[]"}]
    assert review.run_review_gate("flowgate.default.0600", bundle, run) is True
    assert state["review_status"] == "rejected"
    assert hops[-1] == ("rework", instruction_doc_id)

    state.update(revision_no=1, review_status="revised")
    assert review.run_review_gate("flowgate.default.0600", bundle, run) is True
    assert hops[-1] == ("review", 2)

    state["reviews"] = [{"id": 2, "verdict": "pass", "revision_no": 1,
                          "findings": "[]"}, *state["reviews"]]

    def settle(_group, slot, _bundle, _run):
        assert slot["doc_id"] == instruction_doc_id
        assert slot["revision_no"] == 1
        state["review_status"] = "approved"
        state["head"] = {"id": 12, "item_seq": 2, "type": "TR", "label": "TR",
                         "result_doc_id": None, "result_doc_review_status": None}
        return "continue"

    monkeypatch.setattr(review._svc(), "_settle_gate_pass", settle)
    monkeypatch.setattr(review._svc(), "_spawn_auto_resume",
                        lambda _group, _bundle: hops.append(("work", state["head"]["type"])))

    assert review.run_review_gate("flowgate.default.0600", bundle, run) is True
    assert hops[-1] == ("work", "TR")
    assert state["revision_no"] == 1
    assert state["review_status"] == "approved"


def test_c10_resume_chain_materializes_wp_instruction_before_gate_not_head_in_progress(monkeypatch):
    """0600 TR0010 rev4 (human rejection): the pre-gate materialize the connected test
    above proves for _maybe_auto_resume_hop() must ALSO apply on the durable resume
    boundary. resume_chain() used to call review.resolve_review_gate() directly — no
    materialize step — so a WP-backed T with a reviewer that was never materialized
    before the pause was invisible to the gate here: the gate read 'nothing pending',
    fell through to a plain advance, advance_workflow() created the T as pending_review,
    and the very next hop hit head_in_progress. This drives the REAL chain.resume_chain()
    (not _maybe_auto_resume_hop) end to end and proves it now materializes first and
    dispatches the review hop instead of ever reaching that dead end.
    """
    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused
    from modules.flow_gate.db import users as db_users

    instruction_doc_id = "flowgate.default.0600.0006-T"
    root = {"doc_id": "root", "project_id": "flowgate",
            "group_id": "flowgate.default.0600", "module": "default"}
    head = _head()
    head.update(reviewer_provider_id="instruction-reviewer", review_count=2)
    head["result_doc_id"] = None
    head["result_doc_review_status"] = None
    state = {"head": head, "review_status": None}
    hops = []

    monkeypatch.setattr(review.db_docs, "get_by_id", lambda doc_id: root if doc_id == "root" else None)
    monkeypatch.setattr(review.db_wfseq, "get_sequence_for_member_doc", lambda _doc_id: {"id": 9})
    monkeypatch.setattr(workflow.db_wfseq, "get_effective_head", lambda _sid: state["head"])
    monkeypatch.setattr(db_users, "get_by_id", lambda user_id: {"user_id": user_id, "is_admin": 1})
    monkeypatch.setattr(
        docs, "_work_plan_instruction_descriptor",
        lambda _sid, _head: {"source_wp_doc_id": WP_ID, "source_wp_revision_no": 7,
                             "source_wp_step_key": "T#1",
                             "idempotency_key": f"{WP_ID}:7:T#1"},
    )

    def create_instruction(**kwargs):
        assert kwargs["_approve_immediately"] is False
        state["review_status"] = "pending_review"
        state["head"] = {**state["head"], "result_doc_id": instruction_doc_id,
                         "result_doc_review_status": "pending_review"}
        return {"doc_id": instruction_doc_id,
                "data": {"doc_review_status": "pending_review"}}

    monkeypatch.setattr(docs, "create_next_approved_core", create_instruction)

    def pending_slot(_doc_ref):
        if state["review_status"] == "approved":
            return None
        return {"doc_id": instruction_doc_id, "item_seq": 1,
                "revision_no": 0, "review_status": state["review_status"]}

    monkeypatch.setattr(review, "_pending_review_slot", pending_slot)
    monkeypatch.setattr(review, "resolve_review_count", lambda *_args: 2)
    monkeypatch.setattr(review.db_reviews, "list_by_doc", lambda _doc_id: [])
    monkeypatch.setattr(review, "_review_already_rejected", lambda *_args: False)
    monkeypatch.setattr(review, "_queue_gate_bundle", lambda *_args: None)

    monkeypatch.setattr(chain._svc(), "_runs", {})
    monkeypatch.setattr(chain._svc(), "_active_run_for_group", lambda _group: None)
    monkeypatch.setattr(
        db_paused, "get_by_group",
        lambda _group: {
            "group_id": "flowgate.default.0600", "doc_ref": "root",
            "paused_by": "pm", "paused_at": "2026-09-23T00:00:00Z",
            "continuation_target_seq": 2,
        },
    )
    monkeypatch.setattr(
        chain._svc(), "_paused_row_resume_state",
        lambda _project_id, _row, **_kw: {"resume_available": True, "_resume_target_seq": 2},
    )
    monkeypatch.setattr(
        db_paused, "release_owned",
        lambda _group, **_kw: {
            "group_id": "flowgate.default.0600", "doc_ref": "root",
            "paused_by": "pm", "paused_at": "2026-09-23T00:00:00Z",
            "continuation_target_seq": 2,
        },
    )
    monkeypatch.setattr(chain._svc(), "_next_incomplete_item_seq", lambda _doc_ref: 1)
    monkeypatch.setattr(
        chain._svc(), "_spawn_review_hop",
        lambda _group, _bundle, gate: hops.append(("review", gate["round_no"]))
        or {"ok": True, "hop": "review"},
    )
    monkeypatch.setattr(
        chain._svc(), "_spawn_rework_hop",
        lambda _group, _bundle, gate: pytest.fail(
            "a first pass on a freshly materialized instruction must dispatch review, "
            "not rework"),
    )

    result = chain.resume_chain(
        group_id="flowgate.default.0600", user_id="pm", api_base_url="http://x/api/v1",
    )

    assert result == {"ok": True, "hop": "review"}
    assert state["head"]["result_doc_id"] == instruction_doc_id, (
        "the WP-backed T must be materialized before resume_chain() resolves the gate, "
        "exactly like the auto-resume path above — otherwise the gate sees nothing "
        "pending and the resumed advance falls into head_in_progress on the next hop"
    )


def test_c11_wp_t_review_count_positive_reviewer_null_uses_project_default_reviewer(monkeypatch):
    """0600 TR0010 rev5 (human rejection): review necessity is the effective
    ``review_count``, NOT ``reviewer_provider_id`` presence. ``review_count=2`` with
    ``reviewer_provider_id=null`` is a valid WorkPlan configuration meaning "use the
    project default reviewer" — resolve_reviewer()'s existing fallback contract. The
    materializer used to read ``head.get("reviewer_provider_id")`` directly and treat a
    null reviewer as "no review needed" (`_approve_immediately=True`), silently skipping
    instruction review and running straight to the paired NR/TR. This drives the real
    ``_maybe_auto_resume_hop`` -> materialize -> pending_review -> review (resolved via
    the project default provider) -> reject -> rework -> re-review -> pass -> paired
    handoff loop end to end with NO stored reviewer anywhere on the row, proving the
    fallback — not an explicit reviewer id — is what makes this gate.
    """
    from modules.flow_gate.db import users as db_users

    instruction_doc_id = "flowgate.default.0600.0006-T"
    root = {"doc_id": "root", "project_id": "flowgate",
            "group_id": "flowgate.default.0600", "module": "default"}
    head = _head()
    head.update(reviewer_provider_id=None, review_count=2)
    state = {"head": head, "revision_no": 0, "review_status": None, "reviews": []}
    hops = []
    resolved_reviewers = []

    monkeypatch.setattr(review.db_docs, "get_by_id", lambda doc_id: root if doc_id == "root" else None)
    monkeypatch.setattr(review.db_wfseq, "get_sequence_for_member_doc", lambda _doc_id: {"id": 9})
    monkeypatch.setattr(
        review.db_wfseq, "get_sequence_items",
        lambda _sid: [{"item_seq": 1, "review_count": 2, "reviewer_provider_id": None}],
    )
    monkeypatch.setattr(review, "_first_enabled_provider_id", lambda _pid: "project-default-reviewer")
    monkeypatch.setattr(workflow.db_wfseq, "get_effective_head", lambda _sid: state["head"])
    monkeypatch.setattr(db_users, "get_by_id", lambda user_id: {"user_id": user_id, "is_admin": 1})
    monkeypatch.setattr(
        docs, "_work_plan_instruction_descriptor",
        lambda _sid, _head: {"source_wp_doc_id": WP_ID, "source_wp_revision_no": 7,
                             "source_wp_step_key": "T#1",
                             "idempotency_key": f"{WP_ID}:7:T#1"},
    )

    def create_instruction(**kwargs):
        assert kwargs["_approve_immediately"] is False, (
            "review_count=2 must gate even though reviewer_provider_id is null"
        )
        state["review_status"] = "pending_review"
        state["head"] = {**state["head"], "result_doc_id": instruction_doc_id,
                         "result_doc_review_status": "pending_review"}
        return {"doc_id": instruction_doc_id,
                "data": {"doc_review_status": "pending_review"}}

    monkeypatch.setattr(docs, "create_next_approved_core", create_instruction)

    def pending_slot(_doc_ref):
        if state["review_status"] == "approved":
            return None
        return {"doc_id": instruction_doc_id, "item_seq": 1,
                "revision_no": state["revision_no"],
                "review_status": state["review_status"]}

    monkeypatch.setattr(review, "_pending_review_slot", pending_slot)
    monkeypatch.setattr(review.db_reviews, "list_by_doc", lambda _doc_id: list(state["reviews"]))
    monkeypatch.setattr(review, "_review_already_rejected", lambda *_args: False)
    monkeypatch.setattr(review.db_reviews, "get_latest_by_doc",
                        lambda _doc_id: state["reviews"][0] if state["reviews"] else None)

    def spawn_review(_group, bundle, gate):
        reviewer_id = review.resolve_reviewer(
            bundle.get("reviewer_overrides"), gate["slot"]["item_seq"], "flowgate",
            bundle.get("doc_ref"),
        )
        resolved_reviewers.append(reviewer_id)
        hops.append(("review", gate["round_no"]))

    monkeypatch.setattr(review._svc(), "_spawn_review_hop", spawn_review)
    monkeypatch.setattr(review._svc(), "_spawn_rework_hop",
                        lambda _group, _bundle, gate: hops.append(("rework", gate["slot"]["doc_id"])))
    monkeypatch.setattr(review, "_queue_gate_bundle", lambda *_args: None)
    monkeypatch.setattr(review._svc(), "_auto_reject",
                        lambda *_args: state.update(review_status="rejected") or {"ok": True})

    bundle = {"doc_ref": "root", "issued_to": "pm", "locale": "ko",
              "target_seq": 2, "instruction_mode": "auto_approved"}
    run = {"group_id": "flowgate.default.0600", "end_reason": "exited",
           "run_id": "aiv_wp_final_handoff_null_reviewer"}
    monkeypatch.setattr(chain, "peek_auto_resume", lambda _group: bundle)
    monkeypatch.setattr(chain._svc(), "_write_handoff_row", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chain._svc(), "_pop_auto_resume_if_same", lambda *_args: True)
    monkeypatch.setattr(chain._svc(), "_clear_handoff_row", lambda *_args: None)

    chain._maybe_auto_resume_hop(run)
    assert run["_handoff_succeeded"] is True
    assert state["head"]["result_doc_id"] == instruction_doc_id
    assert hops == [("review", 1)]
    assert resolved_reviewers == ["project-default-reviewer"], (
        "no reviewer was ever stored on the row — the review hop must still resolve "
        "through resolve_reviewer()'s project-default fallback"
    )

    state["reviews"] = [{"id": 1, "verdict": "issues", "revision_no": 0, "findings": "[]"}]
    assert review.run_review_gate("flowgate.default.0600", bundle, run) is True
    assert state["review_status"] == "rejected"
    assert hops[-1] == ("rework", instruction_doc_id)

    state.update(revision_no=1, review_status="revised")
    assert review.run_review_gate("flowgate.default.0600", bundle, run) is True
    assert hops[-1] == ("review", 2)
    assert resolved_reviewers[-1] == "project-default-reviewer"

    state["reviews"] = [{"id": 2, "verdict": "pass", "revision_no": 1,
                          "findings": "[]"}, *state["reviews"]]

    def settle(_group, slot, _bundle, _run):
        assert slot["doc_id"] == instruction_doc_id
        assert slot["revision_no"] == 1
        state["review_status"] = "approved"
        state["head"] = {"id": 12, "item_seq": 2, "type": "TR", "label": "TR",
                         "result_doc_id": None, "result_doc_review_status": None}
        return "continue"

    monkeypatch.setattr(review._svc(), "_settle_gate_pass", settle)
    monkeypatch.setattr(review._svc(), "_spawn_auto_resume",
                        lambda _group, _bundle: hops.append(("work", state["head"]["type"])))

    assert review.run_review_gate("flowgate.default.0600", bundle, run) is True
    assert hops[-1] == ("work", "TR")
    assert state["revision_no"] == 1
    assert state["review_status"] == "approved"
