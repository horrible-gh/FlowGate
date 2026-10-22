"""Run Coordinator (D0004 §2.2 실행 서비스, L0009 §2.6.5 / §2.6.7, P0008 §7 S5).

Start, stop and resume each run in one Flow guard; the checks come first and write nothing (the
order of L0009 §4.6 / §4.7 fixes which error wins). Policies come from the run's snapshot only.
Until D0004 §8 is answered every policy is ``undecided``, so a start is refused with
``run_policy_undecided`` naming the policies this revision needs (P0008 S5-1).

flowgate.v02.0056 T#1: a run read also tells the run band how many approved cards' propagations wait
for the run to go on (``pending_propagation_count`` / ``pending_node_keys``: "멈춤 · 다음 카드 N개 대기")
and each node run whether its card waits for a person's approval (``awaiting_approval``,
``node_approval``). 0056 T#1 개정: a bound root is existing input accepted by Run, including a rejected
root. Its card becomes done and propagates approved without changing the document's review status.

flowgate.v02.0056 T#2: each node run also tells the stage of its card run (``stage``: ``instruction`` while the
card's instruction AI writes its instruction, ``result`` while its run AI writes the result) and the instruction
the card run uses (``instruction_doc_id``).

flowgate.v02.0061 T#2: a Flow keeps one run — ``running``, ``paused`` or ``idle`` (``idle_reason``) — and a finished
run stays idle (completed) and goes on with the next command. A command is ``step`` (open the run candidates of the
moment; what they make runnable waits for the next command) or ``full`` (go on opening until no candidate is left):
``POST /flows/{flow_id}/runs`` and ``POST /runs/{run_id}/resume`` take it as ``mode``. ``POST /runs/{run_id}/pause``
is Pause (running -> paused, open nodes keep their tokens and their work, the Flow stays locked; T#2's behaviour, moved
from ``/stop`` by T#7). ``POST /runs/{run_id}/stop`` is T#7's Stop (``run_state_ports.stop``: stopping, then idle with
the reason ``stopped`` once the AI processes ended). A run read tells the command (``mode``), the idle reason and each
node's attempt.
Which command a run goes on with, what it records and whether a run goes on after a step are ``flowgate.graph_run``'s
(``run_state_ports``); this module keeps the order of the steps. The run that goes on must hold its Flow (one run per
Flow): a run written before migration 151 that completed gave the Flow up and takes it back only when no other run
holds or follows it.

flowgate.v02.0061 T#11: each node run tells whether its card is held (``hold``, ``graph_execution_hooks`` ``held_nodes``).
A held root-bound input is not marked done at the run start either (``_take_root_binding``): it
stays ``pending``, no propagation is recorded and nothing follows it. Released, nothing happens until the next step /
full command, which marks the root input done and records its approved propagation (0056 T#1 개정),
then goes on as the start would have — never an ordinary opening of the R / B card
(``run_apply.read_run_state`` keeps such a node out of every opening).

flowgate.v02.0064 T#4: each node run also tells whether its card is running now (``node_running``): the running lock the
card settings and a card title save use (``card_settings_ports.node_running`` — open in the active run, or in review while
the card's AI review works on its result). The Canvas closes a card's title edit by it instead of judging it again.
"""
from __future__ import annotations

import logging
from typing import Optional

from modules.flow_gate.graph_workflow.domain.frontier import MODE_FULL, RunEvent, resolve
from modules.flow_gate.graph_workflow.domain.run_policies import (
    PolicyUndecided,
    RULE_REF,
    policies_of,
    required_policies,
    undecided_for,
)
from modules.flow_gate.graph_workflow.store import definition_store, run_store
from modules.flow_gate.graph_workflow.domain.run_scope_ports import run_scope

from . import card_settings_ports, node_approval, node_dispatch, result_binder, run_state_ports
from .engine_router import Ownership, read_ownership
from .errors import GraphApiError
from .flow_guard import with_flow_guard
from .graph_service import new_key, require_writable
from .policy_snapshot import check_active_runs, policy_set_for_run
from .run_apply import RunContext, apply_decision, dispatch_block, emit_node_state, emit_run_status, load_context
from .run_apply import read_run_state, settle_run

_log = logging.getLogger(__name__)
RUN_ID_PREFIX = "run_"
_restore_checked = False


def _restore_check_once() -> None:
    """L0009 §2.6.2: try every active run's snapshot once per process (log only)."""
    global _restore_checked
    if _restore_checked:
        return
    _restore_checked = True
    try:
        check_active_runs()
    except Exception:  # noqa: BLE001
        _log.exception("graph run policy restore check failed")


def run_jobs(jobs: list) -> None:
    """Post-commit jobs (L0009 §2.1.4): after the guard released, best effort."""
    for job in jobs:
        try:
            job()
        except Exception:  # noqa: BLE001
            _log.exception("graph run post-commit job failed")


def _ownership_error(flow_id: str, own: Ownership) -> GraphApiError:
    if own.state == "unassigned":
        return GraphApiError(409, "flow_unassigned", {"flow_id": flow_id})
    if own.state == "v01":
        return GraphApiError(409, "flow_owned_by_v01", {
            "flow_id": flow_id, "ownership": "v01", "sequence_root_doc_id": own.sequence_root_doc_id})
    return GraphApiError(409, "flow_ownership_conflict", {
        "flow_id": flow_id, "ownership": own.state, "sequence_root_doc_id": own.sequence_root_doc_id,
        "graph_revision_no": definition_store.latest_revision_no(flow_id)})


def _undecided(flow_id: str, revision_no: int, missing: list[dict]) -> GraphApiError:
    return GraphApiError(409, "run_policy_undecided",
                         {"flow_id": flow_id, "revision_no": revision_no, "undecided_policies": missing})


def _missing(ctx: RunContext, entries: dict, extra: frozenset = frozenset()) -> list[dict]:
    """L0009 §2.6.3 ``undecided_for``; ``extra`` adds what one step needs on top (gate: ``gate_node``)."""
    required = required_policies(ctx.revision, (ctx.binding or {}).get("node_key"))
    return undecided_for(required | set(extra), entries)


def start_run(group: dict, user_id: str, revision_no: Optional[int], mode: Optional[str] = None) -> dict:
    """``POST /flows/{flow_id}/runs`` -> 201 ``{"run": …}`` (P0008 S5-1~S5-3, L0009 §2.6.5).

    0061 T#2: the step / full command (``mode``; a new run: ``full`` when not given). The Flow's paused or idle run
    goes on with the command instead of a second run (as ``resume`` with a mode; with no mode, its own command). A
    running run, or a requested revision other than the run's or latest, stays 409 ``run_already_active``.
    T#3: the guarded command adopts the latest confirmed revision only when no attempt is active."""
    flow_id, project_id = group["group_id"], group.get("project_id")
    require_writable(flow_id)
    _restore_check_once()
    jobs: list = []

    def body() -> dict:
        own = read_ownership(flow_id)                                             # 1~3 담당
        if own.state != "graph":
            raise _ownership_error(flow_id, own)
        _refuse_time_machine(flow_id)                                             # 0061 T#13
        active = run_store.current_run(flow_id)                                    # 4 진행 중 실행
        if active is not None:
            if active["status"] not in (run_store.PAUSED, run_store.IDLE) or (
                    revision_no is not None and revision_no not in (
                        int(active["revision_no"]), definition_store.latest_revision_no(flow_id))):
                raise GraphApiError(409, "run_already_active",
                                    {"flow_id": flow_id, "run_id": active["run_id"], "status": active["status"]})
            _hold_flow(active)
            _go_on(group, active, user_id, mode, "user_command", None, jobs)
            return {"run": run_payload(active["run_id"], with_nodes=True)}
        number = revision_no if revision_no is not None else definition_store.latest_revision_no(flow_id)
        if number is None or definition_store.revision(flow_id, number) is None:  # 5 개정
            raise GraphApiError(404, "revision_not_found", {"flow_id": flow_id, "revision_no": revision_no})
        snapshot = policy_set_for_run(flow_id)                                    # 6 정책
        policies = policies_of(snapshot["entries"])
        probe = load_context({"run_id": "", "group_id": flow_id, "revision_no": number, "status": "running",
                              "last_event_seq": 0, "started_by": user_id}, project_id, user_id, jobs)
        missing = _missing(probe, snapshot["entries"])
        if missing:
            raise _undecided(flow_id, number, missing)
        root = probe.root_document() if probe.binding else None                 # 7 결합 문서
        if probe.binding and (root is None or probe.binding["node_key"] not in run_scope(probe.revision).starts):
            raise GraphApiError(409, "root_binding_lost", {"flow_id": flow_id, "node_key": probe.binding["node_key"],
                                                           "doc_id": probe.binding["doc_id"]})
        command = mode or MODE_FULL
        run = run_store.create_run(new_key(RUN_ID_PREFIX, set()), flow_id, number, user_id, snapshot,
                                   probe.revision, command)
        ctx = load_context(run, project_id, user_id, jobs)
        emit_run_status(ctx, None, "running", "user_start")
        try:
            _take_root_binding(ctx, policies, root)
            d = resolve(ctx.revision, read_run_state(ctx), RunEvent("start"), policies)
            run_state_ports.record_step_targets(ctx, command, d)
            apply_decision(ctx, d)
        except PolicyUndecided as exc:                                            # E-21 (defence)
            raise _undecided(flow_id, number, [{"policy": exc.key, "rule_ref": RULE_REF[exc.key]}])
        return {"run": run_payload(ctx.run_id, with_nodes=True)}

    result = with_flow_guard(flow_id, body)
    run_jobs(jobs)
    return result


def _go_on(group: dict, run: dict, user_id: str, mode: Optional[str], cause: str, reason: Optional[str],
           jobs: list) -> None:
    """A paused or idle run goes on (L0009 §2.6.7, 0061 T#2): reconcile -> running (its new command recorded first,
    so the run status event tells it) -> re-dispatch -> drain -> run candidates. The command is
    ``run_state_ports.command_of``'s: a new one (``mode`` given, or an idle run) or, for a paused run with no mode,
    its own (a step keeps its targets). Inside the run's Flow guard; ownership and the Flow checked by the caller."""
    run_id, from_status = run["run_id"], run["status"]
    adopted = run_state_ports.adopt_revision(group, run, user_id, jobs)
    entries = run_store.policy_entries(run_id)
    policies = policies_of(entries)                                              # 3 (E-33 -> 500)
    ctx = load_context(run, group.get("project_id"), user_id, jobs)
    missing = _missing(ctx, entries)                                             # 4 (defence)
    if missing:
        raise _undecided(run["group_id"], int(run["revision_no"]), missing)
    command = run_state_ports.command_of(run, mode)                              # None: the paused command goes on
    if adopted and command is None:
        command = run.get("run_mode") or MODE_FULL
    try:
        from modules.flow_gate.services import graph_execution_hooks
        ctx.command_pending = True  # this explicit command owns any reconciled approval
        result_binder.reconcile_links(ctx, policies)                             # 5-1 while paused / idle
        ctx.command_pending = False
        # 0061 T#9: the integrations that wait (pending / integrating / failed) go first. Asked after the commit in
        # attempt order; until the drain has asked each of them the command holds — its re-dispatch, drain and
        # openings follow in ``go_on_command`` from the state of that moment (the Git work stays outside the guard).
        held = bool(graph_execution_hooks.call("retry_integrations", ctx))
        if run_store.set_run_status(run_id, from_status, run_store.RUNNING) != 1:
            raise AssertionError("resume missed its run")
        ctx.run["idle_reason"] = None
        if command is not None:
            run_state_ports.record_command(ctx, command)
        emit_run_status(ctx, from_status, run_store.RUNNING, cause, reason)
        if held:
            graph_execution_hooks.call("hold_command", ctx, command)
            return
        go_on_command(ctx, command, policies)
    except PolicyUndecided as exc:                                                # E-21 (defence)
        raise _undecided(run["group_id"], int(run["revision_no"]),
                         [{"policy": exc.key, "rule_ref": RULE_REF[exc.key]}])


def go_on_command(ctx: RunContext, command: Optional[str], policies) -> None:
    """The second half of ``_go_on``, inside the run's Flow guard: re-dispatch -> drain -> run candidates (a step
    records its targets). 0061 T#9: also the end of a command's integration hold, once its waiting integrations were
    asked (``flowgate.graph_run.result_merge``). 0061 T#11: a bound node its Hold kept pending at the start takes its
    input completion first, once released (``_take_root_binding``, 0056 T#1 개정)."""
    run_id = ctx.run_id
    _take_root_binding(ctx, policies)
    for row in run_store.node_runs(run_id):                                      # 5-3 re-dispatch
        if row["status"] == "open" and (row.get("dispatch_token_issued") == 0
                                      or node_dispatch.needs_ai_dispatch(ctx, row)):
            dispatch = node_dispatch.dispatch(ctx, row["node_key"])
            run_store.set_dispatch(run_id, row["node_key"], dispatch)
            emit_node_state(ctx, row["node_key"], "open", "open", "redispatched",
                            row.get("result_doc_id"), dispatch)
    event = RunEvent("resume" if command is None else "command")
    d = resolve(ctx.revision, read_run_state(ctx), event, policies)
    if command is not None:
        run_state_ports.record_step_targets(ctx, command, d)
    apply_decision(ctx, d)


def _refuse_time_machine(flow_id: str) -> None:
    """0061 T#13: while a Graph Time Machine operation of the Flow is unfinished (started, its Git applied, or partially
    failed), a step / full command is refused (409 ``graph_time_machine_in_progress``). Inside the command's guard;
    the v0.2 judgement is ``flowgate.graph_time_machine``'s (``graph_execution_hooks`` ``time_machine_refusal``)."""
    from modules.flow_gate.services import graph_execution_hooks
    graph_execution_hooks.call("time_machine_refusal", flow_id)


def _hold_flow(run: dict) -> None:
    """0061 T#2, one run per Flow: the run that goes on holds its Flow (``active_group_id``). A run written before
    migration 151 that completed gave the Flow up (NULL); it takes the Flow back — this run only, under the unique
    ``idx_graph_runs_active`` — when no run holds the Flow and no later run of the Flow follows it. Any other is
    refused as a start is (409 ``run_already_active``, naming the run that holds or follows). Inside the Flow guard."""
    flow_id = run["group_id"]
    if run.get("active_group_id") == flow_id:
        return
    holder = run_store.active_run(flow_id)
    if holder is None:
        latest, _total = run_store.list_runs(flow_id, 1, 0)
        holder = latest[0] if latest and latest[0]["run_id"] != run["run_id"] else None
    if holder is not None:
        raise GraphApiError(409, "run_already_active",
                            {"flow_id": flow_id, "run_id": holder["run_id"], "status": holder["status"]})
    if run_store.claim_flow(run["run_id"], flow_id) != 1:
        raise AssertionError("resume missed its Flow")
    run["active_group_id"] = flow_id


def _take_root_binding(ctx: RunContext, policies: dict, root: Optional[dict] = None) -> None:
    """0061 T#11 / 0056 T#1 개정: the bound input is marked done (``_root_bound_start``) while it is ``pending`` and
    has not been bound yet — at the run start, or at the first command after its Hold is released. A held one stays
    pending (the Hold bounds this as it bounds an opening); a root lost by then is refused as at the start (409
    ``root_binding_lost``)."""
    if ctx.binding is None:
        return
    key = ctx.binding["node_key"]
    row = next((r for r in run_store.node_runs(ctx.run_id) if r["node_key"] == key), None)
    if row is None or row["status"] != "pending" or int(row.get("root_bound") or 0) == 1:
        return
    from modules.flow_gate.services import graph_execution_hooks
    if key in frozenset(graph_execution_hooks.call("held_nodes", ctx.flow_id) or ()):
        return
    root = root if root is not None else ctx.root_document()
    if root is None:
        raise GraphApiError(409, "root_binding_lost", {"flow_id": ctx.flow_id, "node_key": key,
                                                       "doc_id": ctx.binding["doc_id"]})
    _root_bound_start(ctx, root, policies)


def _root_bound_start(ctx: RunContext, root: dict, policies: dict) -> None:
    """0056 T#1 개정: Run accepts the bound root as input, regardless of its review status.

    The input consumes neither approval nor a Step; only its card becomes done. The document's
    review status stays unchanged, and the existing approved propagation opens the first work card.
    """
    key, doc_id = ctx.binding["node_key"], root["doc_id"]
    to = "done"
    if run_store.set_root_bound(ctx.run_id, key, to, doc_id) != 1:
        raise AssertionError(f"root bound start missed node {key}")
    seq = emit_node_state(ctx, key, "pending", to, "root_bound_start", doc_id)
    run_store.insert_propagation(ctx.run_id, seq, key, "approved", "pending")


def _guarded_run(run_id: str, body_of) -> dict:
    """Stop / resume: ``flow_id`` from outside the guard picks the guard only (L0009 §2.1.1)."""
    outside = run_store.run_by_id(run_id)
    if outside is None:
        raise GraphApiError(404, "run_not_found", {"run_id": run_id})
    flow_id = outside["group_id"]
    jobs: list = []

    def body() -> dict:
        run = run_store.run_by_id(run_id)                                        # re-read after the bump
        if run is None:
            raise GraphApiError(404, "run_not_found", {"run_id": run_id})
        assert run["group_id"] == flow_id
        return body_of(run, jobs)

    result = with_flow_guard(flow_id, body)
    run_jobs(jobs)
    return result


def pause_run(group: dict, run_id: str, reason: Optional[str], user_id: str) -> dict:
    """``POST /runs/{run_id}/pause`` (P0008 S5-5, L0009 §2.6.7) — 0061 T#2: Pause (running -> paused). Nothing new
    opens and every propagation waits; open nodes keep their tokens and their work goes on; the run keeps its Flow
    (locked). Resume recomputes the candidates. 0061 T#7 moved it here from ``/stop``."""
    require_writable(group["group_id"])

    def body_of(run: dict, jobs: list) -> dict:
        if run["status"] != run_store.RUNNING:
            raise GraphApiError(409, "run_not_running", {"run_id": run_id, "status": run["status"]})
        if run_store.set_run_status(run_id, run_store.RUNNING, run_store.PAUSED) != 1:
            raise AssertionError("pause missed its run")
        ctx = load_context(run, group.get("project_id"), user_id, jobs)
        emit_run_status(ctx, run_store.RUNNING, run_store.PAUSED, "user_pause", reason)
        return {"run": run_payload(run_id, with_nodes=False)}

    return _guarded_run(run_id, body_of)


def stop_run(group: dict, run_id: str, reason: Optional[str], user_id: str) -> dict:
    """``POST /runs/{run_id}/stop`` — 0061 T#7 Stop (``run_state_ports.stop``, ``flowgate.graph_run.stop``): a running
    or paused run becomes stopping at once, and idle (stopped) once its AI processes ended and the worktrees of the
    attempts it invalidated are removed; a stopping run goes on with its stop; any other is 409 ``run_not_running``."""
    require_writable(group["group_id"])
    if run_store.run_by_id(run_id) is None:
        raise GraphApiError(404, "run_not_found", {"run_id": run_id})
    return run_state_ports.stop(group, run_id, reason, user_id)


def resume_run(group: dict, run_id: str, reason: Optional[str], user_id: str, mode: Optional[str] = None) -> dict:
    """``POST /runs/{run_id}/resume``: a paused or idle run goes on (``_go_on``) — with ``mode`` a new step / full
    command, without it a paused run's own command (an idle run: its last command). A running run is 409
    ``run_not_stopped`` (the code kept for the existing clients); a run that does not hold its Flow and may not take
    it back is 409 ``run_already_active`` (``_hold_flow``)."""
    require_writable(group["group_id"])
    _restore_check_once()

    def body_of(run: dict, jobs: list) -> dict:
        if run["status"] not in (run_store.PAUSED, run_store.IDLE):              # 1
            raise GraphApiError(409, "run_not_stopped", {"run_id": run_id, "status": run["status"]})
        own = read_ownership(run["group_id"])                                     # 2
        if own.state != "graph":
            raise _ownership_error(run["group_id"], own)
        _refuse_time_machine(run["group_id"])                                     # 0061 T#13
        _hold_flow(run)                                                           # 0061 T#2: one run per Flow
        _go_on(group, run, user_id, mode, "user_resume", reason, jobs)            # 3~5
        return {"run": run_payload(run_id, with_nodes=False)}

    return _guarded_run(run_id, body_of)


# ── Reads (P0008 S5-4) ───────────────────────────────────────────────────────

def run_payload(run_id: str, with_nodes: bool) -> Optional[dict]:
    run = run_store.run_by_id(run_id)
    if run is None:
        return None
    rows = run_store.node_runs(run_id)
    pending = run_store.pending_propagations(run_id)
    out = {
        "run_id": run["run_id"], "flow_id": run["group_id"], "revision_no": run["revision_no"],
        "status": run["status"], "started_at": run["started_at"], "started_by": run.get("started_by"),
        "stopped_at": run.get("stopped_at"), "finished_at": run.get("finished_at"),
        "open_node_keys": [r["node_key"] for r in rows if r["status"] == "open"],
        "last_event_seq": run["last_event_seq"],
        "pending_propagation_count": len(pending),
        "pending_node_keys": sorted({p["node_key"] for p in pending}),
        "mode": run.get("run_mode") or MODE_FULL, "idle_reason": run.get("idle_reason"),       # 0061 T#2
    }
    from modules.flow_gate.services import graph_execution_hooks
    out.update(graph_execution_hooks.call("execution_wire", run_id, run["group_id"]))
    if with_nodes:
        attempts = run_store.current_attempts(run_id)
        held = frozenset(graph_execution_hooks.call("held_nodes", run["group_id"]) or ())          # 0061 T#11
        asked = question_nodes(run_id, run["group_id"])                                           # 0063
        out["node_runs"] = [node_run_wire(r, run["group_id"], attempts.get(r["node_key"]), held, asked) for r in rows]
    return out


def question_nodes(run_id: str, flow_id: str) -> frozenset:
    """flowgate.v02.0063: the nodes whose own document (result document, the root bound one included, or instruction)
    holds a question (Q&A item) nobody has answered yet — the card of the document the question is on, not the card
    of the AI that asked it. A failed read marks nothing."""
    from modules.flow_gate.db import questions as db_questions  # noqa: PLC0415
    from modules.flow_gate.graph_workflow.store import assignment_store  # noqa: PLC0415
    try:
        asked = frozenset(db_questions.list_open_doc_ids_by_group(flow_id) or ())
        if not asked:
            return frozenset()
        binding = assignment_store.root_binding(flow_id) or {}
        nodes: set[str] = set()
        for row in run_store.node_runs(run_id):
            docs = {row.get("result_doc_id"), row.get("instruction_doc_id")}
            if row["node_key"] == binding.get("node_key"):
                docs.add(binding.get("doc_id"))
            if docs & asked:
                nodes.add(row["node_key"])
        return frozenset(nodes)
    except Exception:  # noqa: BLE001 — the run read goes on
        _log.warning("graph question nodes read failed flow=%s", flow_id, exc_info=True)
        return frozenset()


def node_run_wire(row: dict, flow_id: str, attempt_id: Optional[str] = None, held: Optional[frozenset] = None,
                  asked: Optional[frozenset] = None) -> dict:
    """flowgate.v02.0056 T#2: ``stage`` (``instruction`` / ``result``, null for a node no AI starts) and the
    card run's ``instruction_doc_id`` (null until known). 0061 T#2: the node's current ``attempt_id`` (its active
    attempt, else its last ended one; null for a node not opened since migration 151). 0061 T#9: that attempt's
    result integration (``integration_status``, ``integration_reason``; null when it has none). 0061 T#11: whether its
    card is held (``hold``; ``held``: the Flow's held node keys when the caller read them already). 0063: whether its
    document holds a question still unanswered (``question``; ``asked``: ``question_nodes`` when the caller read them already).
    0064 T#4: whether its card is running now (``node_running``, ``running_lock``)."""
    from modules.flow_gate.services import graph_execution_hooks
    if held is None:
        held = frozenset(graph_execution_hooks.call("held_nodes", flow_id) or ())
    if asked is None:
        asked = question_nodes(row["run_id"], flow_id)
    dispatch = dispatch_block(row)
    failure = graph_execution_hooks.call("write_failure", row)
    if failure and dispatch is not None:
        dispatch = {**dispatch, "error_code": failure}
    return {**graph_execution_hooks.call("execution_wire", row["run_id"], flow_id, attempt_id or ""),
            **(graph_execution_hooks.call("integration_wire", attempt_id) or {}),
            "node_key": row["node_key"], "status": row["status"], "opened_at": row.get("opened_at"),
            "finished_at": row.get("finished_at"), "result_doc_id": row.get("result_doc_id"),
            "root_bound": bool(row.get("root_bound")), "dispatch": dispatch,
            "awaiting_approval": node_approval.awaiting_approval(flow_id, row),
            "stage": row.get("stage"), "instruction_doc_id": row.get("instruction_doc_id"),
            "attempt_id": attempt_id, "hold": row["node_key"] in held, "question": row["node_key"] in asked,
            "node_running": running_lock(flow_id, row)}


def running_lock(flow_id: str, row: dict) -> bool:
    """0064 T#4: the card settings' running lock of the node run ``row``'s card (``card_settings_ports.node_running``,
    the one a title save is refused by). Only an open or review node can be running, so no other is asked."""
    return row.get("status") in ("open", "review") and card_settings_ports.node_running(flow_id, row["node_key"])


def settle_if_needed(flow_id: str, project_id: Optional[str]) -> None:
    """0061 T#2, before a run read and after a card stage followed a node's AI run: a running run whose work ended with
    no node event to judge it (a card review that ended without passing) goes idle or opens what its command may, an
    idle run whose existing work goes on again (a card review started) is running again, and an idle run's reason
    follows its nodes (``run_state_ports.settle``). A failure only skips it; the read goes on."""
    run = run_store.active_run(flow_id)
    if run is None:
        return
    try:
        if run_state_ports.settle_plan(load_context(run, project_id, None)) is None:
            return
    except Exception:  # noqa: BLE001 — as below: the read goes on
        _log.exception("graph run settle check failed flow=%s", flow_id)
        return
    jobs: list = []

    def body() -> None:
        if read_ownership(flow_id).state != "graph":
            return
        again = run_store.active_run(flow_id)
        if again is not None:
            settle_run(load_context(again, project_id, None, jobs))

    try:
        with_flow_guard(flow_id, body)
    except Exception:  # noqa: BLE001 — as reconcile_if_needed: roll the step back, answer the read
        _log.exception("graph run settle failed flow=%s", flow_id)
        return
    run_jobs(jobs)


def current_run(group: dict) -> dict:
    flow_id = group["group_id"]
    result_binder.reconcile_if_needed(flow_id, group.get("project_id"))
    settle_if_needed(flow_id, group.get("project_id"))
    run = run_store.current_run(flow_id)
    return {"run": run_payload(run["run_id"], with_nodes=True) if run else None}


def list_runs(group: dict, limit: int, offset: int) -> dict:
    rows, total = run_store.list_runs(group["group_id"], limit, offset)
    return {"flow_id": group["group_id"], "total": total, "limit": limit, "offset": offset,
            "items": [run_payload(r["run_id"], with_nodes=False) for r in rows]}


def get_run(run_id: str) -> dict:
    payload = run_payload(run_id, with_nodes=True)
    if payload is None:
        raise GraphApiError(404, "run_not_found", {"run_id": run_id})
    return {"run": payload}


def run_flow_id(run_id: str) -> str:
    run = run_store.run_by_id(run_id)
    if run is None:
        raise GraphApiError(404, "run_not_found", {"run_id": run_id})
    return run["group_id"]
