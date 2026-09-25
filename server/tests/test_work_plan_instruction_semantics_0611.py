"""flowgate.default.0611 T0011 / TR0012 rev2: WorkPlan-backed N/T instruction paths.

Final contract (rej_01M3AVQVHD6PSTBE), each path pinned by its own test below:

  | path                              | server expands T/N md | instruction-authoring AI |
  |-----------------------------------|-----------------------|--------------------------|
  | 1. [AI 호출] auto_approved         | yes                   | no                       |
  | 2. [AI 호출] ai_direct             | no                    | yes                      |
  | 3. manual [승인지시서 생성]         | yes                   | no (no AI call at all)   |

  * A WorkPlan N/T step carries its instruction document as the step's pre-instruction:
    the attached Markdown file and/or the directly written text.  When the server expands it,
    that document -- and never the one-line ``steps[].note`` (0611 B0001) -- becomes the
    canonical N/T body.
  * Path 1: the REAL ``POST /api/v1/ai-invoke/start`` route (the call ContinuousWorkDialog
    makes) -> start_run -> its first-hop issuer -> advance_workflow ->
    _auto_complete_instruction_heads -> materialize_work_plan_instruction expands the real
    T/N Markdown, approves it, and only THEN mints the one token of the next worker (TR/NR),
    whose real subprocess starts on that canonical document.
  * Path 2: the same real route under ai_direct never reaches the materializer.  The first
    token is the N/T authoring worker's, and its prompt carries the step's note, written
    text and attached file as input.  Once that authored N/T is approved through the normal
    flow, the next [AI 호출] goes to the TR/NR worker -- still with no server expansion.
  * Path 3: the real ``POST /next-approved`` handler expands the same real T/N Markdown with
    no AI call at all, whatever instruction mode the WP was poured with.
  * 0611 historical contract (rej_01M3AVQVHD6PSTBE, superseded below by 0614 T0004): a
    WorkPlan N/T with no instruction document had nothing to expand, so it stayed the N/T
    authoring worker hop (0611 T0009) in either mode and the manual path answered 409
    instead of inventing a body.
  * 0614 T0004 (explicit human override, NOT a regression fix -- the user re-confirmed the
    0611 B0001 self-referential-document risk and asked for it anyway): under manual and
    auto_approved, a WorkPlan N/T with no instruction document now falls back to its
    steps[].note as the canonical body, and falls back further to the legacy generated
    instruction only when the note is empty too. ai_direct is untouched -- it still never
    server-expands a WorkPlan step, document or not. When an instruction document IS
    present, its content still wins outright and the note is never merged into it. A broken
    instruction file still fails closed.
  * review_count > 0 exposes the real expanded document as ``pending_review`` to the gate,
    and re-entry reuses it through the provenance/idempotency marker.

The attachment is a real file on disk: the registry row and the storage-jail lookup are the
only edges replaced, so the real ``validate_reference`` digest check and the real
``read_reference_text`` read run unmodified.  Sequence rows, document rows, numbering and SSE
are in-memory fakes; every production function under test runs for real.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

import test_ai_invoke_pre_instruction_0554 as h
from test_ai_invoke_pre_instruction_0554 import paused_env, pre_env  # noqa: F401 — fixtures

from modules.flow_gate.documents.attachments.errors import AttachmentError
from modules.flow_gate.documents.routers import documents as docs
from modules.flow_gate.services import ai_invoke_service as svc
from modules.flow_gate.services import work_plan_attachment_service as wpa_svc
from modules.flow_gate.services import work_plan_sequence_service as wpseq
from modules.flow_gate.services import workflow_decision_service as workflow
from modules.flow_gate.services.ai_invoke import admission, review


# The real /ai-invoke/start route validates the group id, so this file uses a real-shaped one
# (h.GROUP_ID "…0554.preinstr" is refused by validate_group_id).
GROUP_ID = "flowgate.default.0611"
ROOT_DOC = f"{GROUP_ID}.0001-R"
WP_DOC_ID = f"{GROUP_ID}.0004-WP"
_REAL_VALIDATE_REFERENCE = wpa_svc.validate_reference

# The one-line message for the AI of the step -- the sentence 0611 B0001 was opened for.
PROBLEM_NOTE = "NR 및 본 WP를 바탕으로 약식 작업지시서를 작성한다."
# The directly written half of the instruction document.
PRE_TEXT = "기존 0600 materializer의 provenance/idempotency는 유지할 것."
# The attached half: a real Markdown instruction file, with its own frontmatter.
FILE_DIRECTIVE = "0611_REAL_FILE_DIRECTIVE: 작업지시에는 반드시 회귀 검증 단계를 포함할 것."
INSTRUCTION_MD = (
    "---\r\n"
    "title: uploaded draft\r\n"
    "---\r\n"
    "# 결제 모듈 회귀 수정\r\n"
    "\r\n"
    "## 요구사항\r\n"
    "\r\n"
    f"- {FILE_DIRECTIVE}\r\n"
)


class _Store:
    @contextmanager
    def transaction(self):
        yield


class _InstructionWorld:
    """In-memory document rows + a real file on disk for every created document."""

    def __init__(self, wfseq, tmp_path: Path):
        self.wfseq = wfseq
        self.tmp = tmp_path
        self.docs = {
            ROOT_DOC: {"doc_id": ROOT_DOC, "project_id": "flowgate", "group_id": GROUP_ID,
                       "module": "default", "branch": "main", "type_code": "R", "seq": 1},
            WP_DOC_ID: {"doc_id": WP_DOC_ID, "project_id": "flowgate", "group_id": GROUP_ID,
                        "branch": "main", "type_code": "WP", "revision_no": 1},
        }
        self.reserved: list[str] = []
        self.transitions: list[tuple[str, str]] = []

    def get_document(self, doc_id):
        doc = self.docs.get(doc_id)
        return dict(doc) if doc else None

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
        status = {"submit": "pending_review", "approve": "approved"}[action]
        self.docs[doc_id]["doc_review_status"] = status
        for item in self.wfseq.items:
            if item.get("result_doc_id") == doc_id:
                item["result_doc_review_status"] = status
                if status == "approved":
                    later = sorted(
                        i["item_seq"] for i in self.wfseq.items if i["item_seq"] > item["item_seq"]
                    )
                    self.wfseq.head_item_seq = later[0] if later else None

    def predecessor_ids(self, _sequence_id, head_id, limit=2):
        head = next(i for i in self.wfseq.items if i.get("id") == head_id)
        done = [
            i["result_doc_id"] for i in sorted(self.wfseq.items, key=lambda i: -i["item_seq"])
            if i["item_seq"] < head["item_seq"] and i.get("result_doc_id")
        ]
        return done[:limit]

    def predecessor_id(self, sequence_id, head_id):
        ids = self.predecessor_ids(sequence_id, head_id, limit=1)
        return ids[0] if ids else None

    def body(self, doc_id) -> str:
        return Path(self.docs[doc_id]["file_path"]).read_text(encoding="utf-8")

    def row(self, item_seq) -> dict:
        return next(i for i in self.wfseq.get_sequence_items(1) if i["item_seq"] == item_seq)


def _wire_world(monkeypatch, tmp_path, wfseq) -> _InstructionWorld:
    from modules.flow_gate.db import connection as db_connection
    from modules.flow_gate.db import document_type_labels
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.db import tokens as db_tokens
    from modules.flow_gate.db import users as db_users
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.workflow import pipeline_service
    from modules.flow_gate.workflow.routers import workflow as workflow_router

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
    monkeypatch.setattr(docs, "_reject_if_group_ai_running", lambda _doc: None)
    monkeypatch.setattr(db_connection, "get_store", lambda: _Store())
    monkeypatch.setattr(db_connection, "now_iso", lambda: "2026-09-25T00:00:00+09:00")
    monkeypatch.setattr(db_documents, "get_by_id", world.get_by_id)
    monkeypatch.setattr(db_documents, "get_group_max_seq", lambda _group_id: 4)
    monkeypatch.setattr(db_documents, "fetch_recent_group_docs", lambda **_kw: [])
    monkeypatch.setattr(db_tokens, "get_unconsumed_by_doc_ref", lambda _doc_ref: None)
    monkeypatch.setattr(
        document_type_labels, "get_type_name",
        lambda type_code, locale="en": {"T": "작업지시", "N": "조사"}.get(type_code, type_code),
    )
    monkeypatch.setattr(pipeline_service, "register_workflow_result", world.register)
    monkeypatch.setattr(pipeline_service, "transition_document_review", world.transition)
    monkeypatch.setattr(db_users, "get_by_id", lambda uid: {"user_id": uid, "is_admin": 1})
    monkeypatch.setattr(
        workflow_router, "_get_user_permissions",
        lambda _user: {"document.approve", "document.update", "perm_document_create"},
    )
    for name in ("get_sequence_for_member_doc", "get_sequence_by_doc_id", "get_sequence_items",
                 "get_effective_head", "get_item_by_result_doc_id"):
        monkeypatch.setattr(db_wfseq, name, getattr(wfseq, name))
    monkeypatch.setattr(db_wfseq, "get_predecessor_result_doc_ids", world.predecessor_ids)
    monkeypatch.setattr(db_wfseq, "get_predecessor_result_doc_id", world.predecessor_id)
    return world


def _install_instruction_file(monkeypatch, tmp_path, type_code="T", content=INSTRUCTION_MD):
    """Register one real Markdown file as the WP step's reserved pre-instruction attachment."""
    filename = f"{wpa_svc.RESERVED_PREFIX}{type_code}-1__brief.md"
    path = tmp_path / "attachments" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))
    attachment = {
        "doc_id": WP_DOC_ID,
        "filename": filename,
        "original_filename": "0611-brief.md",
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }
    registry_row = {"filename": filename, "original_filename": "0611-brief.md",
                    "content_sha256": attachment["content_sha256"]}

    def registry_get(doc_id, name):
        return dict(registry_row) if (doc_id, name) == (WP_DOC_ID, filename) else None

    def resolve_registered_attachment(doc, name, *, require_file=True):
        if (doc.get("doc_id"), name) != (WP_DOC_ID, filename):
            raise AttachmentError(404, "ATTACHMENT_NOT_FOUND", "not registered")
        return dict(registry_row), path

    monkeypatch.setattr(wpa_svc, "registry_get", registry_get)
    monkeypatch.setattr(wpa_svc, "resolve_registered_attachment", resolve_registered_attachment)
    return attachment, path


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


def _apply_plan(env, monkeypatch, *, attachment, instruction_type="T",
                instruction_mode="auto_approved", review_count=0,
                pre_text=PRE_TEXT, note=PROBLEM_NOTE):
    """Pour a WP instruction/result pair through the REAL work_plan_apply_service.apply()."""
    from contextlib import nullcontext

    from modules.flow_gate.services import work_plan_apply_service as apply_svc

    result_type = {"T": "TR", "N": "NR"}[instruction_type]

    class _NullTransactionStore:
        def transaction(self):
            return nullcontext()

    monkeypatch.setattr(apply_svc, "get_store", lambda: _NullTransactionStore())
    env["wfseq"].sequence = None
    env["wfseq"].items = []
    env["wfseq"].head_item_seq = 1
    plan_steps = [
        {
            "key": f"{instruction_type}#1", "type": instruction_type, "ordinal": 1,
            "locked": False, "provider_id": None, "note": note,
            "review_count": review_count, "reviewer_provider_id": None,
            "pre_instruction_text": pre_text, "pre_instruction_attachment": attachment,
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
    return result


def _worker_prompt(env, *, mention, target_seq, instruction_mode) -> str:
    """A real start_run hop with a fixed mention (used where no route is under test)."""
    cmd, outfile = h._capture_cmd(env["tmp"])
    env["chain"]["providers"] = [h._provider(cmd=cmd)]
    env["chain"]["registered_count"] = 1
    res = svc.start_run(
        project_id="flowgate", module="default", group_id=GROUP_ID, doc_ref=ROOT_DOC,
        action_scope="new", mode="continuous", continuation_target_seq=target_seq,
        continuation_review_mode=False, continuation_instruction_mode=instruction_mode,
        continuation_locale="ko", issued_to="usr_admin",
        api_base_url="http://127.0.0.1:1/flowgate/api/v1",
        mention_builder=lambda raw, scratch: mention,
    )
    h._wait_finished(res["run_id"])
    return h._read(outfile).decode("utf-8")


def _trace_auto_path(monkeypatch, events: list) -> list:
    """Wrap -- never replace -- every production step of the automatic path, in order.

    Each wrapper records that the step ran (and with what) and then calls the real function,
    so the trace is evidence of the real call chain, not a substitute for it.
    """
    adv_calls: list = []
    real_advance = workflow.advance_workflow
    real_auto_complete = workflow._auto_complete_instruction_heads
    real_materialize = docs.materialize_work_plan_instruction
    real_core = docs.create_next_approved_core
    real_document = docs._work_plan_instruction_document
    real_body = docs._work_plan_instruction_body

    def advance(**kw):
        adv_calls.append(dict(kw))
        events.append("advance_workflow")
        return real_advance(**kw)

    def auto_complete(**kw):
        events.append(f"auto_complete:{kw.get('instruction_mode')}")
        done = real_auto_complete(**kw)
        events.append(f"auto_complete_done:{done}")
        return done

    def materialize(**kw):
        head = kw["head"]
        events.append(
            f"materialize:{head.get('type')}:has_doc="
            f"{workflow.has_work_plan_instruction_document(head)}"
        )
        return real_materialize(**kw)

    def core(**kw):
        events.append("core")
        return real_core(**kw)

    def document(*args, **kw):
        found = real_document(*args, **kw)
        events.append(f"instruction_document:{found is not None}")
        return found

    def body(*args, **kw):
        events.append("body_builder")
        return real_body(*args, **kw)

    monkeypatch.setattr(workflow, "advance_workflow", advance)
    monkeypatch.setattr(workflow, "_auto_complete_instruction_heads", auto_complete)
    monkeypatch.setattr(docs, "materialize_work_plan_instruction", materialize)
    monkeypatch.setattr(docs, "create_next_approved_core", core)
    monkeypatch.setattr(docs, "_work_plan_instruction_document", document)
    monkeypatch.setattr(docs, "_work_plan_instruction_body", body)
    return adv_calls


def _post_ai_invoke(env, monkeypatch, events: list, *, instruction_mode, target_seq=2) -> dict:
    """Press [AI 호출]: the REAL POST /api/v1/ai-invoke/start route with the body
    ContinuousWorkDialog sends, through the real start_run and a real worker subprocess.

    Only the edges are replaced: the bearer check (who is calling), the token row insert
    (recorded into ``events`` so the ORDER of token issue vs. materialization is visible),
    and the provider CLI command (a Python one-liner that captures its stdin prompt).
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api.v1 import ai_invoke_routes

    cmd, outfile = h._capture_cmd(env["tmp"])
    env["chain"]["providers"] = [h._provider(cmd=cmd)]
    env["chain"]["registered_count"] = 1
    monkeypatch.setattr(
        ai_invoke_routes, "verify_bearer",
        lambda request: {"_is_user_jwt": True, "issued_to": "usr_admin", "is_admin": True},
    )
    issued: list = []

    def issue(**kw):
        issued.append(kw)
        events.append(f"token_issue:{kw.get('doc_ref')}")
        return {
            "raw_token": f"tok_raw_{len(issued)}", "token_id": f"tok_0611_{len(issued)}",
            "expires_at": "2026-09-26T00:00:00+09:00",
            "scratch_dir": str(env["tmp"] / f"tokwork{len(issued)}"),
        }

    monkeypatch.setattr(workflow.token_service, "issue", issue)
    adv_calls = _trace_auto_path(monkeypatch, events)

    app = FastAPI()
    app.include_router(ai_invoke_routes.router)
    client = TestClient(app, raise_server_exceptions=False)
    body = {
        "project": "flowgate", "module": "default", "group": "0611",
        "doc_ref": ROOT_DOC, "action_scope": "new", "mode": "continuous",
        "continuation_target_seq": target_seq,
        "continuation_review_mode": False,
        "continuation_instruction_mode": instruction_mode,
    }
    resp = client.post("/api/v1/ai-invoke/start", json=body,
                       headers={"Authorization": "Bearer user-jwt"})
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]
    run = h._wait_finished(run_id)
    return {
        "response": resp.json(), "run": run, "adv_calls": adv_calls, "issued": issued,
        "prompt": h._read(outfile).decode("utf-8"),
    }


def _assert_real_instruction_body(body: str, *, type_code: str, attachment: dict) -> None:
    """The canonical N/T is the WP instruction document, not the note and not a stub."""
    frontmatter, _sep, markdown = body.partition("\n---\n")
    assert f"type: {type_code}" in frontmatter
    assert f"source_wp_doc_id: {json.dumps(WP_DOC_ID)}" in frontmatter
    assert f"source_wp_step_key: {json.dumps(type_code + '#1')}" in frontmatter
    assert f"materialization_key: {json.dumps(f'{WP_DOC_ID}:1:{type_code}#1')}" in frontmatter
    assert f"content_source: {docs.WORK_PLAN_INSTRUCTION_CONTENT_SOURCE}" in frontmatter
    assert json.dumps(attachment, ensure_ascii=False, separators=(",", ":")) in frontmatter
    # The instruction file's own Markdown, CRLF-normalized, its upload frontmatter dropped.
    assert markdown.startswith("# 결제 모듈 회귀 수정\n\n## 요구사항\n\n")
    assert f"- {FILE_DIRECTIVE}\n" in markdown
    assert "title: uploaded draft" not in body
    assert "\r" not in body
    # The directly written half follows as its own section.
    assert f"## 추가 지시\n\n{PRE_TEXT}\n" in markdown
    # 0611 B0001: the one-line note is a message to the step's AI, never the body.
    assert PROBLEM_NOTE not in body
    # No placeholder of any earlier revision.
    assert "승인 절차 산출물" not in body
    assert "AI 작성 경로" not in body


# ── Path 1 · auto_approved: WP approval -> AI invoke -> server T/N md -> TR/NR AI work ──

@pytest.mark.parametrize("instruction_type", ["T", "N"])
def test_path1_auto_approved_ai_invoke_route_expands_real_instruction_then_next_worker_starts(
    pre_env, monkeypatch, tmp_path, instruction_type,
):
    """WP approval -> [AI 호출](auto_approved) -> T/N md -> approval -> the next TR/NR AI
    work, in one run, with no instruction-authoring AI."""
    instruction_mode = "auto_approved"
    attachment, _path = _install_instruction_file(monkeypatch, tmp_path, instruction_type)
    result = _apply_plan(pre_env, monkeypatch, attachment=attachment,
                         instruction_type=instruction_type, instruction_mode=instruction_mode)
    assert result["fill"]["pre_instruction_attachments"]["1"] == attachment
    # From here on the attachment is checked by the real digest validator.
    monkeypatch.setattr(wpa_svc, "validate_reference", _REAL_VALIDATE_REFERENCE)
    world = _wire_world(monkeypatch, tmp_path, pre_env["wfseq"])
    # Point 4 of the rejection: the predicate reads the row apply() actually wrote.
    assert workflow.has_work_plan_instruction_document(world.row(1)) is True

    events: list = []
    out = _post_ai_invoke(pre_env, monkeypatch, events, instruction_mode=instruction_mode)

    result_type = {"T": "TR", "N": "NR"}[instruction_type]
    canonical_id = f"{GROUP_ID}.0005-{instruction_type}"
    # 1-2. The route really calls advance_workflow once, with these real values.
    # The capture worker submits nothing, so the engine's real no-output retry re-enters
    # the same first-hop issuer once (attempt 2); both attempts are pinned below.
    assert out["run"]["attempts_used"] == 2
    assert len(out["adv_calls"]) == 2
    call = out["adv_calls"][0]
    assert call["doc_id"] == ROOT_DOC
    assert call["continuous"] is True
    assert call["continuation_instruction_mode"] == instruction_mode
    assert call["continuation_auto_approve_item_seqs"] == []
    assert call["continuation_target_seq"] == 2
    assert call["continuation_review_mode"] is False
    # 3-6. auto-complete runs under that mode, the row reads has_doc=True, the one shared
    # core + body builder expands it, and only AFTER that is exactly one token minted -- the
    # next (TR/NR) worker's. No T/N authoring token is ever issued.
    assert events == [
        "advance_workflow",
        f"auto_complete:{instruction_mode}",
        f"materialize:{instruction_type}:has_doc=True",
        "core",
        "instruction_document:True",
        "body_builder",
        "auto_complete_done:[1]",
        f"token_issue:{ROOT_DOC}",
        # attempt 2: the head is already the TR/NR, so nothing is expanded or numbered again.
        "advance_workflow",
        f"auto_complete:{instruction_mode}",
        "auto_complete_done:[]",
        f"token_issue:{ROOT_DOC}",
    ]
    assert out["adv_calls"][1]["continuation_instruction_mode"] == instruction_mode
    # T/N md generated and approved.
    assert world.reserved == [f"0005-{instruction_type}"]
    assert world.row(1)["result_doc_id"] == canonical_id
    assert world.docs[canonical_id]["doc_review_status"] == "approved"
    assert world.transitions == [(canonical_id, "submit"), (canonical_id, "approve")]
    _assert_real_instruction_body(
        world.body(canonical_id), type_code=instruction_type, attachment=attachment,
    )
    # The run itself records that the server handled slot 1 and the worker was the TR/NR.
    run = out["run"]
    assert run["worker_document_type"] == result_type
    assert run["auto_handled_item_seqs"] == [1]
    assert run["continuation_instruction_mode_normalized"] == instruction_mode
    # Next TR/NR AI work: the real subprocess got a prompt that references the canonical
    # T/N, and neither a second copy of the instruction nor the instruction step's note.
    prompt = out["prompt"]
    assert canonical_id.replace(".", "/") in prompt or canonical_id in prompt
    assert "## WorkPlan 사전지시" not in prompt
    assert PRE_TEXT not in prompt
    assert FILE_DIRECTIVE not in prompt
    assert PROBLEM_NOTE not in prompt


# ── Path 2 · ai_direct: no server expansion; the authoring AI gets the WP input ────────

@pytest.mark.parametrize("instruction_type", ["T", "N"])
def test_path2_ai_direct_ai_invoke_route_starts_authoring_ai_with_wp_input_and_never_expands(
    pre_env, monkeypatch, tmp_path, instruction_type,
):
    """WP approval -> [AI 호출](ai_direct) -> the N/T authoring AI (no server expansion) ->
    its authored N/T approved through the normal flow -> the next [AI 호출] reaches TR/NR."""
    attachment, _path = _install_instruction_file(monkeypatch, tmp_path, instruction_type)
    _apply_plan(pre_env, monkeypatch, attachment=attachment,
                instruction_type=instruction_type, instruction_mode="ai_direct")
    monkeypatch.setattr(wpa_svc, "validate_reference", _REAL_VALIDATE_REFERENCE)
    world = _wire_world(monkeypatch, tmp_path, pre_env["wfseq"])
    # The step DOES carry an instruction document -- ai_direct must still not expand it.
    assert workflow.has_work_plan_instruction_document(world.row(1)) is True

    events: list = []
    out = _post_ai_invoke(pre_env, monkeypatch, events, instruction_mode="ai_direct")

    call = out["adv_calls"][0]
    assert call["continuous"] is True
    assert call["continuation_instruction_mode"] == "ai_direct"
    assert call["continuation_auto_approve_item_seqs"] == []
    # _auto_complete_instruction_heads runs but expands nothing: no materializer, no core,
    # no body builder -- the only token minted per attempt is the N/T authoring worker's.
    assert events == [
        "advance_workflow", "auto_complete:ai_direct", "auto_complete_done:[]",
        f"token_issue:{ROOT_DOC}",
        # attempt 2 (the capture worker submitted nothing): same authoring hop again.
        "advance_workflow", "auto_complete:ai_direct", "auto_complete_done:[]",
        f"token_issue:{ROOT_DOC}",
    ]
    assert world.reserved == []
    assert world.transitions == []
    assert world.row(1)["result_doc_id"] is None
    run = out["run"]
    assert run["worker_document_type"] == instruction_type
    assert run["hop_item_seq"] == 1
    assert run["auto_handled_item_seqs"] == []
    assert run["continuation_instruction_mode_normalized"] == "ai_direct"
    # The authoring AI receives the step's note, written text and attached file as input.
    prompt = out["prompt"]
    assert prompt.count("## WorkPlan 사전지시") == 1
    assert PRE_TEXT in prompt
    assert attachment["original_filename"] in prompt
    assert PROBLEM_NOTE in prompt

    # The authoring AI's N/T goes through the ordinary submit -> approve flow (the full
    # review-gate variant is pinned by test_ai_invoke_pre_instruction_0554
    # TestConnectedFlowFullEffectiveBundle under ai_direct).
    authored_id = f"{GROUP_ID}.0005-{instruction_type}"
    authored_path = tmp_path / "authored.md"
    authored_path.write_text("# AI가 작성한 지시서\n", encoding="utf-8")
    world.create_document({"doc_id": authored_id, "project_id": "flowgate",
                           "group_id": GROUP_ID, "type_code": instruction_type,
                           "file_path": str(authored_path)})
    world.register(item_id=world.row(1)["id"], registered_doc_id=authored_id)
    world.transition(doc_id=authored_id, action="submit")
    world.transition(doc_id=authored_id, action="approve")

    events2: list = []
    out2 = _post_ai_invoke(pre_env, monkeypatch, events2, instruction_mode="ai_direct")
    assert "materialize" not in " ".join(events2)
    assert events2[:4] == [
        "advance_workflow", "auto_complete:ai_direct", "auto_complete_done:[]",
        f"token_issue:{ROOT_DOC}",
    ]
    assert world.reserved == []
    assert out2["run"]["worker_document_type"] == {"T": "TR", "N": "NR"}[instruction_type]
    assert out2["run"]["hop_item_seq"] == 2
    assert out2["run"]["auto_handled_item_seqs"] == []
    # The TR/NR worker gets no hidden second copy of the instruction-step input.
    assert "## WorkPlan 사전지시" not in out2["prompt"]
    assert PRE_TEXT not in out2["prompt"]


# ── Path 3 · manual [승인지시서 생성]: server T/N md, no AI call ─────────────────────

@pytest.mark.parametrize("pour_mode", ["auto_approved", "ai_direct"])
@pytest.mark.parametrize("instruction_type", ["T", "N"])
def test_path3_manual_create_approved_expands_real_instruction_without_ai(
    pre_env, monkeypatch, tmp_path, instruction_type, pour_mode,
):
    attachment, _path = _install_instruction_file(monkeypatch, tmp_path, instruction_type)
    _apply_plan(pre_env, monkeypatch, attachment=attachment, instruction_type=instruction_type,
                instruction_mode=pour_mode)
    monkeypatch.setattr(wpa_svc, "validate_reference", _REAL_VALIDATE_REFERENCE)
    world = _wire_world(monkeypatch, tmp_path, pre_env["wfseq"])

    import test_next_approved_document as nad

    created = docs.create_next_approved_document(
        docs.NextApprovedDocumentCreate(
            project_id="flowgate", group_id=GROUP_ID, prev_doc_id=ROOT_DOC,
            type_code=instruction_type, module="default",
        ),
        request=nad._FakeRequest({"X-Locale": "ko"}),
        current_user={"user_id": "usr_admin"},
    )

    canonical_id = f"{GROUP_ID}.0005-{instruction_type}"
    assert created["doc_id"] == canonical_id
    assert world.docs[canonical_id]["doc_review_status"] == "approved"
    assert world.docs[canonical_id]["title"].endswith(f"{instruction_type}#1")
    assert world.row(1)["result_doc_id"] == canonical_id
    _assert_real_instruction_body(
        world.body(canonical_id), type_code=instruction_type, attachment=attachment,
    )
    # No AI was called on this path.
    assert svc._runs == {}


def test_manual_and_ai_invoke_paths_expand_byte_identical_instruction(
    pre_env, monkeypatch, tmp_path,
):
    """Both server-expanding entry points (manual, [AI 호출] auto_approved) reach the same
    core + body builder, so they cannot drift apart."""
    bodies = []
    traces = {}
    for entry in ("manual", "ai_invoke"):
        sub = tmp_path / entry
        sub.mkdir()
        attachment, _path = _install_instruction_file(monkeypatch, sub)
        monkeypatch.setattr(wpa_svc, "validate_reference", lambda doc_id, reference: None)
        _apply_plan(pre_env, monkeypatch, attachment=attachment, instruction_mode="auto_approved")
        monkeypatch.setattr(wpa_svc, "validate_reference", _REAL_VALIDATE_REFERENCE)
        world = _wire_world(monkeypatch, sub, pre_env["wfseq"])
        events: list = []
        if entry == "manual":
            _trace_auto_path(monkeypatch, events)
            import test_next_approved_document as nad

            docs.create_next_approved_document(
                docs.NextApprovedDocumentCreate(
                    project_id="flowgate", group_id=GROUP_ID, prev_doc_id=ROOT_DOC,
                    type_code="T", module="default",
                ),
                request=nad._FakeRequest({"X-Locale": "ko"}),
                current_user={"user_id": "usr_admin"},
            )
        else:
            _post_ai_invoke(pre_env, monkeypatch, events, instruction_mode="auto_approved")
        traces[entry] = [
            e for e in events if e in ("core", "instruction_document:True", "body_builder")
        ]
        bodies.append(world.body(f"{GROUP_ID}.0005-T"))
    # Point 6: the same materializer core and the same Markdown builder on both paths.
    assert traces["manual"] == traces["ai_invoke"] == [
        "core", "instruction_document:True", "body_builder",
    ]
    assert bodies[0] == bodies[1]


def test_text_only_instruction_document_gets_a_title_heading(pre_env, monkeypatch, tmp_path):
    monkeypatch.setattr(wpa_svc, "validate_reference", lambda doc_id, reference: None)
    _apply_plan(pre_env, monkeypatch, attachment=None)
    world = _wire_world(monkeypatch, tmp_path, pre_env["wfseq"])
    docs.create_next_approved_core(
        project_id="flowgate", group_id=GROUP_ID, module="default", prev_doc_id=ROOT_DOC,
        type_code="T", actor_user_id="usr_admin", approver_perms={"document.approve"},
    )
    body = world.body(f"{GROUP_ID}.0005-T")
    markdown = body.partition("\n---\n")[2]
    assert markdown == f"# 작업지시 — T#1\n\n{PRE_TEXT}\n"
    assert "source_wp_attachment" not in body
    assert PROBLEM_NOTE not in body


# ── A WorkPlan N/T with no instruction document: ai_direct keeps the authoring hop,
# auto_approved/manual now fall back to the note (0614 T0004 override) ─────────────

def test_note_only_workplan_step_ai_direct_keeps_authoring_hop(pre_env, monkeypatch, tmp_path):
    """0611 T0009 contract retained for ai_direct: 0614 T0004 only changes manual and
    auto_approved -- ai_direct still never server-expands a WorkPlan step, document or not."""
    instruction_mode = "ai_direct"
    monkeypatch.setattr(wpa_svc, "validate_reference", lambda doc_id, reference: None)
    _apply_plan(pre_env, monkeypatch, attachment=None, pre_text=None,
                instruction_mode=instruction_mode)
    world = _wire_world(monkeypatch, tmp_path, pre_env["wfseq"])
    assert world.row(1)["note"] == PROBLEM_NOTE
    assert not workflow.has_work_plan_instruction_document(world.row(1))

    monkeypatch.setattr(docs, "materialize_work_plan_instruction", lambda **_kw: pytest.fail(
        "ai_direct must never server-expand a WorkPlan step"
    ))
    assert workflow._auto_complete_instruction_heads(
        spine_doc=world.docs[ROOT_DOC], seq={"id": 1}, actor_user_id="usr_admin",
        locale="ko", target_seq=2, instruction_mode=instruction_mode,
    ) == []
    assert admission._hop_worker_item_seq(
        1, world.row(1), continuation_instruction_mode=instruction_mode,
        continuation_auto_approve_item_seqs=[],
    ) == 1
    # The T authoring worker receives its note.
    prompt = _worker_prompt(pre_env, mention=h.MENTION, target_seq=2,
                            instruction_mode=instruction_mode)
    assert PROBLEM_NOTE in prompt


def test_note_only_workplan_step_auto_approved_materializes_from_note(
    pre_env, monkeypatch, tmp_path,
):
    """0614 T0004 human override of the 0611 rej_01M3AVQVHD6PSTBE final contract: with no
    instruction file/pre_instruction_text, auto_approved now server-materializes the step
    from its note instead of leaving it an authoring hop. The B0001 self-reference risk is
    accepted deliberately here -- this is not the bug B0001 fixed, it is a later, explicit
    override of the fix's fallback rule."""
    instruction_mode = "auto_approved"
    monkeypatch.setattr(wpa_svc, "validate_reference", lambda doc_id, reference: None)
    _apply_plan(pre_env, monkeypatch, attachment=None, pre_text=None,
                instruction_mode=instruction_mode)
    world = _wire_world(monkeypatch, tmp_path, pre_env["wfseq"])
    assert world.row(1)["note"] == PROBLEM_NOTE
    assert not workflow.has_work_plan_instruction_document(world.row(1))
    assert workflow.is_auto_handled_step(
        head_type="T", item_seq=1, instruction_mode=instruction_mode,
        auto_approve_item_seqs=[], source_doc_id=world.row(1)["source_doc_id"],
        has_instruction_document=False,
    ) is True

    completed = workflow._auto_complete_instruction_heads(
        spine_doc=world.docs[ROOT_DOC], seq={"id": 1}, actor_user_id="usr_admin",
        locale="ko", target_seq=2, instruction_mode=instruction_mode,
    )
    assert completed == [1]
    canonical_id = f"{GROUP_ID}.0005-T"
    assert world.row(1)["result_doc_id"] == canonical_id
    body = world.body(canonical_id)
    frontmatter, _sep, markdown = body.partition("\n---\n")
    assert f"content_source: {docs.WORK_PLAN_STEP_NOTE_CONTENT_SOURCE}" in frontmatter
    assert markdown == f"# 작업지시 — T#1\n\n{PROBLEM_NOTE}\n"
    assert "source_wp_attachment" not in body


def test_manual_no_file_no_note_creates_legacy_generic_instruction(pre_env, monkeypatch, tmp_path):
    """0614 T0004 §4 case A: manual [승인지시서 생성] no longer 409s when a WorkPlan step
    carries neither an instruction document nor a note -- it creates the same legacy generic
    approval instruction a non-WorkPlan auto-approved document would (the 409 this test used
    to pin is the 0611 rej_01M3AVQVHD6PSTBE contract 0614 T0004 explicitly supersedes)."""
    monkeypatch.setattr(wpa_svc, "validate_reference", lambda doc_id, reference: None)
    _apply_plan(pre_env, monkeypatch, attachment=None, pre_text=None, note="")
    world = _wire_world(monkeypatch, tmp_path, pre_env["wfseq"])
    assert world.row(1)["note"] == ""
    assert not workflow.has_work_plan_instruction_document(world.row(1))

    created = docs.create_next_approved_core(
        project_id="flowgate", group_id=GROUP_ID, module="default", prev_doc_id=ROOT_DOC,
        type_code="T", actor_user_id="usr_admin", approver_perms={"document.approve"},
    )
    canonical_id = f"{GROUP_ID}.0005-T"
    assert created["doc_id"] == canonical_id
    assert created["content_source"] == docs.WORK_PLAN_LEGACY_CONTENT_SOURCE
    body = world.body(canonical_id)
    frontmatter, _sep, _markdown = body.partition("\n---\n")
    assert f"content_source: {docs.WORK_PLAN_LEGACY_CONTENT_SOURCE}" in frontmatter
    assert "source_wp_doc_id" in frontmatter
    assert world.reserved == ["0005-T"]


def test_manual_no_file_note_present_creates_note_body(pre_env, monkeypatch, tmp_path):
    """0614 T0004 §4 case B: no instruction document, note present -> note becomes the body."""
    monkeypatch.setattr(wpa_svc, "validate_reference", lambda doc_id, reference: None)
    _apply_plan(pre_env, monkeypatch, attachment=None, pre_text=None)
    world = _wire_world(monkeypatch, tmp_path, pre_env["wfseq"])
    assert world.row(1)["note"] == PROBLEM_NOTE

    created = docs.create_next_approved_core(
        project_id="flowgate", group_id=GROUP_ID, module="default", prev_doc_id=ROOT_DOC,
        type_code="T", actor_user_id="usr_admin", approver_perms={"document.approve"},
    )
    canonical_id = f"{GROUP_ID}.0005-T"
    assert created["content_source"] == docs.WORK_PLAN_STEP_NOTE_CONTENT_SOURCE
    body = world.body(canonical_id)
    markdown = body.partition("\n---\n")[2]
    assert markdown == f"# 작업지시 — T#1\n\n{PROBLEM_NOTE}\n"


# ── 0614 T0004 §10.1-10.3 A/B/C/D matrix: the cells rej_01M3BS9K16YGAMXR found missing.
# Case A = no file/no note, case C = file present/no note. Case B (note-only) and case D
# (file+note) are already pinned above (test_note_only_workplan_step_*, test_path1/2/3) --
# this block adds the remaining cells: case A through the REAL route in auto_approved
# (legacy body + no separate authoring token) and the authoring hop in ai_direct, and case C
# (a clean "file only" reading with no note to merge, distinct from test_path1/3's default
# fixture which pours attachment + pre_instruction_text + note together and is case D, not
# C) through manual, auto_approved and ai_direct. ──────────────────────────────────────────

def test_case_a_auto_approved_ai_invoke_route_materializes_legacy_and_skips_authoring_token(
    pre_env, monkeypatch, tmp_path,
):
    """0614 T0004 §4 case A through the REAL ``POST /api/v1/ai-invoke/start`` route: with
    neither an instruction document nor a note, auto_approved server-materializes the
    legacy generic body (content_source=work_plan_legacy_generated) -- exactly like case D
    (test_path1) except no ``body_builder`` call, since there is no document to expand --
    and, like every other cell of this matrix, issues no separate T authoring token: only
    the next TR worker's token is ever minted."""
    instruction_mode = "auto_approved"
    _apply_plan(pre_env, monkeypatch, attachment=None, instruction_type="T",
               instruction_mode=instruction_mode, pre_text=None, note="")
    world = _wire_world(monkeypatch, tmp_path, pre_env["wfseq"])
    assert world.row(1)["note"] == ""
    assert workflow.has_work_plan_instruction_document(world.row(1)) is False

    events: list = []
    out = _post_ai_invoke(pre_env, monkeypatch, events, instruction_mode=instruction_mode)

    canonical_id = f"{GROUP_ID}.0005-T"
    assert out["run"]["attempts_used"] == 2
    # No "body_builder" entry: the legacy branch never calls _work_plan_instruction_body.
    assert events == [
        "advance_workflow",
        f"auto_complete:{instruction_mode}",
        "materialize:T:has_doc=False",
        "core",
        "instruction_document:False",
        "auto_complete_done:[1]",
        f"token_issue:{ROOT_DOC}",
        # attempt 2: the head is already the TR, so nothing is expanded or numbered again.
        "advance_workflow",
        f"auto_complete:{instruction_mode}",
        "auto_complete_done:[]",
        f"token_issue:{ROOT_DOC}",
    ]
    # Only ROOT_DOC's continuation token is ever issued -- no separate T authoring token.
    assert [i["doc_ref"] for i in out["issued"]] == [ROOT_DOC, ROOT_DOC]
    assert world.reserved == [f"0005-T"]
    assert world.row(1)["result_doc_id"] == canonical_id
    assert world.docs[canonical_id]["doc_review_status"] == "approved"
    body = world.body(canonical_id)
    frontmatter, _sep, _markdown = body.partition("\n---\n")
    assert f"content_source: {docs.WORK_PLAN_LEGACY_CONTENT_SOURCE}" in frontmatter
    assert "작업지시 가 승인되었습니다." in body
    assert PROBLEM_NOTE not in body
    run = out["run"]
    assert run["worker_document_type"] == "TR"
    assert run["auto_handled_item_seqs"] == [1]
    prompt = out["prompt"]
    assert PROBLEM_NOTE not in prompt


def test_case_a_ai_direct_keeps_authoring_hop_with_no_file_and_no_note(
    pre_env, monkeypatch, tmp_path,
):
    """0614 T0004 §4 case A under ai_direct: with neither an instruction document nor a
    note, ai_direct still never server-materializes -- the first worker stays the T
    authoring hop, exactly as the 0611 T0009 contract for a documentless step. This is
    distinct from test_note_only_workplan_step_ai_direct_keeps_authoring_hop (case B: no
    file, but a note IS present) -- here neither source exists at all."""
    instruction_mode = "ai_direct"
    _apply_plan(pre_env, monkeypatch, attachment=None, pre_text=None, note="",
               instruction_mode=instruction_mode)
    world = _wire_world(monkeypatch, tmp_path, pre_env["wfseq"])
    assert world.row(1)["note"] == ""
    assert not workflow.has_work_plan_instruction_document(world.row(1))

    monkeypatch.setattr(docs, "materialize_work_plan_instruction", lambda **_kw: pytest.fail(
        "ai_direct must never server-expand a WorkPlan step"
    ))
    assert workflow._auto_complete_instruction_heads(
        spine_doc=world.docs[ROOT_DOC], seq={"id": 1}, actor_user_id="usr_admin",
        locale="ko", target_seq=2, instruction_mode=instruction_mode,
    ) == []
    assert admission._hop_worker_item_seq(
        1, world.row(1), continuation_instruction_mode=instruction_mode,
        continuation_auto_approve_item_seqs=[],
    ) == 1
    # No note/section is injected when the step carries neither a file nor a note -- the
    # fixed mention passes through _inject_hop_notes untouched (only its unrelated runtime
    # boilerplate footer is appended, same as any other single-hop prompt).
    prompt = _worker_prompt(pre_env, mention=h.MENTION, target_seq=2,
                            instruction_mode=instruction_mode)
    assert prompt.startswith(h.MENTION)
    assert "## WorkPlan 사전지시" not in prompt
    assert PROBLEM_NOTE not in prompt


def test_case_c_manual_file_no_note_uses_file_only(pre_env, monkeypatch, tmp_path):
    """0614 T0004 §4 case C: instruction file present, steps[].note absent. The file wins
    outright -- same as case D (file+note) -- but this reading is a clean "file only" case
    with no note and no pre_instruction_text at all, unlike test_path1/test_path3 which
    always pour attachment + pre_instruction_text + note together via _apply_plan()'s
    defaults (case D)."""
    attachment, _path = _install_instruction_file(monkeypatch, tmp_path)
    _apply_plan(pre_env, monkeypatch, attachment=attachment, pre_text=None, note="")
    monkeypatch.setattr(wpa_svc, "validate_reference", _REAL_VALIDATE_REFERENCE)
    world = _wire_world(monkeypatch, tmp_path, pre_env["wfseq"])
    assert world.row(1)["note"] == ""
    assert workflow.has_work_plan_instruction_document(world.row(1)) is True

    created = docs.create_next_approved_core(
        project_id="flowgate", group_id=GROUP_ID, module="default", prev_doc_id=ROOT_DOC,
        type_code="T", actor_user_id="usr_admin", approver_perms={"document.approve"},
    )
    canonical_id = f"{GROUP_ID}.0005-T"
    assert created["content_source"] == docs.WORK_PLAN_INSTRUCTION_CONTENT_SOURCE
    body = world.body(canonical_id)
    frontmatter, _sep, markdown = body.partition("\n---\n")
    assert markdown == f"# 결제 모듈 회귀 수정\n\n## 요구사항\n\n- {FILE_DIRECTIVE}\n"
    assert "## 추가 지시" not in markdown
    assert PROBLEM_NOTE not in body


def test_case_c_auto_approved_ai_invoke_route_expands_file_only_and_skips_authoring_token(
    pre_env, monkeypatch, tmp_path,
):
    """0614 T0004 §4 case C through the REAL AI-invoke route: the file wins with no note to
    merge, and -- like every other cell -- no separate T authoring token is issued."""
    instruction_mode = "auto_approved"
    attachment, _path = _install_instruction_file(monkeypatch, tmp_path, "T")
    _apply_plan(pre_env, monkeypatch, attachment=attachment, instruction_type="T",
               instruction_mode=instruction_mode, pre_text=None, note="")
    monkeypatch.setattr(wpa_svc, "validate_reference", _REAL_VALIDATE_REFERENCE)
    world = _wire_world(monkeypatch, tmp_path, pre_env["wfseq"])
    assert workflow.has_work_plan_instruction_document(world.row(1)) is True
    assert world.row(1)["note"] == ""

    events: list = []
    out = _post_ai_invoke(pre_env, monkeypatch, events, instruction_mode=instruction_mode)

    canonical_id = f"{GROUP_ID}.0005-T"
    assert events == [
        "advance_workflow",
        f"auto_complete:{instruction_mode}",
        "materialize:T:has_doc=True",
        "core",
        "instruction_document:True",
        "body_builder",
        "auto_complete_done:[1]",
        f"token_issue:{ROOT_DOC}",
        "advance_workflow",
        f"auto_complete:{instruction_mode}",
        "auto_complete_done:[]",
        f"token_issue:{ROOT_DOC}",
    ]
    assert [i["doc_ref"] for i in out["issued"]] == [ROOT_DOC, ROOT_DOC]
    body = world.body(canonical_id)
    frontmatter, _sep, markdown = body.partition("\n---\n")
    assert markdown == f"# 결제 모듈 회귀 수정\n\n## 요구사항\n\n- {FILE_DIRECTIVE}\n"
    assert "## 추가 지시" not in markdown
    assert PROBLEM_NOTE not in body
    prompt = out["prompt"]
    assert PROBLEM_NOTE not in prompt
    assert FILE_DIRECTIVE not in prompt


def test_case_c_ai_direct_ai_invoke_route_starts_authoring_ai_with_file_input_and_no_note(
    pre_env, monkeypatch, tmp_path,
):
    """0614 T0004 §4 case C under ai_direct: unaffected by the new source resolution -- the
    authoring AI still receives the file as input and the server never expands it."""
    attachment, _path = _install_instruction_file(monkeypatch, tmp_path, "T")
    _apply_plan(pre_env, monkeypatch, attachment=attachment, instruction_type="T",
               instruction_mode="ai_direct", pre_text=None, note="")
    monkeypatch.setattr(wpa_svc, "validate_reference", _REAL_VALIDATE_REFERENCE)
    world = _wire_world(monkeypatch, tmp_path, pre_env["wfseq"])
    assert workflow.has_work_plan_instruction_document(world.row(1)) is True
    assert world.row(1)["note"] == ""

    events: list = []
    out = _post_ai_invoke(pre_env, monkeypatch, events, instruction_mode="ai_direct")

    assert events == [
        "advance_workflow", "auto_complete:ai_direct", "auto_complete_done:[]",
        f"token_issue:{ROOT_DOC}",
        "advance_workflow", "auto_complete:ai_direct", "auto_complete_done:[]",
        f"token_issue:{ROOT_DOC}",
    ]
    assert world.reserved == []
    prompt = out["prompt"]
    assert prompt.count("## WorkPlan 사전지시") == 1
    assert attachment["original_filename"] in prompt
    assert PROBLEM_NOTE not in prompt


def test_broken_instruction_file_fails_closed_before_numbering(pre_env, monkeypatch, tmp_path):
    attachment, path = _install_instruction_file(monkeypatch, tmp_path)
    _apply_plan(pre_env, monkeypatch, attachment=attachment)
    monkeypatch.setattr(wpa_svc, "validate_reference", _REAL_VALIDATE_REFERENCE)
    world = _wire_world(monkeypatch, tmp_path, pre_env["wfseq"])
    path.write_bytes(b"# tampered after WP approval\n")
    for call in (
        lambda: docs.create_next_approved_core(
            project_id="flowgate", group_id=GROUP_ID, module="default", prev_doc_id=ROOT_DOC,
            type_code="T", actor_user_id="usr_admin", approver_perms={"document.approve"},
        ),
        lambda: workflow._auto_complete_instruction_heads(
            spine_doc=world.docs[ROOT_DOC], seq={"id": 1}, actor_user_id="usr_admin",
            locale="ko", target_seq=2, instruction_mode="auto_approved",
        ),
    ):
        with pytest.raises((docs.NextApprovedError, ValueError)) as exc:
            call()
        assert "pre_instruction_attachment_digest_mismatch" in str(
            getattr(exc.value, "detail", exc.value)
        )
    assert world.reserved == []
    assert world.row(1)["result_doc_id"] is None


# ── review_count > 0 · the gate sees the real expanded document; re-entry reuses it ──

def test_review_gate_receives_real_instruction_pending_review_and_reentry_reuses_it(
    pre_env, monkeypatch, tmp_path,
):
    attachment, _path = _install_instruction_file(monkeypatch, tmp_path)
    _apply_plan(pre_env, monkeypatch, attachment=attachment, review_count=2)
    monkeypatch.setattr(wpa_svc, "validate_reference", _REAL_VALIDATE_REFERENCE)
    world = _wire_world(monkeypatch, tmp_path, pre_env["wfseq"])
    assert world.row(1)["review_count"] == 2

    bundle = {
        "materialize_instruction_before_gate": True,
        "doc_ref": ROOT_DOC, "issued_to": "usr_admin", "locale": "ko",
        "target_seq": 2, "instruction_mode": "auto_approved",
    }
    # Pending review does not advance the head, so nothing is reported as auto-completed.
    assert review._materialize_work_plan_instruction_before_gate(bundle) == []
    canonical_id = f"{GROUP_ID}.0005-T"
    assert world.row(1)["result_doc_id"] == canonical_id
    assert world.docs[canonical_id]["doc_review_status"] == "pending_review"
    assert world.transitions == [(canonical_id, "submit")]
    _assert_real_instruction_body(world.body(canonical_id), type_code="T", attachment=attachment)
    # The reviewer's slot is that real document.
    slot = review._pending_review_slot(ROOT_DOC)
    assert slot is not None and slot["doc_id"] == canonical_id

    # Re-entry on the occupied slot reuses the same document through its marker.
    again = docs.materialize_work_plan_instruction(
        project_id="flowgate", group_id=GROUP_ID, module="default", prev_doc_id=ROOT_DOC,
        sequence_id=1, head=world.row(1), actor_user_id="usr_admin",
        approver_perms={"document.approve"},
    )
    assert again["idempotent_reuse"] is True
    assert again["doc_id"] == canonical_id
    assert again["content_source"] == docs.WORK_PLAN_INSTRUCTION_CONTENT_SOURCE
    assert world.reserved == ["0005-T"]


# ── the shared predicate · server expansion is auto_approved-only for WorkPlan rows ────

def test_workplan_instruction_document_is_server_expanded_only_under_auto_approved():
    """The single predicate every consumer shares (auto-complete loop, docs target, hop
    provider, hop item_seq, review eligibility) -- pinned directly for all row kinds."""
    wp_t = {"type": "T", "item_seq": 1, "source_doc_id": WP_DOC_ID,
            "pre_instruction_text": PRE_TEXT, "pre_instruction_attachment_json": None}
    wp_file_n = {**wp_t, "type": "N", "pre_instruction_text": None,
                 "pre_instruction_attachment_json": '{"filename": "x.md"}'}
    note_only_t = {**wp_t, "pre_instruction_text": None}
    legacy_t = {"type": "T", "item_seq": 1, "source_doc_id": None}

    def handled(row, mode, selection=None):
        return workflow.is_auto_handled_step(
            head_type=row["type"], item_seq=row["item_seq"], instruction_mode=mode,
            auto_approve_item_seqs=selection, source_doc_id=row.get("source_doc_id"),
            has_instruction_document=workflow.has_work_plan_instruction_document(row),
        )

    for row in (wp_t, wp_file_n):
        # auto_approved -> server expansion.
        assert handled(row, "auto_approved") is True
        # ai_direct -> authoring AI, even when the step is in the per-step selection.
        assert handled(row, "ai_direct") is False
        assert handled(row, "ai_direct", [1]) is False
    # 0614 T0004 (human override of 0611 rej_01M3AVQVHD6PSTBE): a WorkPlan row with no
    # instruction document is now ALWAYS server-materialized under auto_approved --
    # documents.py's source resolution falls back to steps[].note or the legacy generated
    # instruction instead of an authoring hop. ai_direct is untouched.
    assert handled(note_only_t, "auto_approved") is True
    assert handled(note_only_t, "ai_direct") is False
    # Legacy (non-WorkPlan) rows keep the 0352 mode contract untouched.
    assert handled(legacy_t, "auto_approved") is True
    assert handled(legacy_t, "ai_direct") is False
    assert handled(legacy_t, "ai_direct", [1]) is True


def test_plan_to_rows_attach_auto_rows_keeps_instruction_document_on_source_only():
    """The final-expansion pour keeps the instruction document on the source N/T row."""
    attachment = {"doc_id": WP_DOC_ID, "filename": f"{wpa_svc.RESERVED_PREFIX}T-1__b.md",
                  "original_filename": "b.md", "content_sha256": "b" * 64}
    plan = {
        "steps": [
            {"key": "T#1", "type": "T", "pair_key": "TR#1", "pair_role": "instruction",
             "note": PROBLEM_NOTE, "pre_instruction_text": PRE_TEXT,
             "pre_instruction_attachment": attachment},
            {"key": "TR#1", "type": "TR", "pair_key": "T#1", "pair_role": "result", "note": ""},
        ],
        "defaults": {"note": ""},
    }
    rows, dropped, next_uid = wpseq.plan_to_rows(plan, WP_DOC_ID, 1)
    assert dropped == []
    rows, _next_uid = wpseq.attach_auto_rows(rows, next_uid=next_uid)
    source, paired = rows
    assert [source["type"], paired["type"]] == ["T", "TR"]
    assert source["pre_instruction_text"] == PRE_TEXT
    assert source["pre_instruction_attachment"] == attachment
    assert workflow.has_work_plan_instruction_document(source)
    assert paired["pre_instruction_text"] is None
    assert paired["pre_instruction_attachment"] is None
    assert not workflow.has_work_plan_instruction_document(paired)
