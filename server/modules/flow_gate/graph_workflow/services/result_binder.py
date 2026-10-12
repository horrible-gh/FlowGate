"""Result Binder (D0004 §2.2 결과 연결기, L0009 §2.6.8, P0008 §7.2).

The v0.1 contacts hand every document event of a Graph-owned Flow here after v0.1 committed
(inbox registration, review decision). Inside the Flow guard the event is located on a node run
of the active run and turned into a node event for the resolver:

- a document that ends the supersede chain of a node's result -> ``revised`` (one hop) or a
  reconciliation of that node (a gap in the chain);
- a document registered with a node's token, of the node's result type, node open without result ->
  ``result_submitted``; a token-less document is not bound (Q-12, L0009 E-25). The result type is the
  node's own type, or the card's report when the card omits its instruction (flowgate.v02.0053 T#2,
  ``card_settings_ports.result_type``: the same port the dispatch asked, so the AI is told to write
  exactly the type that is bound);
- a review decision on a node's result document -> ``review_approved`` / ``review_rejected``.

Anything else binds nothing and stays an unbound document node. A lost delegation (exception in
the contact) is recovered by reconciliation from the v0.1 records (L0009 E-27): on resume, and
before a run read when ``needs_reconcile`` says something is missing. While the card's AI review is
still working on a result (``card_settings_ports.review_running``), the rejection it wrote on the way to
a rework is not reconciled: the review ends in a pass, a decision or a stop, and that is what counts.
"""
from __future__ import annotations

import logging
from typing import Optional

from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.graph_workflow.domain.frontier import RunEvent, RunState, resolve
from modules.flow_gate.graph_workflow.domain.graph_model import run_doc_type
from modules.flow_gate.graph_workflow.domain.run_policies import policies_of
from modules.flow_gate.graph_workflow.domain.supersede import live_end
from modules.flow_gate.graph_workflow.projection.run_documents import document, registered_documents
from modules.flow_gate.graph_workflow.store import run_store

from . import card_settings_ports
from .engine_router import read_ownership
from .flow_guard import with_flow_guard
from .run_apply import RunContext, apply_decision, load_context, read_run_state

_log = logging.getLogger(__name__)

EVENT_KINDS = ("inbox_registered", "review_decided")
RECONCILE_MAX_STEPS_PER_NODE = 64
_DECISIONS = {"approve": "review_approved", "approved": "review_approved",
              "reject": "review_rejected", "rejected": "review_rejected"}
CANCELLED = "cancelled"


def _project_id(flow_id: str) -> Optional[str]:
    return (db_groups.get_by_id(flow_id) or {}).get("project_id")


def _run_jobs(jobs: list) -> None:
    for job in jobs:
        try:
            job()
        except Exception:  # noqa: BLE001
            _log.exception("graph result post-commit job failed")


def on_document_event(kind: str, flow_id: str, payload: dict) -> None:
    if kind not in EVENT_KINDS or run_store.active_run(flow_id) is None:
        return                                                    # no run: an unbound document node
    jobs: list = []

    def body() -> None:
        if read_ownership(flow_id).state != "graph":              # re-judged inside the guard
            return
        run = run_store.active_run(flow_id)
        if run is None:
            return
        ctx = load_context(run, _project_id(flow_id), None, jobs)
        found = locate(ctx, kind, payload)
        if found is None:
            return
        policies = policies_of(run_store.policy_entries(ctx.run_id))      # E-33 -> rollback
        if found.kind == "revised" and _next_in_chain(ctx, found.replaces) != found.doc_id:
            reconcile_node(ctx, found.node, policies)            # chain gap: one hop at a time
            return
        d = resolve(ctx.revision, read_run_state(ctx), found, policies)
        if not d.ignored:
            apply_decision(ctx, d)

    with_flow_guard(flow_id, body)
    _run_jobs(jobs)


def locate(ctx: RunContext, kind: str, payload: dict) -> Optional[RunEvent]:
    """L0009 §2.6.8 ``locate_node_run`` / §4.5 — the node event, or None (bind nothing)."""
    doc = document(payload.get("doc_id"))
    if doc is None:
        return None
    rows = run_store.node_runs(ctx.run_id)
    if kind == "inbox_registered":
        docs = ctx.docs()
        for row in rows:                                          # 1) revision of a node's result
            current = row.get("result_doc_id")
            if not current or current == doc["doc_id"]:
                continue
            end, _hops = live_end(ctx.flow_id, docs.get(current), docs)
            if end is not None and end["doc_id"] == doc["doc_id"]:
                return RunEvent("revised", row["node_key"], doc_id=doc["doc_id"], replaces=current)
        mapped = run_store.node_run_by_token(payload["token_id"]) if payload.get("token_id") else None
        if not mapped or mapped["run_id"] != ctx.run_id:          # 3) token-less: Q-12, not bound
            return None
        node = ctx.node_rows.get(mapped["node_key"]) or {}        # 2) the node's token
        row = next((r for r in rows if r["node_key"] == mapped["node_key"]), None)
        if _type(doc.get("type_code")) != result_type(ctx, node) or row is None:
            return None                                           # E-24: another type
        if row.get("result_doc_id") is None and row["status"] == "open":
            return RunEvent("result_submitted", row["node_key"], doc_id=doc["doc_id"])
        return None                                               # already has a result
    if kind == "review_decided":
        row = run_store.node_run_by_result(ctx.run_id, doc["doc_id"])
        cause = _DECISIONS.get(str(payload.get("action") or payload.get("decision") or ""))
        if row is None or cause is None:
            return None
        return RunEvent(cause, row["node_key"], doc_id=doc["doc_id"])
    return None


def _type(value) -> str:
    return str(value or "").strip().upper()


def result_type(ctx: RunContext, node: dict) -> str:
    """The type a result of ``node`` is: its card's (instruction omitted: the report), else its own — a Custom
    card's being its work instruction (flowgate.v02.0053 T#8, ``graph_model.run_doc_type``)."""
    return _type(card_settings_ports.result_type(ctx.flow_id, node)
                 or run_doc_type(node.get("node_type"), node.get("doc_type")))


def _next_in_chain(ctx: RunContext, doc_id: Optional[str]) -> Optional[str]:
    cur = ctx.docs().get(doc_id or "") or document(doc_id)
    return (cur or {}).get("superseded_by")


# ── Reconciliation (L0009 §2.6.8, E-27) ──────────────────────────────────────

def reconcile_targets(ctx: RunContext, s: RunState) -> list[str]:
    out = []
    for key in sorted(s.node_runs):
        nr = s.node_runs[key]
        if (nr.status == "open" and nr.result_doc_id is None and run_store.token_ids(ctx.run_id, key)) or \
                (nr.result_doc_id is not None and nr.status in ("review", "rejected")):
            out.append(key)
    return out


def next_missing_event(ctx: RunContext, s: RunState, key: str) -> Optional[RunEvent]:
    """The next node event that brings the node in line with the v0.1 records, or None."""
    nr = s.node(key)
    if nr is None:
        return None
    if nr.result_doc_id is None:
        want = result_type(ctx, ctx.node_rows.get(key) or {})
        for doc in registered_documents(ctx.flow_id, run_store.token_ids(ctx.run_id, key)):
            if _type(doc.get("type_code")) == want and doc.get("status") != CANCELLED:
                return RunEvent("result_submitted", key, doc_id=doc["doc_id"])
        return None
    cur = ctx.docs().get(nr.result_doc_id) or document(nr.result_doc_id)
    if cur is None:
        return None                                               # E-10: no document row
    if nr.status == "review":
        if cur.get("doc_review_status") in ("approved", "wf_done"):
            return RunEvent("review_approved", key, doc_id=cur["doc_id"])
        if cur.get("doc_review_status") == "rejected" and not card_settings_ports.review_running(
                ctx.flow_id, key, cur["doc_id"]):
            return RunEvent("review_rejected", key, doc_id=cur["doc_id"])
    if cur.get("superseded_by"):
        nxt = ctx.docs().get(cur["superseded_by"]) or document(cur["superseded_by"])
        if nxt is not None and nxt.get("status") != CANCELLED and nxt.get("group_id") == ctx.flow_id:
            return RunEvent("revised", key, doc_id=nxt["doc_id"], replaces=cur["doc_id"])
    return None


def needs_reconcile(ctx: RunContext) -> bool:
    s = read_run_state(ctx)
    return any(next_missing_event(ctx, s, key) is not None for key in reconcile_targets(ctx, s))


def reconcile_node(ctx: RunContext, key: str, policies: dict) -> None:
    """Inside a guard only: apply missing events one at a time, re-reading the state each time."""
    for _ in range(RECONCILE_MAX_STEPS_PER_NODE):
        s = read_run_state(ctx)
        event = next_missing_event(ctx, s, key)
        if event is None:
            return
        d = resolve(ctx.revision, s, event, policies)
        if d.ignored:
            _log.warning("graph reconcile stopped run=%s node=%s event=%s", ctx.run_id, key, event.kind)
            return
        apply_decision(ctx, d)
    _log.warning("graph reconcile_step_limit run=%s node=%s", ctx.run_id, key)


def reconcile_links(ctx: RunContext, policies: dict) -> None:
    for key in reconcile_targets(ctx, read_run_state(ctx)):
        reconcile_node(ctx, key, policies)


def reconcile_if_needed(flow_id: str, project_id: Optional[str]) -> None:
    """Before a run read (L0009 §2.6.8 호출 시점). A failure only skips the repair; the read goes on."""
    run = run_store.active_run(flow_id)
    if run is None or not needs_reconcile(load_context(run, project_id, None)):
        return
    jobs: list = []

    def body() -> None:
        if read_ownership(flow_id).state != "graph":
            return
        again = run_store.active_run(flow_id)
        if again is None:
            return
        ctx = load_context(again, project_id, None, jobs)
        reconcile_links(ctx, policies_of(run_store.policy_entries(ctx.run_id)))

    try:
        with_flow_guard(flow_id, body)
    except Exception:  # noqa: BLE001 — L0009 §2.6.8: roll the repair back, answer the read
        _log.exception("graph run reconcile failed flow=%s", flow_id)
        return
    _run_jobs(jobs)
