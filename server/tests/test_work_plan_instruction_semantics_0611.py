"""flowgate.default.0611 T#2: WorkPlan-backed N/T semantic contract, connected regression.

The contract these tests pin (0611 T#1 + T#2):

  * ``steps[].note`` / ``pre_instruction_text`` / ``pre_instruction_attachment`` are execution
    metadata for the AI that actually runs the logical step.  They are NOT the canonical N/T
    document body.
  * The canonical body of a WorkPlan-backed N/T is the server-owned approval artifact
    (``documents.WORK_PLAN_INSTRUCTION_CONTENT_SOURCE``): WP provenance frontmatter + a
    status-neutral artifact line.  That same artifact is what an instruction reviewer reads
    when ``review_count > 0``.
  * Execution metadata survives on the sequence row the worker fills and reaches that worker
    through ``admission._inject_hop_notes`` — the paired NR/TR under ``auto_approved``, the N/T
    itself under ``ai_direct``.
  * 0600 provenance (source WP doc/revision/step key, materialization_key), idempotent re-entry
    and the review → reject → rework → re-review → pass gate stay intact.

Every case drives the real production functions for the part it asserts on: the real
``work_plan_apply_service.apply()``, the real ``create_next_approved_document`` route /
``materialize_work_plan_instruction`` / ``create_next_approved_core`` writing a real Markdown
file, the real ``_auto_complete_instruction_heads`` / ``chain._maybe_auto_resume_hop`` /
``chain.resume_chain`` / ``review.run_review_gate`` / ``review.resolve_reviewer``, and the real
``start_run`` whose worker stdin bytes are captured (0554 harness).  Only the storage edges
(sequence rows, document rows, numbering, SSE) are in-memory fakes.
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
from modules.flow_gate.services.ai_invoke import chain, review


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

# Section headings the pre-0611 materializer promoted execution metadata into.
_OLD_BODY_HEADINGS = ("## 지시 내용", "## 추가 사전 지시", "## 첨부 참조", "## 출처")


def _assert_canonical_body_is_the_approval_artifact(body: str, *, type_label: str) -> None:
    """The body is the server artifact: provenance + status-neutral line, no metadata."""
    assert f"content_source: {docs.WORK_PLAN_INSTRUCTION_CONTENT_SOURCE}" in body
    assert docs.WORK_PLAN_INSTRUCTION_CONTENT_SOURCE == "server_approval_artifact"
    assert f'source_wp_doc_id: "{WP_DOC_ID}"' in body
    assert "materialization_key: " in body
    assert f"이 문서는 서버가 생성한 {type_label} 승인 절차 산출물입니다." in body
    assert "승인되었습니다." not in body
    assert PROBLEM_NOTE not in body
    assert PRE_TEXT not in body
    for value in ATTACHMENT.values():
        if value != WP_DOC_ID:
            assert value not in body
    assert ATTACHMENT_JSON not in body
    assert "source_wp_attachment" not in body
    for heading in _OLD_BODY_HEADINGS:
        assert heading not in body


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


# ── Case 1/2/3 · manual [승인 문서 생성] ─────────────────────────────────────────────

@pytest.mark.parametrize(("instruction_type", "result_type", "label"), [
    ("T", "TR", "작업지시"),
    ("N", "NR", "조사"),
])
def test_case1_2_3_manual_approve_button_keeps_metadata_off_the_body_and_on_the_worker(
    pre_env, monkeypatch, tmp_path, instruction_type, result_type, label,
):
    """[승인 문서 생성] → canonical N/T has none of note/text/file; the worker that runs the
    logical step afterwards still receives all three."""
    from test_next_approved_document import _FakeRequest

    _result, attachment = _apply_plan(pre_env, monkeypatch, instruction_type=instruction_type)
    world = _wire_world(monkeypatch, tmp_path, pre_env["wfseq"])
    instruction_row_before = world.row(1)

    created = docs.create_next_approved_document(
        docs.NextApprovedDocumentCreate(
            project_id="flowgate", group_id=GROUP_ID, prev_doc_id=ROOT_DOC,
            type_code=instruction_type, module="default",
        ),
        request=_FakeRequest({"X-Locale": "ko"}),
        current_user={"user_id": "usr_admin", "is_admin": 1},
    )

    doc_id = created["doc_id"]
    body = world.body(doc_id)
    _assert_canonical_body_is_the_approval_artifact(body, type_label=label)
    assert attachment["filename"] not in body
    step_key = f"{instruction_type}#1"
    revision_no = instruction_row_before["source_revision_no"]
    assert f'source_wp_step_key: "{step_key}"' in body
    assert f"source_wp_revision_no: {revision_no}" in body
    assert f'materialization_key: "{WP_DOC_ID}:{revision_no}:{step_key}"' in body
    assert world.transitions == [(doc_id, "submit"), (doc_id, "approve")]

    # Execution metadata was not consumed by the button: it is still on the rows the real
    # apply() wrote, and the paired worker is now the head.
    assert world.row(1) == {**instruction_row_before,
                            "result_doc_id": doc_id, "result_doc_review_status": "approved"}
    worker_row = world.row(2)
    assert worker_row["type"] == result_type
    assert worker_row["note"] == PROBLEM_NOTE
    assert worker_row["pre_instruction_text"] == PRE_TEXT
    assert json.loads(worker_row["pre_instruction_attachment_json"]) == attachment
    assert pre_env["wfseq"].head_item_seq == 2

    prompt = _worker_prompt(pre_env, target_seq=2, instruction_mode="auto_approved")
    _assert_prompt_carries_execution_metadata(prompt, attachment)
    # ...and the canonical body was not touched by running the worker.
    assert world.body(doc_id) == body


@pytest.mark.parametrize("entry", ["manual_button", "continuous_auto_approved"])
def test_case1_2_3_row_that_still_carries_all_metadata_never_leaks_it_into_the_body(
    monkeypatch, tmp_path, entry,
):
    """The sequence-edit pour keeps note/text/file on the WP T row itself (only apply()'s
    auto_approved projection moves them).  Whichever entry point materializes that row, the
    canonical body is still only the approval artifact and the row keeps its metadata."""
    from test_next_approved_document import _FakeRequest

    wfseq = h.FakeWfseq(head_item_seq=1, items=[
        _metadata_heavy_instruction(), _wp_item(2, "TR", item_id=102),
    ])
    world = _wire_world(monkeypatch, tmp_path, wfseq)
    row_before = world.row(1)

    if entry == "manual_button":
        doc_id = docs.create_next_approved_document(
            docs.NextApprovedDocumentCreate(
                project_id="flowgate", group_id=GROUP_ID, prev_doc_id=ROOT_DOC,
                type_code="T", module="default",
            ),
            request=_FakeRequest({"X-Locale": "ko"}),
            current_user={"user_id": "usr_admin", "is_admin": 1},
        )["doc_id"]
    else:
        assert workflow._auto_complete_instruction_heads(
            spine_doc=world.docs[ROOT_DOC], seq={"id": 1}, actor_user_id="usr_admin",
            locale="ko", target_seq=2, instruction_mode="auto_approved",
        ) == [1]
        doc_id = world.row(1)["result_doc_id"]

    body = world.body(doc_id)
    _assert_canonical_body_is_the_approval_artifact(body, type_label="작업지시")
    assert f'materialization_key: "{WP_DOC_ID}:7:T#1"' in body
    assert world.transitions == [(doc_id, "submit"), (doc_id, "approve")]
    assert world.row(1) == {**row_before, "result_doc_id": doc_id,
                            "result_doc_review_status": "approved"}


# ── Case 6 · continuous auto-approved + pause/resume ─────────────────────────────────

def test_case6_continuous_auto_materialization_and_resume_keep_one_semantic_contract(
    paused_env, monkeypatch, tmp_path,
):
    env = paused_env
    _result, attachment = _apply_plan(env, monkeypatch)
    world = _wire_world(monkeypatch, tmp_path, env["wfseq"])
    source_revision_no = world.row(1)["source_revision_no"]

    # continuous auto_approved: the SAME server-side loop advance_workflow runs.
    completed = workflow._auto_complete_instruction_heads(
        spine_doc=world.docs[ROOT_DOC], seq={"id": 1}, actor_user_id="usr_admin",
        locale="ko", target_seq=2, instruction_mode="auto_approved",
    )
    assert completed == [1]
    doc_id = world.row(1)["result_doc_id"]
    body = world.body(doc_id)
    _assert_canonical_body_is_the_approval_artifact(body, type_label="작업지시")
    assert f'materialization_key: "{WP_DOC_ID}:{source_revision_no}:T#1"' in body
    body_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()

    # Idempotent re-entry on the same WP revision/step reuses the same document (real marker
    # check against the real file) and never reserves another number.
    again = docs.materialize_work_plan_instruction(
        project_id="flowgate", group_id=GROUP_ID, module="default", prev_doc_id=ROOT_DOC,
        sequence_id=1, head=world.row(1), actor_user_id="usr_admin",
        approver_perms={"document.approve"},
    )
    assert again["idempotent_reuse"] is True
    assert again["doc_id"] == doc_id
    assert again["content_source"] == docs.WORK_PLAN_INSTRUCTION_CONTENT_SOURCE
    assert again["materialization"]["idempotency_key"] == f"{WP_DOC_ID}:{source_revision_no}:T#1"
    assert len(world.reserved) == 1

    # A worker hop that is paused and resumed through the real pause/resume APIs receives the
    # same execution metadata before and after the boundary.
    worker_cmd, worker_outfile = h._slow_capture_cmd(env["tmp"], seconds=1.0)
    before, _ = h._start(env, h.MENTION, target_seq=2, cmd=worker_cmd, outfile=worker_outfile)
    assert svc.pause_run(before["run_id"], "usr_admin")["status"] == "pause_requested"
    assert svc.mark_user_paused(GROUP_ID, before["run_id"]) is True
    assert h._wait_finished(before["run_id"])["end_reason"] == "user_paused"
    before_text = h._read(worker_outfile).decode("utf-8")
    _assert_prompt_carries_execution_metadata(before_text, attachment)

    cmd2, outfile_after = h._capture_cmd(env["tmp"])
    monkeypatch.setattr(workflow, "advance_workflow", lambda **kw: {
        "token": "tok_raw_resume", "token_id": "tok_resume_0611",
        "expires_at": "2026-09-25T00:00:00+00:00",
        "scratch_dir": str(env["tmp"] / "resumework"), "mention": h.MENTION,
    })
    env["chain"]["providers"] = [h._provider(cmd=cmd2)]
    after = svc.resume_chain(
        group_id=GROUP_ID, user_id="usr_admin",
        api_base_url="http://127.0.0.1:1/flowgate/api/v1",
    )
    h._wait_finished(after["run_id"])
    assert h._read(outfile_after).decode("utf-8") == before_text

    # resume did not regenerate the canonical body from note/pre-instruction.
    assert hashlib.sha256(world.body(doc_id).encode("utf-8")).hexdigest() == body_sha
    assert len(world.reserved) == 1


# ── Case 4/5 · review_count > 0, reviewer explicit / project-default fallback ────────

def _wire_review_gate(monkeypatch, world, state, hops, resolved_reviewers, reviewed_bodies):
    monkeypatch.setattr(review, "_first_enabled_provider_id", lambda _pid: "project-default-reviewer")
    monkeypatch.setattr(review, "_provider_enabled", lambda _pid, _provider: True)
    monkeypatch.setattr(review, "_review_already_rejected", lambda *_args: False)
    monkeypatch.setattr(review.db_reviews, "list_by_doc", lambda _doc_id: list(state["reviews"]))
    monkeypatch.setattr(review.db_reviews, "get_latest_by_doc",
                        lambda _doc_id: state["reviews"][0] if state["reviews"] else None)
    monkeypatch.setattr(review, "_queue_gate_bundle", lambda *_args: None)

    def spawn_review(_group, bundle, gate):
        resolved_reviewers.append(review.resolve_reviewer(
            bundle.get("reviewer_overrides"), gate["slot"]["item_seq"], "flowgate",
            bundle.get("doc_ref"),
        ))
        # What the reviewer is handed is the slot document itself.
        reviewed_bodies.append(world.body(gate["slot"]["doc_id"]))
        hops.append(("review", gate["round_no"]))
        return {"ok": True, "hop": "review"}

    monkeypatch.setattr(review._svc(), "_spawn_review_hop", spawn_review)
    monkeypatch.setattr(review._svc(), "_spawn_rework_hop",
                        lambda _group, _bundle, gate: hops.append(("rework", gate["slot"]["doc_id"])))
    monkeypatch.setattr(review._svc(), "_auto_reject",
                        lambda slot, *_args: world.set_status(slot["doc_id"], "rejected")
                        or {"ok": True})


@pytest.mark.parametrize(("stored_reviewer", "expected_reviewer"), [
    ("instruction-reviewer", "instruction-reviewer"),
    (None, "project-default-reviewer"),
])
def test_case4_5_review_reject_rework_rereview_pass_reviews_the_approval_artifact(
    monkeypatch, tmp_path, stored_reviewer, expected_reviewer,
):
    wfseq = h.FakeWfseq(head_item_seq=1, items=[
        _metadata_heavy_instruction(review_count=2, reviewer_provider_id=stored_reviewer),
        _wp_item(2, "TR", item_id=102),
    ])
    world = _wire_world(monkeypatch, tmp_path, wfseq)
    state = {"reviews": []}
    hops, resolved_reviewers, reviewed_bodies = [], [], []
    _wire_review_gate(monkeypatch, world, state, hops, resolved_reviewers, reviewed_bodies)

    bundle = {"doc_ref": ROOT_DOC, "issued_to": "usr_admin", "locale": "ko",
              "target_seq": 2, "instruction_mode": "auto_approved"}
    run = {"group_id": GROUP_ID, "end_reason": "exited", "run_id": "aiv_0611_review"}
    monkeypatch.setattr(chain, "peek_auto_resume", lambda _group: bundle)
    monkeypatch.setattr(chain._svc(), "_write_handoff_row", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chain._svc(), "_pop_auto_resume_if_same", lambda *_args: True)
    monkeypatch.setattr(chain._svc(), "_clear_handoff_row", lambda *_args: None)

    # materialize → review (round 1)
    chain._maybe_auto_resume_hop(run)
    assert run["_handoff_succeeded"] is True
    doc_id = world.row(1)["result_doc_id"]
    assert doc_id is not None
    assert world.transitions == [(doc_id, "submit")], "review_count=2 must not auto-approve"
    assert world.docs[doc_id]["doc_review_status"] == "pending_review"
    assert hops == [("review", 1)]
    assert resolved_reviewers == [expected_reviewer]
    # The FIRST review target is the server approval artifact, not a fake instruction sheet
    # assembled from the WP note / pre-instruction / attachment.
    _assert_canonical_body_is_the_approval_artifact(reviewed_bodies[0], type_label="작업지시")

    # reject → rework
    state["reviews"] = [{"id": 1, "verdict": "issues", "revision_no": 0, "findings": "[]"}]
    assert review.run_review_gate(GROUP_ID, bundle, run) is True
    assert world.docs[doc_id]["doc_review_status"] == "rejected"
    assert hops[-1] == ("rework", doc_id)

    # re-review (round 2) on the reworked revision of the SAME document
    world.set_status(doc_id, "revised", revision_no=1)
    assert review.run_review_gate(GROUP_ID, bundle, run) is True
    assert hops[-1] == ("review", 2)
    assert resolved_reviewers[-1] == expected_reviewer

    # pass → handoff to the paired worker
    state["reviews"] = [{"id": 2, "verdict": "pass", "revision_no": 1, "findings": "[]"},
                        *state["reviews"]]

    def settle(_group, slot, _bundle, _run):
        assert slot["doc_id"] == doc_id
        assert slot["revision_no"] == 1
        world.set_status(doc_id, "approved")
        return "continue"

    monkeypatch.setattr(review._svc(), "_settle_gate_pass", settle)
    monkeypatch.setattr(
        review._svc(), "_spawn_auto_resume",
        lambda _group, _bundle: hops.append(("work", wfseq.get_effective_head(1)["type"])),
    )
    assert review.run_review_gate(GROUP_ID, bundle, run) is True
    assert hops[-1] == ("work", "TR")
    assert len(world.reserved) == 1, "rework/re-review must reuse the one materialized document"

    # The review cycle never folded metadata into the row's document nor dropped it from the row.
    row = world.row(1)
    assert row["note"] == PROBLEM_NOTE
    assert row["pre_instruction_text"] == PRE_TEXT
    assert row["pre_instruction_attachment_json"] == ATTACHMENT_JSON


# ── Case 6 · durable resume boundary with review_count > 0 ───────────────────────────

def test_case6_resume_chain_materializes_the_approval_artifact_before_the_review_gate(
    monkeypatch, tmp_path,
):
    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

    wfseq = h.FakeWfseq(head_item_seq=1, items=[
        _metadata_heavy_instruction(review_count=1, reviewer_provider_id=None),
        _wp_item(2, "TR", item_id=102),
    ])
    world = _wire_world(monkeypatch, tmp_path, wfseq)
    state = {"reviews": []}
    hops, resolved_reviewers, reviewed_bodies = [], [], []
    _wire_review_gate(monkeypatch, world, state, hops, resolved_reviewers, reviewed_bodies)
    monkeypatch.setattr(review._svc(), "_spawn_rework_hop", lambda *_args: pytest.fail(
        "a freshly materialized instruction must be reviewed first, not reworked"))

    paused_row = {"group_id": GROUP_ID, "doc_ref": ROOT_DOC, "paused_by": "usr_admin",
                  "paused_at": "2026-09-24T00:00:00Z", "continuation_target_seq": 2,
                  "continuation_instruction_mode": "auto_approved"}
    monkeypatch.setattr(chain._svc(), "_runs", {})
    monkeypatch.setattr(chain._svc(), "_active_run_for_group", lambda _group: None)
    monkeypatch.setattr(db_paused, "get_by_group", lambda _group: dict(paused_row))
    monkeypatch.setattr(db_paused, "release_owned", lambda _group, **_kw: dict(paused_row))
    monkeypatch.setattr(
        chain._svc(), "_paused_row_resume_state",
        lambda _project_id, _row, **_kw: {"resume_available": True, "_resume_target_seq": 2},
    )
    monkeypatch.setattr(chain._svc(), "_next_incomplete_item_seq", lambda _doc_ref: 1)

    result = chain.resume_chain(
        group_id=GROUP_ID, user_id="usr_admin", api_base_url="http://x/api/v1",
    )

    assert result == {"ok": True, "hop": "review"}
    doc_id = world.row(1)["result_doc_id"]
    assert doc_id is not None
    assert world.transitions == [(doc_id, "submit")]
    assert resolved_reviewers == ["project-default-reviewer"]
    _assert_canonical_body_is_the_approval_artifact(reviewed_bodies[0], type_label="작업지시")
    row = world.row(1)
    assert (row["note"], row["pre_instruction_text"], row["pre_instruction_attachment_json"]) == (
        PROBLEM_NOTE, PRE_TEXT, ATTACHMENT_JSON,
    )


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


def test_case7_plan_to_rows_attach_auto_rows_ai_direct_start_run_preserves_source_metadata(
    pre_env, monkeypatch,
):
    """The final-expansion pour must serve both modes without consuming the N/T metadata."""
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
    assert paired["pre_instruction_text"] == PRE_TEXT
    assert paired["pre_instruction_attachment"] == ATTACHMENT
    assert paired["pre_instruction_attachment"] is not source["pre_instruction_attachment"]

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
