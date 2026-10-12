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
"""
from __future__ import annotations

from typing import Optional

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services import ai_invoke_service, mention_service, token_service

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
        if run is None or run["status"] == "completed" or row is None or row["status"] != "open":
            raise NodeNotStartable(f"node {job['node_key']} is no longer open")
        run_store.map_token(token_id, job["run_id"], job["node_key"])

    with_flow_guard(job["flow_id"], body)


def start_ai_invoke(job: dict) -> dict:
    """Start the node's AI once (``job``: flow, run, node, provider, issued_to, doc_ref, doc_type,
    result_doc_type)."""
    doc_ref = job.get("doc_ref")
    parent = db_documents.get_by_id(doc_ref) if doc_ref else None
    if parent is None:
        raise NodeNotStartable("the Flow has no root document for the AI to work from")
    project_id, flow_id = job.get("project_id") or "", job["flow_id"]

    def _issue(ai_run_id: Optional[str] = None) -> dict:
        issue = token_service.issue(
            project=project_id, group_id=flow_id, action_scope="new", doc_ref=doc_ref,
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
            api_base_url=API_BASE_URL, raw_token=issue["raw_token"],
        )
        return {"raw_token": issue["raw_token"], "token_id": issue["token_id"],
                "scratch_dir": issue["scratch_dir"], "mention": mention or ""}

    return ai_invoke_service.start_run(
        project_id=project_id, module=_module_of(flow_id), group_id=flow_id, doc_ref=doc_ref,
        action_scope="new", mode="single", continuation_target_seq=None, continuation_review_mode=False,
        continuation_instruction_mode=None, continuation_locale=None, issued_to=job["issued_to"],
        api_base_url=API_BASE_URL, mention_builder=lambda _raw, _scratch: None,
        provider_id=job.get("provider_id"), issue_builder=_issue,
        continuation_step_timeout_sec=job.get("step_timeout_sec"),
        node_restart_max_attempts=job.get("restart_max"),
    )
