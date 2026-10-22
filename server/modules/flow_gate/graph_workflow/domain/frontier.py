"""Frontier Resolver (D0004 §2.2 실행 판단기, L0009 §2.6.4) — a pure function of
(revision, run state, event, policies) -> decision. It knows no DB, HTTP or token.

Node events go through the L0009 §3.2 transition table and the document-argument check
(``transition_doc``); anything invalid is ``ignored`` (idempotent, L0009 E-20 / E-36). A result
event (approval / rejection) is propagated at once while the run is running, or recorded as a
``pending`` propagation while it is stopped. Control events (``start``, ``resume``) never touch
the node table: they open start nodes and drain pending propagations in ``source_event_seq``
order. Every rule that D0004 §8 has not answered comes from a policy slot; a slot the revision's
structure does not need (``run_policies.required_policies``) is not asked — a single plain
out-edge is simply followed and a node with one in-edge simply opens.

flowgate.v02.0061 T#2: a propagation waits (``pending``) whenever the run is not ``running`` (paused, idle), and in
a step command also when it would open a node outside the step's targets (``RunState.in_command``).
0083 WP-3 (0061 T#2 개정, revised): an idle run is no longer one wait — a person's decision on an idle run that may be
taken back to running (``RunState.approval_wakes``: not stopped, no Time Machine operation unfinished, not inside an
explicit command, the run holding its Flow; ``wake_graph_run``'s conditions) propagates at once and opens the next
cards, as on a running run (``defers``); the caller takes the run back to running before the openings
(``graph_execution_hooks`` ``wake_approval``). A paused run, a stopped one and one under a Time Machine operation still
wait, and a step still opens only its targets. A command
(``start``, ``command`` = a new step / full command, ``resume`` = the paused command going on) drains what waits
and then opens the run candidates (``run_candidates``: run scope targets that are pending with every predecessor
done), so a node that became runnable without a propagation (a node added after a done node) opens as well.

flowgate.v02.0061 T#11 Hold: the caller passes the Flow's held node keys (``RunState.held``, the cards whose ``hold``
setting is on). ``openable_only`` — the one step every opening goes through (the start node, a propagation's opening,
a rejection path, the run candidates) — never opens a held node: it stays ``pending``, so its successors and the joins
they enter wait as the propagation structure already makes them wait, and branches without it go on. Hold only bounds
the opening: a held node already open / in review goes on. Once released, the next command's run candidates open it.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Optional

from .graph_model import ROOT_DOC_TYPES, Graph, GraphEdge
from .run_policies import required_policies
from .run_scope_ports import run_scope

NODE_EVENTS = ("result_submitted", "revised", "review_approved", "review_rejected", "gate_confirmed", "gate_rejected")
CONTROL_EVENTS = ("start", "resume", "command")
MODE_STEP, MODE_FULL = "step", "full"
RUN_MODES = (MODE_STEP, MODE_FULL)

# L0009 §3.2 — node events only (``opened`` is applied by the decision, ``root_bound_start`` by
# the run start). Any other (status, cause) pair is invalid.
_TRANSITIONS = {
    ("pending", "opened"): "open",
    ("open", "result_submitted"): "review",
    ("review", "review_approved"): "done",
    ("review", "review_rejected"): "rejected",
    ("rejected", "revised"): "review",
    ("review", "revised"): "review",
    ("open", "gate_confirmed"): "done",
    ("open", "gate_rejected"): "rejected",
}
_OUTCOME = {"review_approved": "approved", "gate_confirmed": "approved",
            "review_rejected": "rejected", "gate_rejected": "rejected"}


class ProgrammingError(Exception):
    """An event or outcome the resolver has no branch for (L0009 §2.6.4 ``else`` rows)."""


@dataclass(frozen=True)
class NodeRun:
    node_key: str
    status: str
    result_doc_id: Optional[str] = None


@dataclass(frozen=True)
class PendingPropagation:
    node_key: str
    outcome: str                     # "approved" | "rejected"
    source_event_seq: int


@dataclass
class RunState:
    status: str                                              # run.status
    node_runs: dict[str, NodeRun] = field(default_factory=dict)
    pending: list[PendingPropagation] = field(default_factory=list)
    open_decided: frozenset = frozenset()
    has_active_attempts: bool = False  # compatibility name: requested/running AI executions (0061 T#4)
    mode: str = MODE_FULL              # the command the run goes on with (0061 T#2)
    step_targets: Optional[frozenset] = None   # a step's targets; None: a full command or a step not begun yet
    held: frozenset = frozenset()      # 0061 T#11: never opened — the held node keys (and a bound node waiting for its root)
    approval_wakes: bool = False       # 0083 WP-3 (0061 T#2 개정): an idle run a person's decision takes back to running

    def node(self, key: str) -> Optional[NodeRun]:
        return self.node_runs.get(key)

    def in_command(self, key: str) -> bool:
        """0061 T#2: may the current command open ``key``? A step opens only its targets."""
        return self.mode != MODE_STEP or self.step_targets is None or key in self.step_targets

    def open_nodes(self) -> list[str]:
        return sorted(k for k, n in self.node_runs.items() if n.status == "open")


@dataclass(frozen=True)
class RunEvent:
    kind: str
    node: Optional[str] = None
    doc_id: Optional[str] = None
    replaces: Optional[str] = None


@dataclass(frozen=True, eq=False)
class Transition:
    node: str
    from_status: str
    to_status: str
    cause: str
    expected_doc_id: Optional[str]   # the conditional-update baseline (L0009 §2.6.6)
    result_doc_id: Optional[str]


@dataclass(frozen=True)
class NewPropagation:
    node: str
    outcome: str
    status: str                      # "pending" | "done"
    transition: Transition           # the node transition whose event becomes source_event_seq


@dataclass
class Decision:
    node_transitions: list[Transition] = field(default_factory=list)
    propagation_new: list[NewPropagation] = field(default_factory=list)
    propagation_done: list[PendingPropagation] = field(default_factory=list)
    open: list[str] = field(default_factory=list)
    close: list[str] = field(default_factory=list)
    run_transition: Optional[str] = None
    ignored: bool = False

    def add(self, other: "Decision") -> "Decision":
        self.node_transitions += other.node_transitions
        self.propagation_new += other.propagation_new
        self.propagation_done += other.propagation_done
        self.open += other.open
        self.close += other.close
        if other.run_transition is not None:
            self.run_transition = other.run_transition
        return self


def local_transition(status: str, cause: str) -> Optional[str]:
    return _TRANSITIONS.get((status, cause))


def virtual(s: RunState, d: Decision) -> RunState:
    """``s ⊕ d``: the state with the decision applied (pure; nothing is written)."""
    nodes = dict(s.node_runs)
    for t in d.node_transitions:
        nodes[t.node] = NodeRun(t.node, t.to_status, t.result_doc_id)
    for key in d.open:
        if key in nodes:
            nodes[key] = replace(nodes[key], status="open")
    for key in d.close:
        if key in nodes:
            nodes[key] = replace(nodes[key], status="closed")
    done = set(d.propagation_done)
    status = "idle" if d.run_transition == "completed" else s.status
    return RunState(status, nodes, [p for p in s.pending if p not in done],
                    s.open_decided | frozenset(d.open), s.has_active_attempts, s.mode, s.step_targets, s.held,
                    s.approval_wakes)


def transition_doc(s: RunState, nr: NodeRun, event: RunEvent) -> Any:
    """L0009 §2.6.4: the node's result_doc_id after the event, or ``"invalid"``."""
    taken = {x.result_doc_id for x in s.node_runs.values() if x.node_key != nr.node_key and x.result_doc_id}
    if event.kind == "result_submitted":
        if nr.result_doc_id is not None or event.doc_id is None or event.doc_id in taken:
            return "invalid"
        return event.doc_id
    if event.kind == "revised":
        if event.replaces is None or event.replaces != nr.result_doc_id:
            return "invalid"
        if event.doc_id is None or event.doc_id == nr.result_doc_id or event.doc_id in taken:
            return "invalid"
        return event.doc_id
    if event.kind in ("review_approved", "review_rejected"):
        if event.doc_id is None or event.doc_id != nr.result_doc_id:
            return "invalid"
        return nr.result_doc_id
    if event.kind in ("gate_confirmed", "gate_rejected"):
        return nr.result_doc_id
    return "invalid"


def resolve(revision: Graph, state: RunState, event: RunEvent, policies: Mapping[str, Any]) -> Decision:
    if event.kind in CONTROL_EVENTS:
        return resolve_control(revision, state, event, policies)
    return resolve_node_event(revision, state, event, policies, defer=defers(state))


def defers(s: RunState) -> bool:
    """0061 T#2: a result's propagation waits unless the run is running (paused, idle).

    0083 WP-3 (0061 T#2 개정, revised): nor on an idle run a person's decision takes back to running
    (``RunState.approval_wakes``) — the flowgate.v02.0056 T#1 contract that a person's approval moves the node on, with
    no further resume. A paused run, a stopped run (idle reason ``stopped``) and a run under a Time Machine operation
    still wait for the next command; a step still opens only its targets (``RunState.in_command``)."""
    if s.status == "running":
        return False
    return not (s.status == "idle" and s.approval_wakes)


def resolve_node_event(revision: Graph, s: RunState, event: RunEvent, policies: Mapping[str, Any],
                       defer: bool) -> Decision:
    d = Decision()
    nr = s.node(event.node or "")
    to = local_transition(nr.status, event.kind) if nr is not None and event.kind in NODE_EVENTS else None
    if nr is None or to is None:
        d.ignored = True
        return d
    doc = transition_doc(s, nr, event)
    if doc == "invalid":
        d.ignored = True
        return d
    t = Transition(event.node, nr.status, to, event.kind, nr.result_doc_id, doc)
    d.node_transitions.append(t)
    outcome = _OUTCOME.get(event.kind)
    if outcome is None:
        return d                                                  # result_submitted / revised
    if defer:
        d.propagation_new.append(NewPropagation(event.node, outcome, "pending", t))
        return d
    follow = propagate(revision, virtual(s, d), event.node, outcome, policies)
    if not all(s.in_command(key) for key in follow.open):         # a step: waits for the next command
        d.propagation_new.append(NewPropagation(event.node, outcome, "pending", t))
        return d
    d.add(follow)
    d.propagation_new.append(NewPropagation(event.node, outcome, "done", t))
    d.add(completion_check(revision, virtual(s, d), policies))
    return d


def resolve_control(revision: Graph, s: RunState, event: RunEvent, policies: Mapping[str, Any]) -> Decision:
    """``start`` / ``command`` begin a command (the caller passes no step targets: a step's targets are what it
    opens), ``resume`` goes on with the paused one. Each drains what waits, then opens the run candidates."""
    d = Decision()
    if event.kind == "start":
        d.open += openable_only(s, policies["start_node"].select(revision, s))
    elif event.kind not in ("resume", "command"):
        raise ProgrammingError(f"unknown control event {event.kind}")
    d.add(drain_pending(revision, virtual(s, d), policies))
    d.open += command_candidates(revision, virtual(s, d))
    d.add(completion_check(revision, virtual(s, d), policies))
    return d


def drain_pending(revision: Graph, s: RunState, policies: Mapping[str, Any]) -> Decision:
    """Pending propagations in ``source_event_seq`` order; in a step, one that would open a node outside the step's
    targets keeps waiting (0061 T#2)."""
    d = Decision()
    for p in sorted(s.pending, key=lambda p: (p.source_event_seq, p.node_key)):
        follow = propagate(revision, virtual(s, d), p.node_key, p.outcome, policies)
        if not all(s.in_command(key) for key in follow.open):
            continue
        d.add(follow)
        d.propagation_done.append(p)
    return d


def run_candidates(revision: Graph, s: RunState) -> list[str]:
    """0061 T#2: the run scope targets that are ``pending`` with every live predecessor ``done``, in revision order
    (``openable_only``; T#11: not held). Pure; every command calls it after ``drain_pending`` (the revision move of
    T#3 as well)."""
    targets = run_scope(revision).targets
    preds: dict[str, list[str]] = {}
    for e in revision.live_edges():
        preds.setdefault(e.target, []).append(e.source)
    ready = [key for key in revision.node_keys() if key in targets
             and all((s.node(p) or NodeRun(p, "pending")).status == "done" for p in preds.get(key, ()))]
    return openable_only(s, ready)


def command_candidates(revision: Graph, s: RunState) -> list[str]:
    """The run candidates the current command may open (a step: its targets only)."""
    return [key for key in run_candidates(revision, s) if s.in_command(key)]


def out_edges(revision: Graph, node: str) -> list[GraphEdge]:
    return [e for e in revision.live_edges() if e.source == node]


def in_edge_states(revision: Graph, s: RunState, target: str) -> list[dict]:
    """Per incoming edge: ``completed`` (source done), ``not_run`` (source not finished), else ``inactive``."""
    out = []
    for e in revision.live_edges():
        if e.target != target:
            continue
        status = (s.node(e.source) or NodeRun(e.source, "pending")).status
        state = "completed" if status == "done" else ("not_run" if status in ("pending", "open", "review") else "inactive")
        out.append({"edge_id": e.edge_id, "source": e.source, "state": state})
    return out


def propagate(revision: Graph, s: RunState, node: str, outcome: str, policies: Mapping[str, Any]) -> Decision:
    d = Decision()
    needed = required_policies(revision)
    if outcome == "approved":
        edges = out_edges(revision, node)
        if "edge_condition" in needed:
            edges = list(policies["edge_condition"].select(node, result_of(s, node), edges))
        candidates = []
        for target in sorted({e.target for e in edges}):
            if "join" in needed and len(in_edge_states(revision, s, target)) >= 2:
                if policies["join"].openable(target, in_edge_states(revision, s, target)) != "open":
                    continue
            candidates.append(target)
        if "parallelism" in needed:
            candidates = list(policies["parallelism"].admit(candidates, s.open_nodes()))
        d.open += openable_only(s, candidates)
    elif outcome == "rejected":
        rejected = revision.node(node)
        if rejected is None or not (rejected.node_type == "gate" or
                                    (rejected.node_type == "document" and rejected.doc_type in ROOT_DOC_TYPES)):
            return d  # ordinary card rejection never routes to another node
        to_open, to_close = policies["rejection_path"].route(node, revision, s)
        d.open += openable_only(s, list(to_open))
        d.close += list(to_close)
    else:
        raise ProgrammingError(f"unknown outcome {outcome}")
    return d


def openable_only(s: RunState, nodes) -> list[str]:
    """Only ``pending`` nodes not already chosen are opened (L0009 §2.6.4); order kept, no repeats. 0061 T#11: a held
    node (``RunState.held``) is never opened."""
    out: list[str] = []
    for key in nodes:
        nr = s.node(key)
        if nr is not None and nr.status == "pending" and key not in s.open_decided and key not in out \
                and key not in s.held:
            out.append(key)
    return out


def result_of(s: RunState, node: str) -> dict:
    nr = s.node(node)
    return {"status": nr.status if nr else None, "result_doc_id": nr.result_doc_id if nr else None}


def completion_check(revision: Graph, s: RunState, policies: Mapping[str, Any]) -> Decision:
    if policies["completion"].is_complete(revision, s):
        return Decision(run_transition="completed")
    return Decision()
