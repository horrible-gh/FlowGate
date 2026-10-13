"""Writing a resolver decision (L0009 §2.6.6) and the run event log (L0009 §2.6.9).

Always inside a Flow guard: one ``apply_decision`` call is one part of the guard transaction, so a
node transition, its ``result_doc_id``, its event and its propagation record commit together or
not at all. Conditional updates that miss their row raise (rollback, L0009 E-35 / E-36).

flowgate.v02.0061 T#2: opening a node starts its attempt (``run_store.open_attempt``, before the dispatch so the
node's token is mapped to it) and its approval (``done``) ends it. After a decision, ``settle_run`` asks whether the
run goes on (``run_state_ports.settle``): the running / idle judgement, the idle reason and the candidates a command
may still open are v0.2 rules in ``flowgate.graph_run``; this module only writes what a step decides.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from modules.flow_gate.graph_workflow.domain import canvas_mapping as cm
from modules.flow_gate.graph_workflow.domain.frontier import Decision, NodeRun, PendingPropagation, RunState
from modules.flow_gate.graph_workflow.domain.graph_model import Graph, GraphEdge
from modules.flow_gate.graph_workflow.domain.supersede import live_end
from modules.flow_gate.graph_workflow.projection.graph_projector import to_graph_node
from modules.flow_gate.graph_workflow.projection.run_documents import flow_documents
from modules.flow_gate.graph_workflow.store import assignment_store, definition_store, run_store

from . import node_dispatch, run_events, run_state_ports
from modules.flow_gate.services import graph_execution_hooks
from .card_document_ports import result_bound

DISPATCH_FIELDS = ("token_issued", "mention_id", "issued_to", "error_code")


@dataclass
class RunContext:
    """One run as a guarded step sees it. ``run["last_event_seq"]`` is kept current by ``emit``."""
    run: dict
    project_id: Optional[str]
    revision: Graph
    node_rows: dict[str, dict]
    binding: Optional[dict]
    actor: Optional[str]
    jobs: list[Callable[[], None]] = field(default_factory=list)
    _docs: Optional[dict] = None

    @property
    def run_id(self) -> str:
        return self.run["run_id"]

    @property
    def flow_id(self) -> str:
        return self.run["group_id"]

    def docs(self) -> dict:
        if self._docs is None:
            self._docs = flow_documents(self.flow_id)
        return self._docs

    def root_document(self) -> Optional[dict]:
        """The bound root document: the live end of its supersede chain (L0009 E-18), or None."""
        if not self.binding:
            return None
        docs = self.docs()
        end, _hops = live_end(self.flow_id, docs.get(self.binding["doc_id"]), docs)
        return end


def load_context(run: dict, project_id: Optional[str], actor: Optional[str],
                 jobs: Optional[list] = None) -> RunContext:
    record = definition_store.revision(run["group_id"], int(run["revision_no"])) or {"nodes": [], "edges": []}
    binding = assignment_store.root_binding(run["group_id"])
    rows = {n["node_key"]: n for n in record["nodes"]}
    if binding and binding["node_key"] not in rows:
        binding = None                                      # L0009 E-31: not in this revision
    graph = Graph(nodes=tuple(to_graph_node(n) for n in record["nodes"]),
                  edges=tuple(GraphEdge(e["edge_id"], e["source"], e["target"], e.get("condition"))
                              for e in record["edges"]))
    return RunContext(dict(run), project_id, graph, rows, binding, actor,
                      jobs if jobs is not None else [])


def read_run_state(ctx: RunContext) -> RunState:
    nodes = {r["node_key"]: NodeRun(r["node_key"], r["status"], r.get("result_doc_id"))
             for r in run_store.node_runs(ctx.run_id)}
    pending = [PendingPropagation(p["node_key"], p["outcome"], int(p["source_event_seq"]))
               for p in run_store.pending_propagations(ctx.run_id)]
    step = ctx.run.get("step_node_keys")
    return RunState(ctx.run["status"], nodes, pending, has_active_attempts=bool(graph_execution_hooks.call("working", ctx.run_id)),
                    mode=ctx.run.get("run_mode") or "full",
                    step_targets=frozenset(step) if step is not None else None)


def canvas_state_of(ctx: RunContext, node_key: str, status: str) -> Optional[str]:
    """P0008 §2.3: a root-bound node shows its root document's state, any other its run status."""
    if ctx.binding and node_key == ctx.binding["node_key"] and _is_root_bound(ctx, node_key):
        doc = ctx.root_document()
        return cm.document_state(doc.get("doc_review_status"), doc.get("type_code")) if doc else None
    return cm.node_run_state(status)


def _is_root_bound(ctx: RunContext, node_key: str) -> bool:
    return any(r["node_key"] == node_key and int(r.get("root_bound") or 0) == 1
               for r in run_store.node_runs(ctx.run_id))


def emit_run_status(ctx: RunContext, from_status: Optional[str], to_status: str, cause: str,
                    reason: Optional[str] = None) -> int:
    seq = int(ctx.run["last_event_seq"]) + 1
    actor = ctx.actor if cause not in run_events.AUTO_CAUSES else None
    payload = run_events.run_status_payload(ctx.run, seq, from_status, to_status, cause, actor, reason)
    run_store.append_event(ctx.run_id, seq, run_events.RUN_STATUS, payload, node_key=None,
                           from_status=from_status, to_status=to_status, cause=cause, result_doc_id=None,
                           canvas_state=None, actor_user_id=actor, reason=reason)
    ctx.run["last_event_seq"] = seq
    ctx.run["status"] = to_status
    run_events.queue(ctx.project_id, ctx.flow_id, run_events.RUN_STATUS, payload)
    return seq


def emit_node_state(ctx: RunContext, node_key: str, from_status: str, to_status: str, cause: str,
                    result_doc_id: Optional[str], dispatch: Optional[dict] = None,
                    note: Optional[dict] = None) -> int:
    """``note`` (``actor_user_id``, ``reason``) is kept in the event log only; the SSE payload is P0008 §8.3."""
    seq = int(ctx.run["last_event_seq"]) + 1
    state = canvas_state_of(ctx, node_key, to_status)
    payload = run_events.node_state_payload(ctx.run, seq, node_key, from_status, to_status, state, cause,
                                            result_doc_id, dispatch)
    run_store.append_event(ctx.run_id, seq, run_events.NODE_STATE, payload, node_key=node_key,
                           from_status=from_status, to_status=to_status, cause=cause, result_doc_id=result_doc_id,
                           canvas_state=state, actor_user_id=(note or {}).get("actor_user_id"),
                           reason=(note or {}).get("reason"))
    ctx.run["last_event_seq"] = seq
    run_events.queue(ctx.project_id, ctx.flow_id, run_events.NODE_STATE, payload, doc_id=result_doc_id)
    return seq


def _assert_one(affected: int, what: str) -> None:
    if affected != 1:
        raise AssertionError(f"graph run write missed its row: {what}")


def apply_decision(ctx: RunContext, d: Decision, note: Optional[dict] = None, settle: bool = True) -> None:
    """L0009 §2.6.6 ``apply_decision`` — transitions, propagation records, close, open, then whether the run goes
    on (``settle_run``, 0061 T#2; ``settle=False`` for a step that applies its command afterwards).

    ``note`` (who decided, the comment) goes with the node transitions of the deciding event (gate).
    """
    for t in d.node_transitions:
        _assert_one(run_store.transition_node_run(
            ctx.run_id, t.node, t.from_status, t.expected_doc_id, t.to_status, t.result_doc_id,
            finished=t.to_status in ("done", "rejected")), f"transition {t.node} {t.cause}")
        if t.to_status == "done":
            run_store.end_attempt(ctx.run_id, t.node)                  # 0061 T#2: the node's attempt ends
        seq = emit_node_state(ctx, t.node, t.from_status, t.to_status, t.cause, t.result_doc_id, note=note)
        if t.result_doc_id and t.result_doc_id != t.expected_doc_id:
            result_bound(ctx.flow_id, t.node, t.result_doc_id)        # flowgate.v02.0053 T#1: fills the line
        for p in d.propagation_new:
            if p.transition is t:
                run_store.insert_propagation(ctx.run_id, seq, p.node, p.outcome, p.status)
    for p in d.propagation_done:
        _assert_one(run_store.mark_propagation_done(ctx.run_id, p.source_event_seq), f"propagation {p}")
    if d.close:
        # L0009 D-03: the status a closed node takes waits on Q-4 / Q-6. No decided policy can ask
        # for it yet; refusing (rollback) is safer than inventing a status.
        raise NotImplementedError("closed node status is undecided (L0009 D-03)")
    for key in graph_execution_hooks.call("opening", ctx, d.open):
        if not graph_execution_hooks.call("can_open", ctx):
            break
        run_store.open_attempt(ctx.run_id, key, int(ctx.run["revision_no"]))   # 0061 T#2: one active per node
        dispatch = node_dispatch.dispatch(ctx, key)
        _assert_one(run_store.open_node_run(ctx.run_id, key, dispatch), f"open {key}")
        emit_node_state(ctx, key, "pending", "open", "opened", None, dispatch)
    if settle:
        settle_run(ctx)


def settle_run(ctx: RunContext) -> None:
    """0061 T#2, inside the step's guard after its decision: whether the run goes on (``run_state_ports.settle``)."""
    run_state_ports.settle(ctx)


def fill_line(ctx: RunContext, node_key: str, doc_id: str) -> None:
    """A document of a node's card that is not its result — the instruction its card run wrote or found
    written (flowgate.v02.0056 T#2) — takes its card line, as a bound result does (no node event)."""
    result_bound(ctx.flow_id, node_key, doc_id)


def dispatch_block(row: dict) -> Optional[dict]:
    """P0008 §7.1 ``dispatch`` from a node run row (``None`` when never dispatched)."""
    if row.get("dispatch_token_issued") is None:
        return None
    out = {"token_issued": bool(row["dispatch_token_issued"]), "mention_id": row.get("dispatch_mention_id"),
           "issued_to": row.get("dispatch_issued_to")}
    if row.get("dispatch_error_code"):
        out["error_code"] = row["dispatch_error_code"]
    return out
