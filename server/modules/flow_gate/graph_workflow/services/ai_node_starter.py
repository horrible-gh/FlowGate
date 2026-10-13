"""The AI start of a Graph document node (L0009 §2.6.6 ``start_ai_invoke``, D0004 §3.4 / §3.5).

A post-commit job: the guard that opened the node has committed and released (L0009 §2.1.4). The
node is started once, single mode, through the existing AI invoke service — called, never
changed — exactly as a v0.1 next step is (``action_scope="new"``, ``doc_ref`` = the Flow's root
document, the mention of ``mention_service.build_mention_from_token_rec`` for the node's
document type).

- The AI invoke service mints the run's own token through ``issue_builder`` so the token carries
  the AI run id (its group lease admits only that token). The node executor's token stays valid
  for a person (L0009 E-23); the AI token is recorded with ``map_token`` in a new guard before the
  worker can submit (L0009 §2.6.6 "호출이 돌려준 token_id 를 커밋 뒤 새 가드 구간에서
  map_token"). The result binder accepts either token, first result only.
- Any refusal (no root document to work from, no provider, a group AI run already in progress,
  the node no longer open) raises; ``node_dispatch.start_ai`` then records ``ai_invoke_failed``.
- flowgate.v02.0053 T#2: the card's run settings ride the job (``node_dispatch``): its time limit is
  the run's budget (``continuation_step_timeout_sec``, honoured by a single run since 0446 T0010) and
  its restarts are the no-output retries on the same AI (``node_restart_max_attempts``). ``None`` keeps
  the engine's own single-run default (the per-document budget, no restart). The mention asks for the
  job's ``result_doc_type``: the node's own type, or the card's report when its instruction is omitted
  (the result binder binds that same type, asking the card as the dispatch did).
- The card's review (review AI / count / basis) and what follows it (the approval) are not part of this
  start: they begin when this run has ended and released the group lease (``ai_run_end_hooks``).
- flowgate.v02.0056 T#2: the same start serves both stages of an AI card with its instruction used
  (``node_dispatch``). The instruction stage asks for the card's instruction type; when the card's
  instruction line already has its (empty) document the AI writes into it — ``action_scope`` edit on that
  document, the shape of the v0.1 edit AI invoke, so no new document is made — with the Flow's root
  attached as a reference. The result stage asks for the node's result as before, with the instruction
  attached as a reference document (``ref_doc_ids``: the mention's reference section, as v0.1 hands a next
  step its instruction). The run's token is mapped with the stage it was started for (``map_token``'s ``stage``):
  what the instruction AI registers with it is never the node's result, however late its registration lands.
- flowgate.v02.0056 T#3: the card's memos turned on for AI reading open the mention of both stages (and of every
  reissue of the run's token) as one "User memos" section (``card_memo_hooks``); none, or no handler: unchanged.
- flowgate.v02.0056 T#3: a person may register the node's result with its dispatch token (the card line write) once
  the AI start holds the group lease but before its token is mapped. The mapping guard then finds that result
  registered (its consume is committed before the Graph is told) and refuses the start, as for a node no longer
  open: the token is revoked and the AI never runs on a node a person already answered. The judgement is the run
  reconciliation's own (``result_binder.next_missing_event``), so it sees what the contact or a reconciliation
  binds next, whoever registered it. The line write refuses in turn once an AI token is mapped while its run
  holds the lease (``node_ai_working``): under the Flow guard, whichever comes first wins.
"""
from __future__ import annotations

from typing import Optional

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services import ai_invoke_service, card_memo_hooks, mention_service, token_service

from .flow_guard import with_flow_guard

API_BASE_URL = "/flowgate/api/v1"            # operator base; the agent base is resolved downstream


class NodeNotStartable(RuntimeError):
    """The node cannot be handed to the AI (the reason is the message)."""


def _module_of(flow_id: str) -> Optional[str]:
    parts = flow_id.split(".")
    return parts[1] if len(parts) > 2 and parts[1] != "none" else None


def _map_if_open(job: dict, token_id: str) -> None:
    def body() -> None:
        run = run_store.run_by_id(job["run_id"])                   # re-read inside the guard (L0009 §2.1.1)
        row = next((r for r in run_store.node_runs(job["run_id"]) if r["node_key"] == job["node_key"]), None)
        if run is None or run_store.completed(run) or row is None or row["status"] != "open":
            raise NodeNotStartable(f"node {job['node_key']} is no longer open")
        from modules.flow_gate.services import graph_execution_hooks
        graph_execution_hooks.call("validate_write", job)
        if _result_registered(run, job):
            raise NodeNotStartable(f"node {job['node_key']} already has a registered result")
        run_store.map_token(token_id, job["run_id"], job["node_key"], job.get("stage"))

    with_flow_guard(job["flow_id"], body)


def _result_registered(run: dict, job: dict) -> bool:
    """Inside the mapping guard: a result of the node is registered but not bound yet (the next event the run
    reconciliation would apply to the node is ``result_submitted``)."""
    from . import result_binder                                   # noqa: PLC0415 - the run engine, on use
    from .run_apply import load_context, read_run_state           # noqa: PLC0415

    ctx = load_context(run, job.get("project_id"), None, [])
    event = result_binder.next_missing_event(ctx, read_run_state(ctx), job["node_key"])
    return event is not None and event.kind == "result_submitted"


def start_ai_invoke(job: dict) -> dict:
    """Start the node's AI once (``job``: flow, run, node, provider, issued_to, doc_ref, doc_type,
    result_doc_type; ``action_scope`` edit with ``target_doc_id`` to write into that document, and
    ``ref_doc_ids`` for the mention's reference documents)."""
    doc_ref = job.get("doc_ref")
    parent = db_documents.get_by_id(doc_ref) if doc_ref else None
    if parent is None:
        raise NodeNotStartable("the Flow has no root document for the AI to work from")
    project_id, flow_id = job.get("project_id") or "", job["flow_id"]
    scope = "edit" if job.get("action_scope") == "edit" else "new"
    if scope == "edit":
        parent = db_documents.get_by_id(job.get("target_doc_id") or "")
        if parent is None:
            raise NodeNotStartable("the card's instruction document is gone")
        doc_ref = parent["doc_id"]
    ref_doc_ids = [d for d in job.get("ref_doc_ids") or [] if d and d != doc_ref] or None

    def _issue(ai_run_id: Optional[str] = None) -> dict:
        issue = token_service.issue(
            project=project_id, group_id=flow_id, action_scope=scope, doc_ref=doc_ref,
            issued_to=job["issued_to"], provider_id=job.get("provider_id"), ai_run_id=ai_run_id,
        )
        try:
            _map_if_open(job, issue["token_id"])
        except Exception:
            token_service.revoke(issue["token_id"], reason="graph_node_not_open")
            raise
        mention = mention_service.build_mention_from_token_rec(
            token_rec={"project": project_id, "group_id": flow_id, "scratch_dir": issue["scratch_dir"]},
            head_type=job.get("result_doc_type") or job["doc_type"], head_status="pending", parent_doc=parent,
            api_base_url=API_BASE_URL, raw_token=issue["raw_token"], ref_doc_ids=ref_doc_ids, action_scope=scope,
        )
        mention = card_memo_hooks.prepend_memo_section(mention or "", flow_id, node_key=job["node_key"])
        return {"raw_token": issue["raw_token"], "token_id": issue["token_id"],
                "scratch_dir": issue["scratch_dir"], "mention": mention or ""}

    return ai_invoke_service.start_run(
        project_id=project_id, module=_module_of(flow_id), group_id=flow_id, doc_ref=doc_ref,
        action_scope=scope, mode="single", continuation_target_seq=None, continuation_review_mode=False,
        continuation_instruction_mode=None, continuation_locale=None, issued_to=job["issued_to"],
        api_base_url=API_BASE_URL, mention_builder=lambda _raw, _scratch: None,
        provider_id=job.get("provider_id"), issue_builder=_issue,
        continuation_step_timeout_sec=job.get("step_timeout_sec"),
        node_restart_max_attempts=job.get("restart_max"),
        graph_exec_id=job.get("graph_exec_id"),
    )
