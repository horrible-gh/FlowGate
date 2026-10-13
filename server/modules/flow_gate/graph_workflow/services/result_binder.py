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
- an approval of a node's result document -> ``review_approved`` — once every other document of the node's
  card is approved too (``card_approval_ports.pending_documents``, flowgate.v02.0056 T#1: a card is approved
  as a whole). A result approved while its card's instruction is not stays in review, awaiting the card's
  approval; the approval of that last document — through the card approval or a person's v0.1 approval,
  in either order — then brings the node in line (``_follow_card``, the reconciliation's own step).

flowgate.v02.0056 T#1 (0002-CH turn 5~10): a rejection of a node's result — a person's (the v0.1
``review_transitions/reject`` route, kept for v0.1) or the one the card's AI review writes on the way to
a rework or when its count is spent — is not a node event of the v0.2 contract: the node stays in review
and waits for the card's approval (``node_approval``). The one exception is a root bound in the rejected
state at the run start: it keeps L0009 §2.6.5 (``run_service._root_bound_start`` applies ``review_rejected``),
as answered on the T#1 TR's question. A gate's confirm / reject is its own event and does not change.

flowgate.v02.0056 T#2: an AI card with its instruction used runs an instruction stage before its result stage
(``node_dispatch``, the node run's ``stage``). Only the result stage's document is the node's result: while the
node is in its instruction stage nothing it registers is ``result_submitted`` — the card's instruction,
registered with the node's token, is recorded as the stage's instruction (``instruction_doc_id``) and fills
the card's instruction line, with no node event; the node stays open. (Once that stage's AI failed —
``ai_invoke_failed`` — the node's token is a person's again, L0009 E-23, and a result they register binds as
before.) The instruction's automatic approval is no node event either: it is no node's result. The stage's end —
the move to the result stage or its failure — records the node run's registration boundary
(``result_event_floor``): what the node's tokens registered up to it (a report the instruction AI registered too
early, say) is never the node's result afterwards, on a contact or a reconciliation; only a later registration is.
The boundary sees only the registrations consumed by then, and the inbox contacts the Graph before it consumes the
token, so a registration on its way when the stage ends is told by its token's stage (``graph_run_token_map.stage``):
the instruction AI's token is mapped as the instruction stage's when its run starts, and the node's dispatch token is
marked so when a contact with it arrives during the stage. What an instruction stage's token registered is never the
node's result — the contact and the reconciliation judge it alike, whenever its consume or commit lands.
A node run with no stage — an AI card run opened before migration 148 — keeps the one stage its AI was told then:
its result type is the card's of that time (``result_type`` hands the stage on as ``run_stage``).

Anything else binds nothing and stays an unbound document node. A lost delegation (exception in
the contact) is recovered by reconciliation from the v0.1 records (L0009 E-27): on resume, and
before a run read when ``needs_reconcile`` says something is missing.
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

from . import card_approval_ports, card_settings_ports
from .engine_router import read_ownership
from .flow_guard import with_flow_guard
from .run_apply import RunContext, apply_decision, fill_line, load_context, read_run_state, settle_run

_log = logging.getLogger(__name__)

EVENT_KINDS = ("inbox_registered", "review_decided")
INSTRUCTION_STAGE = "instruction"
RECONCILE_MAX_STEPS_PER_NODE = 64
# Only an approval moves a node on; a rejection is no node event (flowgate.v02.0056 T#1).
_DECISIONS = {"approve": "review_approved", "approved": "review_approved"}
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
            if kind == "review_decided" and _approval(payload):
                _follow_card(ctx)                                # another document of a card approved
            settle_run(ctx)                                      # 0061 T#2: a review ended with no node event
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
        if row is not None and in_instruction_stage(row):
            if not mapped.get("stage"):                           # T#2: the dispatch token spent in this stage
                run_store.mark_token_stage(payload["token_id"], INSTRUCTION_STAGE)
            _take_instruction(ctx, node, row, doc)                # T#2: the instruction, no node event
            return None
        if mapped.get("stage") == INSTRUCTION_STAGE:
            return None                                           # T#2: the instruction stage's token, never a result
        if row is None or _type(doc.get("type_code")) != result_type(ctx, node, row):
            return None                                           # E-24: another type
        if doc["doc_id"] in instruction_stage_documents(ctx, row):
            return None                                           # T#2: registered before the result stage
        if row.get("result_doc_id") is None and row["status"] == "open":
            return RunEvent("result_submitted", row["node_key"], doc_id=doc["doc_id"])
        return None                                               # already has a result
    if kind == "review_decided":
        row = run_store.node_run_by_result(ctx.run_id, doc["doc_id"])
        cause = _DECISIONS.get(_decision(payload))
        if row is None or cause is None:
            return None
        if not card_complete(ctx, row["node_key"], doc["doc_id"]):
            return None                                           # the card's other documents wait
        return RunEvent(cause, row["node_key"], doc_id=doc["doc_id"])
    return None


def in_instruction_stage(row: dict) -> bool:
    """The node run is in its card's instruction stage and its AI has not failed (flowgate.v02.0056 T#2)."""
    return row.get("stage") == INSTRUCTION_STAGE and row["status"] == "open" and not row.get("dispatch_error_code")


def instruction_stage_documents(ctx: RunContext, row: Optional[dict]) -> set[str]:
    """The documents the node's tokens registered before its result stage (at or before the node run's
    ``result_event_floor``): its instruction stage's, never its result (flowgate.v02.0056 T#2). A registration
    consumed after the boundary is the instruction stage's by its token's stage instead (``locate``,
    ``next_missing_event``)."""
    floor = (row or {}).get("result_event_floor")
    if floor is None:
        return set()
    tokens = run_store.token_ids(ctx.run_id, row["node_key"])
    return {d["doc_id"] for d in registered_documents(ctx.flow_id, tokens, upto_event_id=int(floor))}


def _take_instruction(ctx: RunContext, node: dict, row: dict, doc: dict) -> None:
    """Inside the guard: the card's instruction registered with the node's token during its instruction stage
    is the stage's instruction and takes the card's instruction line; anything else binds nothing."""
    found = card_settings_ports.instruction_document(ctx.flow_id, node)
    if not found.get("doc_type") or _type(doc.get("type_code")) != _type(found["doc_type"]):
        return
    if doc.get("status") == CANCELLED:
        return
    run_store.set_stage(ctx.run_id, row["node_key"], INSTRUCTION_STAGE, doc["doc_id"])
    fill_line(ctx, row["node_key"], doc["doc_id"])              # the card's instruction line


def _decision(payload: dict) -> str:
    return str(payload.get("action") or payload.get("decision") or "")


def _approval(payload: dict) -> bool:
    return _DECISIONS.get(_decision(payload)) == "review_approved"


def card_complete(ctx: RunContext, key: str, result_doc_id: str) -> bool:
    """No other document of the node's card waits for its approval (T0004 §4: the card is approved when all
    of its documents are)."""
    node = ctx.node_rows.get(key) or {"node_key": key}
    return not card_approval_ports.pending_documents(ctx.flow_id, node, result_doc_id)


def _follow_card(ctx: RunContext) -> None:
    """Inside the guard: a document that is no node's result was approved — the last one a card in review
    with an approved result waited for — so that node takes its missing ``review_approved``."""
    s = read_run_state(ctx)
    keys = [key for key in reconcile_targets(ctx, s)
            if (event := next_missing_event(ctx, s, key)) is not None and event.kind == "review_approved"]
    if not keys:
        return
    policies = policies_of(run_store.policy_entries(ctx.run_id))
    for key in keys:
        reconcile_node(ctx, key, policies)


def _type(value) -> str:
    return str(value or "").strip().upper()


def result_type(ctx: RunContext, node: dict, row: Optional[dict] = None) -> str:
    """The type a result of ``node`` is: its card's (instruction omitted: the report), else its own — a Custom
    card's being its work instruction (flowgate.v02.0053 T#8, ``graph_model.run_doc_type``). With its node run
    ``row`` the card run's stage goes along as ``run_stage`` (flowgate.v02.0056 T#2): a node run with no stage,
    opened before migration 148, keeps the type its AI was told then."""
    asked = {**node, "run_stage": row.get("stage")} if row is not None else node
    return _type(card_settings_ports.result_type(ctx.flow_id, asked)
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
        row = next((r for r in run_store.node_runs(ctx.run_id) if r["node_key"] == key), None)
        if row is not None and in_instruction_stage(row):
            return None                                           # T#2: no result before the result stage
        want = result_type(ctx, ctx.node_rows.get(key) or {}, row)
        early = instruction_stage_documents(ctx, row)             # T#2: the instruction stage's registrations
        tokens = run_store.token_ids(ctx.run_id, key, skip_stage=INSTRUCTION_STAGE)   # T#2: and its tokens'
        for doc in registered_documents(ctx.flow_id, tokens):
            if doc["doc_id"] in early:
                continue
            if _type(doc.get("type_code")) == want and doc.get("status") != CANCELLED:
                return RunEvent("result_submitted", key, doc_id=doc["doc_id"])
        return None
    cur = ctx.docs().get(nr.result_doc_id) or document(nr.result_doc_id)
    if cur is None:
        return None                                               # E-10: no document row
    if cur.get("superseded_by"):
        # A live replacement comes first, as on the contact (``locate`` 1): a result approved while its card
        # waited for its other documents and then revised is followed, not held at its old approval.
        nxt = ctx.docs().get(cur["superseded_by"]) or document(cur["superseded_by"])
        if nxt is not None and nxt.get("status") != CANCELLED and nxt.get("group_id") == ctx.flow_id:
            return RunEvent("revised", key, doc_id=nxt["doc_id"], replaces=cur["doc_id"])
    if nr.status == "review" and cur.get("doc_review_status") in ("approved", "wf_done"):
        if not card_complete(ctx, key, cur["doc_id"]):
            return None                                           # approved result, card not yet
        return RunEvent("review_approved", key, doc_id=cur["doc_id"])
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
