"""The card's review stage (flowgate.v02.0053 T#2): what follows a Graph node's AI result.

A Graph node's AI run writes the result and the result binder puts the node in review. The card then
decides what follows (``fields.after_result``), once that run has ended and released the Flow's group
AI lease (``ai_run_end_hooks``, registered by ``integration``):

- review count not 0: the card's review AI reviews the result with the v0.1 document review loop —
  the same engine the AI invoke dialog starts — with the card's review count and review basis, the
  rework going to the AI that runs the node (``fields.review_loop``). Every review hop, the first one
  included, is told that basis (``fields.review_basis_note``). The review AI is the card's, or
  the project's default reviewer when the card names none. A rejection the loop writes on the way to
  a rework is not the node's outcome (``review_running``, read by the result binder's reconciliation);
- when that review loop ends: passed and after review follow -> the result is approved; human, or
  any other end (hold, count spent, stalled, failure) -> a person decides, as before;
- no review and after review follow -> the result is approved at once; otherwise a person decides.

An approval is the v0.1 approval (``legacy_adapter.approve``) followed by the same Graph contact a
person's approval reaches, so the node moves on exactly as if a person had approved it. Only AI nodes
read their card (as at dispatch). The card is locked while its review runs (``settings``).
"""
from __future__ import annotations

import logging
from typing import Optional

from flowgate.card_settings import fields
from flowgate.card_settings import legacy_adapter as legacy
from flowgate.card_settings import repository

_log = logging.getLogger(__name__)

PASSED = "review_passed"


def on_run_ended(run: dict) -> None:
    """``ai_run_end_hooks.on_run_ended`` handler: a run of a Graph Flow ended and released its lease."""
    flow_id = run.get("group_id")
    if not flow_id or not repository.available() or not legacy.graph_owned(flow_id):
        return
    if run.get("document_review_loop"):
        _after_review(flow_id, run)
    elif run.get("action_scope") == "new" and run.get("mode") == "single":
        _after_work(flow_id, run)


def _after_work(flow_id: str, run: dict) -> None:
    """The node's AI run ended: start the card's review, approve, or leave it to a person."""
    found = legacy.node_of_ai_run(flow_id, run["run_id"])
    if found is None:
        return                                  # not a node's AI run, or no result bound
    node, doc_id = found
    stored = repository.values(flow_id, node["node_key"])
    step = fields.after_result(fields.card_kind(node.get("node_type"), node.get("doc_type")), stored)
    if step == fields.REVIEW:
        project_id = legacy.project_of(flow_id)
        reviewer = stored.get("review_ai") or legacy.default_reviewer(project_id)
        reworker = stored.get("run_ai") or node.get("assignee_provider_id") or run.get("provider_id")
        if not reviewer or not reworker:
            _log.warning("card review not started flow=%s node=%s: no reviewer or rework AI",
                         flow_id, node["node_key"])
            return
        loop = fields.review_loop(stored, reviewer=reviewer, reworker=reworker)
        criteria = loop["review_criteria"]
        legacy.start_review_loop(project_id, flow_id, doc_id, run.get("issued_to") or "", loop,
                                 lambda last: fields.review_basis_note(criteria, last))
    elif step == fields.APPROVE:
        legacy.approve(flow_id, doc_id, run.get("issued_to") or "")


def _after_review(flow_id: str, run: dict) -> None:
    """A review loop on a node's result ended: follow approves a passed result."""
    doc_id = run.get("doc_ref")
    node = legacy.node_of_result(flow_id, doc_id)
    if node is None:
        return
    loop = legacy.review_loop_row(run["run_id"]) or run.get("document_review_loop") or {}
    if loop.get("stop_reason") != PASSED:
        return                                  # hold, count spent, stalled, failure: a person decides
    if repository.values(flow_id, node["node_key"]).get("after_review") == "follow":
        legacy.approve(flow_id, doc_id, run.get("issued_to") or "")


def review_running(_flow_id: str, _node_key: str, doc_id: Optional[str]) -> bool:
    """``card_settings_ports.review_running`` handler: a review loop is still working on ``doc_id``."""
    return bool(doc_id) and legacy.review_loop_active(doc_id)
