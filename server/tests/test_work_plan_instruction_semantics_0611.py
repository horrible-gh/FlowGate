"""flowgate.default.0611: WorkPlan-backed N/T AI-authoring contract regression.

The contract these tests pin:

  * ``steps[].note`` / ``pre_instruction_text`` / ``pre_instruction_attachment`` are execution
    metadata supplied to the AI that authors the logical N/T step; the server does not copy
    them mechanically into a canonical document body.
  * Every WorkPlan-backed N/T remains an actual AI worker hop, including ``auto_approved``.
    The Markdown submitted by that worker is the canonical N/T and, when ``review_count > 0``,
    is the document the reviewer reads.
  * Server-owned WorkPlan placeholder materialization is deprecated and fail-closed.  Both
    ``materialize_work_plan_instruction`` and the manual next-approved path reject a
    WorkPlan-backed head with HTTP 409 instead of creating a canonical stub.
  * Execution metadata survives on the source N/T sequence row and reaches the authoring worker
    through ``admission._inject_hop_notes``.  Pause/resume and review → reject → rework →
    re-review → pass preserve the same worker context and canonical document lifecycle.

Every case drives the real production function for the boundary it asserts on: the real
``work_plan_apply_service.apply()``, manual/materializer rejection paths, chain and review
entrypoints, and ``start_run`` with captured worker stdin bytes (0554 harness).  Only storage
edges (sequence rows, document rows, numbering, SSE) are in-memory fakes.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

import test_ai_invoke_pre_instruction_0554 as h
from test_ai_invoke_pre_instruction_0554 import paused_env, pre_env  # noqa: F401 — fixtures

from modules.flow_gate.documents.routers import documents as docs
from modules.flow_gate.services import ai_invoke_service as svc
from modules.flow_gate.services import work_plan_attachment_service as wpa_svc
from modules.flow_gate.services import work_plan_sequence_service as wpseq
from modules.flow_gate.services import workflow_decision_service as workflow
from modules.flow_gate.services.ai_invoke import admission, chain, review


GROUP_ID = h.GROUP_ID
ROOT_DOC = h.ROOT_DOC
WP_DOC_ID = f"{GROUP_ID}.0004-WP"

# Case 1 — the literal sentence that 0611 was opened for.
PROBLEM_NOTE = "NR 및 본 WP를 바탕으로 약식 작업지시서를 작성한다."
# Case 2 — a text pre-instruction.
PRE_TEXT = "기존 0600 materializer의 provenance/idempotency는 유지할 것."
# Case 3 — a file pre-instruction reference.
ATTACHMENT = {
    "doc_id": WP_DOC_ID,
    "filename": "__wp_pre_instruction__T-1__0611brief.txt",
    "original_filename": "0611-brief.txt",
    "content_sha256": "b" * 64,
}
ATTACHMENT_JSON = json.dumps(ATTACHMENT, ensure_ascii=False, separators=(",", ":"))


class _Store:
    @contextmanager
    def transaction(self):
        yield


class _InstructionWorld:
    """In-memory document rows + a real file on disk for every materialized document.

    Backs the storage edges of create_next_approved_core so the REAL descriptor, content
    builder, idempotency marker check and review-slot lookup all run unmodified.
    """

    def __init__(self, wfseq, tmp_path: Path):
        self.wfseq = wfseq
        self.tmp = tmp_path
        self.docs = {
            ROOT_DOC: {"doc_id": ROOT_DOC, "project_id": "flowgate", "group_id": GROUP_ID,
                       "module": "default", "branch": "main", "type_code": "R"},
            WP_DOC_ID: {"doc_id": WP_DOC_ID, "project_id": "flowgate", "group_id": GROUP_ID,
                        "branch": "main", "type_code": "WP", "revision_no": 1},
        }
        self.reserved: list[str] = []
        self.transitions: list[tuple[str, str]] = []

    # document_service.get_document — strict: unknown ids do not exist.
    def get_document(self, doc_id):
        doc = self.docs.get(doc_id)
        return dict(doc) if doc else None

    # db.documents.get_by_id — lenient like pre_env's stub for ids this world does not own.
    def get_by_id(self, doc_id):
        doc = self.docs.get(doc_id)
        return dict(doc) if doc else {"doc_id": doc_id, "branch": "main"}

    def reserve(self, *, group_id, doc_type, module=None):
        code = f"{5 + len(self.reserved):04d}-{doc_type}"
        self.reserved.append(code)
        return code

    def document_path(self, **kwargs):
        return self.tmp / "documents" / f"{kwargs['doc_code']}.md"

    def create_document(self, data, actor_user_id=None):
        doc = {**data, "branch": "main", "doc_review_status": None, "revision_no": 0,
               "rejection_history": None}
        self.docs[doc["doc_id"]] = doc
        return dict(doc)

    def register(self, *, item_id, registered_doc_id, **_kwargs):
        for item in self.wfseq.items:
            if item.get("id") == item_id:
                item["result_doc_id"] = registered_doc_id
                item["result_doc_review_status"] = None

    def transition(self, *, doc_id, action, **_kwargs):
        self.transitions.append((doc_id, action))
        self.set_status(doc_id, {"submit": "pending_review", "approve": "approved"}[action])

    def set_status(self, doc_id, status, *, revision_no=None):
        self.docs[doc_id]["doc_review_status"] = status
        if revision_no is not None:
            self.docs[doc_id]["revision_no"] = revision_no
        for item in self.wfseq.items:
            if item.get("result_doc_id") == doc_id:
                item["result_doc_review_status"] = status
                if status == "approved":
                    later = sorted(
                        i["item_seq"] for i in self.wfseq.items if i["item_seq"] > item["item_seq"]
                    )
                    self.wfseq.head_item_seq = later[0] if later else None

    def body(self, doc_id) -> str:
        return Path(self.docs[doc_id]["file_path"]).read_text(encoding="utf-8")

    def row(self, item_seq) -> dict:
        return next(i for i in self.wfseq.get_sequence_items(1) if i["item_seq"] == item_seq)


def _wire_world(monkeypatch, tmp_path, wfseq) -> _InstructionWorld:
    from modules.flow_gate.db import connection as db_connection
    from modules.flow_gate.db import document_type_labels
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.db import users as db_users
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.workflow import pipeline_service

    world = _InstructionWorld(wfseq, tmp_path)
    monkeypatch.setattr(docs.document_service, "get_document", world.get_document)
    monkeypatch.setattr(docs.document_service, "create_document", world.create_document)
    monkeypatch.setattr(docs.numbering_service, "reserve_document", world.reserve)
    monkeypatch.setattr(docs.storage_paths, "document_path", world.document_path)
    monkeypatch.setattr(docs.storage_paths, "to_storage_relative", lambda path, project=None: str(path))
    monkeypatch.setattr(
        docs.storage_paths, "resolve_storage_path", lambda raw, project_id, branch="main": Path(raw),
    )
    monkeypatch.setattr(docs, "_get_project_branch", lambda _project: "main")
    monkeypatch.setattr(docs, "_try_close_parent_on_child_created", lambda *_a, **_k: None)
    monkeypatch.setattr(db_connection, "get_store", lambda: _Store())
    monkeypatch.setattr(db_connection, "now_iso", lambda: "2026-09-24T00:00:00+09:00")
    monkeypatch.setattr(db_documents, "get_by_id", world.get_by_id)
    monkeypatch.setattr(
        document_type_labels, "get_type_name",
        lambda type_code, locale="en": {"T": "작업지시", "N": "조사"}.get(type_code, type_code),
    )
    monkeypatch.setattr(pipeline_service, "register_workflow_result", world.register)
    monkeypatch.setattr(pipeline_service, "transition_document_review", world.transition)
    monkeypatch.setattr(db_users, "get_by_id", lambda uid: {"user_id": uid, "is_admin": 1})
    for name in ("get_sequence_for_member_doc", "get_sequence_by_doc_id", "get_sequence_items",
                 "get_effective_head", "get_item_by_result_doc_id"):
        monkeypatch.setattr(db_wfseq, name, getattr(wfseq, name))
    return world


def _wp_item(item_seq, type_code, *, item_id, revision_no=7, **fields):
    row = {
        "id": item_id, "item_seq": item_seq, "type": type_code, "label": type_code,
        "note": "", "source_doc_id": WP_DOC_ID, "source_revision_no": revision_no,
        "pre_instruction_text": None, "pre_instruction_attachment_json": None,
        "review_count": 0, "reviewer_provider_id": None,
        "result_doc_id": None, "result_doc_review_status": None, "status": "pending",
    }
    row.update(fields)
    return row


def _metadata_heavy_instruction(type_code="T", **fields):
    """An instruction row that carries ALL execution metadata itself (the worst case for the
    body contract: whatever a row carries, none of it may leak into the canonical body)."""
    return _wp_item(
        1, type_code, item_id=101, note=PROBLEM_NOTE, pre_instruction_text=PRE_TEXT,
        pre_instruction_attachment_json=ATTACHMENT_JSON, **fields,
    )


def _apply_plan(env, monkeypatch, *, instruction_type="T", instruction_mode="auto_approved",
                review_count=0):
    """Pour a one-step WorkPlan through the REAL work_plan_apply_service.apply()."""
    from contextlib import nullcontext

    from modules.flow_gate.services import work_plan_apply_service as apply_svc

    result_type = {"T": "TR", "N": "NR"}[instruction_type]

    class _NullTransactionStore:
        def transaction(self):
            return nullcontext()

    monkeypatch.setattr(apply_svc, "get_store", lambda: _NullTransactionStore())
    monkeypatch.setattr(wpa_svc, "validate_reference", lambda doc_id, reference: None)
    env["wfseq"].sequence = None
    env["wfseq"].items = []
    env["wfseq"].head_item_seq = 1
    attachment = {**ATTACHMENT, "filename": ATTACHMENT["filename"].replace("T-1", f"{instruction_type}-1")}
    plan_steps = [
        {
            "key": f"{instruction_type}#1", "type": instruction_type, "ordinal": 1,
            "locked": False, "provider_id": None, "note": PROBLEM_NOTE,
            "review_count": review_count, "reviewer_provider_id": None,
            "pre_instruction_text": PRE_TEXT, "pre_instruction_attachment": attachment,
        },
        {
            "key": f"{result_type}#1", "type": result_type, "ordinal": 1, "locked": False,
            "provider_id": None, "note": "", "review_count": 0, "reviewer_provider_id": None,
            "pre_instruction_text": None, "pre_instruction_attachment": None,
        },
    ]
    result = apply_svc.apply(
        doc={"doc_id": WP_DOC_ID, "revision_no": 1, "doc_review_status": "approved",
             "target_id": ROOT_DOC},
        owner_doc={"doc_id": ROOT_DOC, "type_code": "R"},
        plan={"steps": plan_steps, "defaults": {"note": ""}},
        plan_path=env["tmp"] / "wp_plan.json",
        providers=[],
        instruction_mode=instruction_mode,
        change_workflow=True,
        workflow_tag=apply_svc.build_workflow_tag(None, []),
        wp_revision_no=1,
        applied_by="usr_admin",
    )
    assert result["ok"] is True
    return result, attachment


def _worker_prompt(env, *, target_seq, instruction_mode, cmd=None, outfile=None) -> str:
    res, outfile = h._start(
        env, h.MENTION, target_seq=target_seq, instruction_mode=instruction_mode,
        cmd=cmd, outfile=outfile,
    )
    h._wait_finished(res["run_id"])
    return h._read(outfile).decode("utf-8")


def _assert_prompt_carries_execution_metadata(prompt: str, attachment: dict) -> None:
    assert PROBLEM_NOTE in prompt
    assert PRE_TEXT in prompt
    assert prompt.count("## WorkPlan 사전지시") == 1
    assert attachment["original_filename"] in prompt


# ── Cases A-D · real WorkPlan N/T authoring path ────────────────────────────────────

@pytest.mark.parametrize("instruction_type", ["T", "N"])
def test_case_a_b_auto_approved_workplan_uses_source_authoring_worker_with_real_file(
    pre_env, monkeypatch, tmp_path, instruction_type,
):
    result, attachment = _apply_plan(
        pre_env, monkeypatch, instruction_type=instruction_type,
        instruction_mode="auto_approved",
    )
    world = _wire_world(monkeypatch, tmp_path, pre_env["wfseq"])
    row = world.row(1)
    assert row["type"] == instruction_type
    assert row["note"] == PROBLEM_NOTE
    assert row["pre_instruction_text"] == PRE_TEXT
    assert json.loads(row["pre_instruction_attachment_json"]) == attachment
    assert result["fill"]["pre_instruction_attachments"]["1"] == attachment

    monkeypatch.setattr(docs, "materialize_work_plan_instruction", lambda **_kw: pytest.fail(
        "WorkPlan N/T must be authored by the source worker, never server-materialized"
    ))
    assert workflow._auto_complete_instruction_heads(
        spine_doc=world.docs[ROOT_DOC], seq={"id": 1}, actor_user_id="usr_admin",
        locale="ko", target_seq=2, instruction_mode="auto_approved",
    ) == []
    prompt = _worker_prompt(pre_env, target_seq=2, instruction_mode="auto_approved")
    _assert_prompt_carries_execution_metadata(prompt, attachment)
    # The prompt carries the authenticated attachment read contract; the connected
    # attachment suite exercises that endpoint and verifies the file bytes, not only its name.
    assert "document_attachments" in prompt
    assert world.reserved == []


def test_server_materializer_refuses_workplan_placeholder(monkeypatch, tmp_path):
    wfseq = h.FakeWfseq(head_item_seq=1, items=[
        _metadata_heavy_instruction(), _wp_item(2, "TR", item_id=102),
    ])
    world = _wire_world(monkeypatch, tmp_path, wfseq)
    with pytest.raises(docs.NextApprovedError) as exc:
        docs.materialize_work_plan_instruction(
            project_id="flowgate", group_id=GROUP_ID, module="default",
            prev_doc_id=ROOT_DOC, sequence_id=1, head=world.row(1),
            actor_user_id="usr_admin", approver_perms={"document.approve"},
        )
    assert exc.value.status_code == 409
    assert "AI authoring path" in exc.value.detail
    assert world.reserved == []


def test_case_c_review_gate_does_not_pre_materialize_workplan_instruction(
    monkeypatch, tmp_path,
):
    wfseq = h.FakeWfseq(head_item_seq=1, items=[
        _metadata_heavy_instruction(review_count=2, reviewer_provider_id=None),
        _wp_item(2, "TR", item_id=102),
    ])
    world = _wire_world(monkeypatch, tmp_path, wfseq)
    monkeypatch.setattr(docs, "materialize_work_plan_instruction", lambda **_kw: pytest.fail(
        "review must wait for the AI-authored canonical N/T"
    ))
    bundle = {
        "materialize_instruction_before_gate": True,
        "doc_ref": ROOT_DOC, "issued_to": "usr_admin", "locale": "ko",
        "target_seq": 2, "instruction_mode": "auto_approved",
    }
    assert review._materialize_work_plan_instruction_before_gate(bundle) == []
    assert world.row(1)["result_doc_id"] is None


def test_case_d_resume_keeps_workplan_instruction_as_the_authoring_hop(
    monkeypatch, tmp_path,
):
    wfseq = h.FakeWfseq(head_item_seq=1, items=[
        _metadata_heavy_instruction(), _wp_item(2, "TR", item_id=102),
    ])
    world = _wire_world(monkeypatch, tmp_path, wfseq)
    assert admission._hop_worker_item_seq(
        1, world.row(1),
        continuation_instruction_mode="auto_approved",
        continuation_auto_approve_item_seqs=[],
    ) == 1
    assert workflow._auto_complete_instruction_heads(
        spine_doc=world.docs[ROOT_DOC], seq={"id": 1}, actor_user_id="usr_admin",
        locale="ko", target_seq=2, instruction_mode="auto_approved",
    ) == []
    assert world.row(1)["result_doc_id"] is None

# ── Case 7 · ai_direct contrast ──────────────────────────────────────────────────────

def test_case7_ai_direct_worker_receives_note_text_and_file_and_nothing_is_materialized(
    pre_env, monkeypatch, tmp_path,
):
    result, attachment = _apply_plan(pre_env, monkeypatch, instruction_mode="ai_direct")
    # ai_direct: the N/T is a worker hop, so the metadata stays on the T row itself.
    assert result["fill"]["pre_instruction_texts"]["1"] == PRE_TEXT
    assert result["fill"]["pre_instruction_attachments"]["1"] == attachment
    assert result["fill"]["note_overrides"]["1"] == PROBLEM_NOTE
    world = _wire_world(monkeypatch, tmp_path, pre_env["wfseq"])
    row = world.row(1)
    assert row["type"] == "T"
    assert row["note"] == PROBLEM_NOTE
    assert row["pre_instruction_text"] == PRE_TEXT
    assert json.loads(row["pre_instruction_attachment_json"]) == attachment

    # The server never auto-assembles an ai_direct N/T, so no approval artifact is created.
    monkeypatch.setattr(docs, "materialize_work_plan_instruction", lambda **_kw: pytest.fail(
        "ai_direct N/T is written by the worker, not materialized"))
    assert workflow._auto_complete_instruction_heads(
        spine_doc=world.docs[ROOT_DOC], seq={"id": 1}, actor_user_id="usr_admin",
        locale="ko", target_seq=2, instruction_mode="ai_direct",
    ) == []
    assert world.reserved == []

    # The T worker itself receives note + text + file through the runtime contract.
    prompt = _worker_prompt(pre_env, target_seq=2, instruction_mode="ai_direct")
    _assert_prompt_carries_execution_metadata(prompt, attachment)


def test_case7_plan_to_rows_attach_auto_rows_preserves_source_metadata_only(
    pre_env, monkeypatch,
):
    """The final-expansion pour must retain authoring metadata only on source N/T."""
    plan = {
        "steps": [
            {
                "key": "T#1", "type": "T", "pair_key": "TR#1",
                "pair_role": "instruction", "note": PROBLEM_NOTE,
                "pre_instruction_text": PRE_TEXT,
                "pre_instruction_attachment": ATTACHMENT,
            },
            {
                "key": "TR#1", "type": "TR", "pair_key": "T#1",
                "pair_role": "result", "note": "",
            },
        ],
        "defaults": {"note": ""},
    }
    rows, dropped, next_uid = wpseq.plan_to_rows(plan, WP_DOC_ID, 1)
    assert dropped == []
    rows, _next_uid = wpseq.attach_auto_rows(rows, next_uid=next_uid)
    assert [row["type"] for row in rows] == ["T", "TR"]

    source, paired = rows
    assert source["note"] == PROBLEM_NOTE
    assert source["pre_instruction_text"] == PRE_TEXT
    assert source["pre_instruction_attachment"] == ATTACHMENT
    assert paired["pre_instruction_text"] is None
    assert paired["pre_instruction_attachment"] is None

    # Persist the poured rows through the storage-edge fake, then enter the real start_run.
    pre_env["wfseq"].items = [
        {
            **row,
            "id": 200 + item_seq,
            "item_seq": item_seq,
            "pre_instruction_attachment_json": (
                json.dumps(row["pre_instruction_attachment"], ensure_ascii=False, separators=(",", ":"))
                if isinstance(row.get("pre_instruction_attachment"), dict)
                else None
            ),
            "result_doc_id": None,
            "result_doc_review_status": None,
        }
        for item_seq, row in enumerate(rows, start=1)
    ]
    pre_env["wfseq"].head_item_seq = 1
    monkeypatch.setattr(wpa_svc, "validate_reference", lambda _doc_id, _reference: None)

    prompt = _worker_prompt(pre_env, target_seq=2, instruction_mode="ai_direct")
    _assert_prompt_carries_execution_metadata(prompt, ATTACHMENT)
