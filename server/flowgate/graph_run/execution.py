"""Durable start intent and process fencing for Graph AI executions (0061 T#4)."""
from __future__ import annotations

import contextlib
import json
import logging
import threading

from modules.flow_gate.db.connection import get_store
from modules.flow_gate.db import groups as db_groups, ai_invoke_runs
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.services import graph_execution_hooks
from flowgate.graph_run import execution_store as ledger, workspace, scheduling, fencing, stop, result_merge, hold, editing, command

log = logging.getLogger(__name__)


def register():
    graph_execution_hooks.register(lambda action, *args, **kwargs: _ACTIONS[action](*args, **kwargs))
    result_merge.install()      # T#9: every session close of a group reaches its Graph integrations
    from modules.flow_gate.services import graph_source_hooks
    graph_source_hooks.register(workspace.source_root, workspace.mutation, fencing.require_token)


def request_write(job):
    # Called inside the transaction opening the node or advancing its instruction stage.
    attempt = run_store.active_attempt_id(job["run_id"], job["node_key"])
    if not attempt:
        raise ledger.LeaseLost("write has no active attempt")
    # T#7: a stopping run requests nothing (inside the guard). The engine's own opening of a node is no attempt token's
    # write. 0083 WP-3 (0061 T#2 개정): a person's approval on an idle run opens the next one at once
    # (``frontier.defers``) after taking the run back to running (``command.wake_approval``), so the opening requests on
    # a running run; ``OPENING`` still admits idle as before (the settlement below), the acquire needs running / paused.
    fencing.check(attempt, "request_write", statuses=fencing.OPENING)
    _refuse_time_machine(job["flow_id"], attempt, "request_write")
    job["graph_attempt_id"] = attempt
    payload = dict(job)
    payload.pop("graph_exec_id", None)
    # A dispatch token's raw bearer is never persisted in the execution journal.
    payload["token"] = {"token_id": (job.get("token") or {}).get("token_id")}
    # flowgate.v02.0113 T#3 (P-1 §7.2): the assignment that made the request and the call's origin, at the top level
    # of the request (and in the ledger's assignment columns); a pre-feature attempt's card call names no assignment.
    ref = job.get("assignment_ref")
    origin = job.get("invocation_origin") or _legacy_origin(job, attempt)
    job["invocation_origin"] = origin
    payload["invocation_origin"] = origin
    old = run_store._one("SELECT * FROM graph_attempt_runs WHERE attempt_id = ? "
                         "AND status IN ('requested', 'running')", [attempt])
    if old:
        prior = json.loads(old["request_json"])
        # An already written instruction skips its process, then hands off to the result.
        if old["status"] == "requested" and prior.get("job", {}).get("stage") == "instruction" \
                and job.get("stage") == "result":
            ledger.cancel(old["exec_id"], None, "requested")
        else:
            raise ledger.LeaseLost("attempt already has an execution")
    request = {"type": "write", "job": payload, "invocation_origin": origin}
    if ref:
        request["assignment_ref"] = ref
    job["graph_exec_id"] = ledger.request(attempt, job["run_id"], "write", request,
                                          assignment=(ref["stage"], ref["seq"]) if ref else None)
    run = run_store.run_by_id(job["run_id"])
    if run and run["status"] == "idle":
        from modules.flow_gate.graph_workflow.services import run_apply
        run_apply.settle_run(run_apply.load_context(run, job.get("project_id"), None))
    return job["graph_exec_id"]


def _legacy_origin(job, attempt):
    """P-1 §7.2: the origin of a card call of a pre-feature attempt (no assignment reference)."""
    purpose = "instruction_write" if job.get("stage") == "instruction" else "result_write"
    if job.get("instruction_retry"):
        purpose = "instruction_write_retry"
    elif job.get("result_retry"):
        purpose = "result_write_retry"
    from modules.flow_gate.db import documents
    doc = documents.get_by_id(job.get("target_doc_id")) if job.get("target_doc_id") else None
    return {"origin_kind": "card_legacy_attempt", "purpose": purpose,
            "role": "instruction" if job.get("stage") == "instruction" else "execution",
            "role_source": "legacy_attempt", "requested_ai_id": job.get("provider_id"),
            "target": {"flow_id": job.get("flow_id"), "graph_run_id": job.get("run_id"), "node_key": job.get("node_key"),
                       "attempt_id": attempt, "stage": job.get("stage"), "seq": None, "doc_id": (doc or {}).get("doc_id"),
                       "doc_revision_no": int(doc.get("revision_no") or 0) if doc else None},
            "round_no": None}


def _attempt_for_document(flow_id, doc_id):
    if not get_store().table_exists("graph_attempt_runs"):
        return None
    return run_store._one(
        "SELECT a.* FROM graph_node_attempts a JOIN graph_runs r ON r.run_id = a.run_id "
        "JOIN graph_node_runs n ON n.run_id = a.run_id AND n.node_key = a.node_key "
        "WHERE r.group_id = ? AND (n.result_doc_id = ? OR n.instruction_doc_id = ?) "
        "ORDER BY CASE WHEN a.status = 'active' THEN 0 ELSE 1 END, a.created_at DESC, a.attempt_id DESC LIMIT 1",
        [flow_id, doc_id, doc_id])


def _guarded_request(flow_id, doc_id, kind, payload, existing, attempt_id, operation, statuses, assignment=None):
    """The guarded body of a document's hop / loop request (``_request_document``), inside the caller's Flow guard."""
    attempt = _attempt_for_document(flow_id, doc_id)
    if not attempt:
        raise ledger.LeaseLost("document has no Graph attempt")
    if attempt_id and attempt["attempt_id"] != attempt_id:
        raise fencing.refuse(attempt_id, operation, "attempt_superseded", active_attempt_id=attempt["attempt_id"])
    fencing.check(attempt["attempt_id"], operation, statuses=statuses)
    _refuse_time_machine(flow_id, attempt["attempt_id"], operation)
    if existing:
        row = ledger.get(existing)
        if not row or row["attempt_id"] != attempt["attempt_id"] or row["status"] != "requested":
            raise ledger.LeaseLost("execution is no longer requested")
        return existing
    # A prewritten instruction skips its writer and hands ownership to its review.
    # Cancel only the still-requested instruction writer of this exact attempt/document.
    if payload.get("type") == "loop":
        pending = run_store._one("SELECT * FROM graph_attempt_runs WHERE attempt_id = ? AND status = 'requested'",
                                 [attempt["attempt_id"]])
        if pending:
            prior = json.loads(pending["request_json"])
            row = run_store._one("SELECT instruction_doc_id, stage FROM graph_node_runs WHERE run_id = ? AND node_key = ?",
                                 [attempt["run_id"], attempt["node_key"]]) or {}
            if (prior.get("job", {}).get("stage") == "instruction" and row.get("stage") == "instruction"
                    and row.get("instruction_doc_id") == doc_id):
                ledger.cancel(pending["exec_id"], None, "requested")
    if not payload.get("invocation_origin"):
        from modules.flow_gate.db import documents
        doc = documents.get_by_id(doc_id) or {}
        row = run_store._one("SELECT stage FROM graph_node_runs WHERE run_id = ? AND node_key = ?",
                            [attempt["run_id"], attempt["node_key"]]) or {}
        base = "instruction" if row.get("stage") == "instruction" else "result"
        purpose = base + ("_rework" if kind == "rework" else "_review_round")
        loop = payload.get("loop") or {}
        payload["invocation_origin"] = {
            "origin_kind": "card_legacy_attempt", "purpose": purpose, "role_source": "legacy_attempt",
            "requested_ai_id": loop.get("rework_provider_id" if kind == "rework" else "reviewer_provider_id"),
            "target": {"flow_id": flow_id, "graph_run_id": attempt["run_id"], "node_key": attempt["node_key"],
                       "attempt_id": attempt["attempt_id"], "stage": base + "_review", "seq": None,
                       "doc_id": doc_id, "doc_revision_no": int(doc.get("revision_no") or 0)}}
    result = ledger.request(attempt["attempt_id"], attempt["run_id"], kind, payload, assignment=assignment)
    # An explicit review may start while its card was idle awaiting a person.
    run = run_store.run_by_id(attempt["run_id"])
    if run and run["status"] == "idle":
        from modules.flow_gate.graph_workflow.services import run_apply
        ctx = run_apply.load_context(run, (db_groups.get_by_id(flow_id) or {}).get("project_id"), None)
        run_apply.settle_run(ctx)
    return result


def _request_document(flow_id, doc_id, kind, payload, existing=None, attempt_id=None):
    attempt = _attempt_for_document(flow_id, doc_id)
    if not attempt:
        if existing:
            raise ledger.LeaseLost("recovered attempt is no longer active")
        return None
    operation = "hop_" + kind
    # T#7: a review / rework hop of an attempt that may not write any more (its run not running or paused, the attempt
    # not active) is refused (and recorded) before its guard, and judged again inside it (a refusal there is recorded
    # after the rollback, ``fencing.guarded``). Q439: the hop of a person's explicit review request is also taken
    # on an idle run (and takes it back to running below); an automatic hop is not. ``attempt_id``: the attempt the
    # run-end stage asking for the hop was made for — a node opened again since is not its to review.
    statuses = fencing.IDLE_EXCEPTED if graph_execution_hooks.explicit() and not existing else fencing.WRITABLE
    if attempt_id and attempt["attempt_id"] != attempt_id:
        raise fencing.refuse(attempt_id, operation, "attempt_superseded", active_attempt_id=attempt["attempt_id"])
    fencing.require(attempt["attempt_id"], operation, statuses=statuses)
    return fencing.guarded(flow_id, lambda: _guarded_request(flow_id, doc_id, kind, payload, existing, attempt_id,
                                                             operation, statuses))


def request_hop(flow_id, bundle, gate, kind):
    clean = {k: v for k, v in bundle.items() if k != "_graph_exec_id"}
    return _request_document(flow_id, gate["slot"]["doc_id"], kind,
                             {"type": "hop", "flow_id": flow_id, "bundle": clean, "gate": gate, "kind": kind},
                             bundle.get("_graph_exec_id"))


def request_loop(project_id, flow_id, doc_id, issued_to, loop, basis, node_key, existing=None, attempt_id=None):
    from modules.flow_gate.services.ai_invoke import review
    baseline = review.compute_review_baseline(doc_id)
    kind = "rework" if baseline.get("starts_with_rework") else "review"
    return _request_document(flow_id, doc_id, kind, {
        "type": "loop", "project_id": project_id, "flow_id": flow_id, "doc_id": doc_id,
        "issued_to": issued_to, "loop": {**loop, **baseline, "current_stage": kind},
        "basis": basis, "node_key": node_key}, existing, attempt_id)


def request_loop_locked(project_id, flow_id, doc_id, issued_to, loop, basis, node_key, *, assignment_ref,
                        origin=None, statuses="writable", attempt_id=None):
    """flowgate.v02.0113 T#3 (L-2 §2.2.2): ``request_loop`` inside the caller's Flow guard (the review assignment's own
    transaction) — no guard of its own, no AI start: the same judgements (``fencing.check``, the Time Machine refusal,
    the pending instruction writer's cancel, the idle run's settlement) and a ledger request carrying the assignment
    (its columns, and ``assignment_ref`` / ``invocation_origin`` at the top of the request). ``statuses``:
    ``writable`` (an AI run's end) or ``idle_excepted`` (a person's held step, Q439). A refusal raises; the caller rolls
    its savepoint back."""
    from modules.flow_gate.graph_workflow.services.flow_guard import held_guard
    if held_guard() != ("flow", flow_id):
        raise RuntimeError("request_loop_locked runs inside its Flow guard")
    from modules.flow_gate.services.ai_invoke import review
    baseline = review.compute_review_baseline(doc_id)
    kind = "rework" if baseline.get("starts_with_rework") else "review"
    payload = {"type": "loop", "project_id": project_id, "flow_id": flow_id, "doc_id": doc_id,
               "issued_to": issued_to, "loop": {**loop, **baseline, "current_stage": kind},
               "basis": basis, "node_key": node_key, "assignment_ref": dict(assignment_ref)}
    if origin is not None:
        payload["invocation_origin"] = dict(origin)
    chosen = fencing.IDLE_EXCEPTED if statuses == "idle_excepted" else fencing.WRITABLE
    return _guarded_request(flow_id, doc_id, kind, payload, None, attempt_id, "hop_" + kind, chosen,
                            assignment=(assignment_ref["stage"], assignment_ref["seq"]))


def request_origin(exec_id):
    """The ``invocation_origin`` / ``assignment_ref`` a ledger request carries (P-1 §7.2), or ``{}``."""
    row = ledger.get(exec_id) if exec_id else None
    try:
        payload = json.loads(row["request_json"]) if row else {}
    except (TypeError, ValueError):
        return {}
    return {k: payload[k] for k in ("invocation_origin", "assignment_ref") if k in payload}


def _hop_origin(payload, loop):
    """The successor hop's origin: the same assignment, the hop's purpose, its provider and its round (P-1 §7.2)."""
    origin = dict(payload.get("invocation_origin") or {})
    ref = payload.get("assignment_ref") or {}
    if not origin or not ref:
        return origin or None
    review_stage = "instruction" if ref.get("stage") == "instruction_review" else "result"
    rework = loop.get("current_stage") == "rework"
    origin.update(purpose=f"{review_stage}_{'rework' if rework else 'review_round'}",
                  role="rework" if rework else ("instruction_review" if review_stage == "instruction" else "review"),
                  requested_ai_id=loop.get("rework_provider_id") if rework else loop.get("reviewer_provider_id"),
                  round_no=loop.get("round_no"))
    return origin


def reopen_due(flow_id) -> bool:
    """flowgate.v02.0113 T#3 (L-2 §2.10, T §4.5), outside every guard: an open node of the Flow's paused / idle run would
    be replaced by an explicit command (``REOPEN_NEW_ATTEMPT``). Only a hint for the command's entry (``command_fence``):
    the replacement itself is judged again inside the command's guard. A read that fails answers False (no fence: the
    command then replaces nothing and the next command does)."""
    from modules.flow_gate.graph_workflow.services import node_dispatch, run_apply
    try:
        run = run_store.current_run(flow_id)
        if run is None:
            return False
        rows = [r for r in run_store.node_runs(run["run_id"]) if r["status"] == "open"]
        if not rows:
            return False
        ctx = run_apply.load_context(run, (db_groups.get_by_id(flow_id) or {}).get("project_id"), None, [])
        return any(node_dispatch.redispatch_kind(ctx, r) == node_dispatch.REOPEN for r in rows)
    except Exception:  # noqa: BLE001
        log.warning("reopen probe failed flow=%s", flow_id, exc_info=True)
        return False


@contextlib.contextmanager
def command_fence(flow_id):
    """flowgate.v02.0113 T#3 (T §4.5): an explicit command's entry, outside its Flow guard. When the command would
    replace an attempt (``reopen_due``), the Flow's Stop gate is taken exclusively first — the lock order every
    attempt replacement keeps (gate, then Flow guard; Stop): every side effect of the old attempt
    in flight (under the gate's shared hold) ends before the replacement commits, and none starts across it. A thread
    already inside a shared hold of the Flow takes no fence (its command replaces nothing). Yields whether it fenced."""
    if fencing._held().get(flow_id) or not reopen_due(flow_id):
        yield False
        return
    with fencing.exclusive(flow_id):
        yield True


def _reopen_refusal(ctx, row, attempt_id):
    """Why the node's attempt is not replaced now (judged again inside the command's guard, T §4.5), or None."""
    from modules.flow_gate.graph_workflow.services import card_settings_ports, node_dispatch, result_binder, run_apply
    key = row["node_key"]
    if not fencing.exclusive_held(ctx.flow_id):
        return "not_fenced"                                    # the command's entry did not fence: the next one does
    if node_dispatch.redispatch_kind(ctx, row) != node_dispatch.REOPEN:
        return "assignment"                                    # the current stage assignment (held nodes: NONE)
    if key in frozenset(hold.held(ctx.flow_id) or ()):
        return "held"
    if _time_machine().busy(ctx.flow_id):
        return "time_machine_in_progress"
    attempt = run_store._one("SELECT * FROM graph_node_attempts WHERE attempt_id = ?", [attempt_id]) or {}
    if row.get("result_doc_id") or attempt.get("result_doc_id") or attempt.get("result_commit"):
        return "result_exists"
    if run_store._one("SELECT 1 AS present FROM graph_attempt_runs WHERE attempt_id = ? "
                      "AND status IN ('requested', 'running') LIMIT 1", [attempt_id]):
        return "execution_active"
    if card_settings_ports.ai_working(ctx.flow_id, key, None):
        return "ai_running"
    if result_binder.next_missing_event(ctx, run_apply.read_run_state(ctx), key) is not None:
        return "result_exists"                                 # a stored registration whose contact was lost binds
    return None


def reopen_attempt(ctx, row):
    """flowgate.v02.0113 T#3 (L-2 §2.10 ``REOPEN_NEW_ATTEMPT``), inside the explicit command's Flow guard, entered under
    the Flow's exclusive Stop gate (``command_fence``): the node's attempt (an assigned one waiting for a setting, an
    unavailable AI, or failed) is invalidated with its tokens, the node reset and opened again as a new attempt, then
    dispatched as any opening is (its stages resolved anew). 0038-T T#3 B-6: also a waiting write stage — a person's
    manual write, an AI setup or an unavailable AI's wait — whose role changed (``reinterpret_wait``, invalidated as
    ``stage_reinterpret``: its documents stay the card's); an unchanged wait is never replaced. The
    replacement is judged again here (T §4.5): the fence
    held, the current stage assignment, Hold, Time Machine, no result (a lost registration binds first), no execution
    or AI working. A node in review is never reopened (its result stays). Late contacts of the old attempt find it
    invalidated (``fencing``) and touch nothing of the new one."""
    from modules.flow_gate.graph_workflow.services import card_settings_ports, node_dispatch, run_apply
    current = next((r for r in run_store.node_runs(ctx.run_id) if r["node_key"] == row["node_key"]), None)
    if current is None or current["status"] != "open":
        return None
    attempt_id = run_store.active_attempt_id(ctx.run_id, row["node_key"])
    if not attempt_id:
        return None
    why = _reopen_refusal(ctx, current, attempt_id)
    if why is not None:
        log.info("attempt not replaced run=%s node=%s: %s", ctx.run_id, row["node_key"], why)
        return None
    # 0038-T T#3 B-6: a waiting stage whose role changed is resolved again as a new attempt — the old attempt's tokens
    # are revoked (a person's write permission of the old assignment), its documents stay the card's
    # (``run_store.REINTERPRET_REASON``)
    verdict = card_settings_ports.stage_needs_dispatch(ctx, current)
    reason = run_store.REINTERPRET_REASON if verdict == "reinterpret_wait" else "stage_reopen"
    if not stop.invalidate_attempt(attempt_id, reason):
        return None
    current = next((r for r in run_store.node_runs(ctx.run_id) if r["node_key"] == row["node_key"]), current)
    if not stop.reset_node(ctx, current, reason):
        return None
    run_store.open_attempt(ctx.run_id, row["node_key"], int(ctx.run["revision_no"]))
    dispatch = node_dispatch.dispatch(ctx, row["node_key"])
    if run_store.open_node_run(ctx.run_id, row["node_key"], dispatch) != 1:
        raise AssertionError("graph run write missed its row: reopen " + row["node_key"])
    run_apply.emit_node_state(ctx, row["node_key"], "pending", "open", "opened", None, dispatch)
    if attempt_id:
        ctx.jobs.append(lambda: workspace.cleanup(attempt_id))
    return dispatch


def loop_state(exec_id):
    """Internal replay data, never taken from an HTTP caller's loop settings."""
    if not exec_id:
        return None
    row = ledger.get(exec_id)
    if not row or row["status"] != "requested":
        raise ledger.LeaseLost("review loop request is no longer available")
    payload = json.loads(row["request_json"])
    if payload.get("type") != "loop":
        return None
    return {"loop": dict(payload["loop"]), "review_run_ids": list(payload.get("review_run_ids") or [])}


def validate_write(job):
    exec_id = job.get("graph_exec_id")
    if not exec_id:
        raise ledger.LeaseLost("Graph write has no durable execution")
    row = ledger.get(exec_id)
    if not row or row["run_id"] != job["run_id"]:
        raise ledger.LeaseLost("Graph write no longer belongs to the open attempt")
    # T#7: no token for a stopping run's write, nor for an execution or attempt that no longer owns the node. Inside
    # the mapping guard: the refusal is recorded once it rolled back (``start_failed``).
    if row["status"] not in ("requested", "running"):
        raise fencing.refuse(row["attempt_id"], "validate_write", "execution_" + str(row["status"]), exec_id=exec_id)
    active = run_store.active_attempt_id(job["run_id"], job["node_key"])
    if row["attempt_id"] != active:
        raise fencing.refuse(row["attempt_id"], "validate_write", "attempt_superseded", exec_id=exec_id,
                             active_attempt_id=active)
    fencing.check(row["attempt_id"], "validate_write")


def _lock(run):
    # Short lifecycle writes only; never hold this while running or killing a provider.
    return run.setdefault("_graph_execution_lock", threading.RLock())


def _remote_work_alive(run, exec_id):
    """flowgate.v02.0129 T#2 (D0005 §3-3): a remote Job is a provider too. Alive while the
    run marks it, or while any remote Job of this run or of an execution of the same node
    (an earlier attempt / replay this launch follows) is not settled."""
    if run.get("remote_job_active"):
        return True
    from modules.flow_gate.services.ai_invoke import remote_supervisor
    if not remote_supervisor.available():
        return False
    if remote_supervisor.run_has_unsettled_job(run.get("run_id")):
        return True
    exec_ids = [exec_id]
    try:
        row = ledger.get(exec_id) or {}
        attempt = run_store._one("SELECT run_id, node_key FROM graph_node_attempts WHERE attempt_id = ?",
                                 [row.get("attempt_id")]) if row.get("attempt_id") else None
        if attempt:
            exec_ids += [r["exec_id"] for r in run_store._all(
                "SELECT e.exec_id FROM graph_attempt_runs e JOIN graph_node_attempts a "
                "ON a.attempt_id = e.attempt_id WHERE a.run_id = ? AND a.node_key = ?",
                [attempt["run_id"], attempt["node_key"]])]
    except Exception:
        log.warning("Graph launch could not list the node's executions for %s", exec_id, exc_info=True)
    return remote_supervisor.executions_unsettled(dict.fromkeys(exec_ids))


def launch(run):
    exec_id = run.get("graph_exec_id")
    if not exec_id:
        return True
    with _lock(run):
        try:
            if run["cancel_event"].is_set() and not run.get("graph_lease_token"):
                try:
                    ledger.cancel(exec_id, None, "requested")
                except ledger.LeaseLost:
                    pass  # Cancellation of this launcher must not cancel an acquired winner.
            if run["cancel_event"].is_set() or run.get("graph_execution_finished"):
                raise ledger.LeaseLost("execution was cancelled or finished")
            proc = run.get("proc")
            if proc is not None and proc.poll() is None:
                raise ledger.LeaseLost("previous provider has not exited")
            if _remote_work_alive(run, exec_id):
                raise ledger.LeaseLost("previous provider has not exited")
            workspace.verify_execution(exec_id)
            if run.get("graph_lease_token"):
                ledger.renew(exec_id, run["graph_lease_token"])
            else:
                # Capacity waits keep the durable request and hold no Flow/DB lock.
                while not run["cancel_event"].is_set():
                    token = scheduling.acquire(exec_id, run["run_id"])
                    if token:
                        run["graph_lease_token"] = token
                        break
                    run["cancel_event"].wait(0.1)
                if not run.get("graph_lease_token"):
                    ledger.cancel_requested(exec_id, run["run_id"])
                    raise ledger.LeaseLost("execution cancelled while waiting for capacity")
                workspace.verify_execution(exec_id)
            return True
        except (ledger.LeaseLost, workspace.port.GraphSourceError):
            run["graph_lease_lost"] = True
            run["cancel_event"].set()
            ledger.cancel_ineligible(exec_id)
            return False


def _finish_owned(run):
    token = run.get("graph_lease_token")
    # A losing launcher never touches the winner's row or card.
    if run.get("graph_lease_lost") and not token:
        # 0063 B7: a launcher whose provider chain failed before its acquire (a refused report: the execution is still
        # requested) keeps its own admission claim, which no refill replays (``queue_requests``). It cancels that claim
        # only -- never a winner's -- and its node records the AI start failure (``finish``).
        if ledger.cancel_requested(run["graph_exec_id"], run.get("run_id")) == 1:
            run["graph_start_failed"] = True
        return False
    from modules.flow_gate.services.ai_invoke.terminal import provider_stopped
    if not provider_stopped(run):
        raise RuntimeError("cannot finish a live Graph process")
    row = ledger.get(run["graph_exec_id"])
    if not row:
        raise ledger.LeaseLost("execution disappeared")
    if token and row["status"] in ("cancelled", "lost") and row.get("lease_token") == token:
        # T#7: a Stop (or the recovery) settled this execution after its process ended; this late end changes
        # neither a node nor the run candidates.
        run["graph_report_late"] = True
        return True
    if token:
        status = "cancelled" if run["cancel_event"].is_set() else "ended"
        ledger.finish(row["exec_id"], token, status)
    elif row["status"] == "requested":
        ledger.cancel(row["exec_id"], None, "requested")
    else:
        raise ledger.LeaseLost("unowned execution")
    return True


def finish(run):
    """The execution's ledger end (``terminal.cleanup``). T#7: only an end owned to its close — the launcher held the
    lease (or cancelled its own request), the ledger took the end, the attempt is not invalidated, the run is not
    stopping and no Stop / recovery settled the execution first — is marked ``graph_end_owned``; the finalize hands
    nothing else on and settles nothing after it (``finalize._finalize_run``). An owned end the finalize hands on to
    its run-end stage (``graph_run_end_pending``) keeps the run working until that stage is over (``handoff_end``);
    any other owned end settles here, as before."""
    if not run.get("graph_exec_id"):
        return
    with _lock(run):
        if not run.get("graph_execution_finished"):
            run["graph_execution_finished"] = _finish_owned(run)
    if run.pop("graph_start_failed", False):
        _start_failure(run)
    if not run.get("graph_execution_finished") or run.get("graph_end_judged"):
        return
    run["graph_end_judged"] = True
    row = ledger.get(run["graph_exec_id"]) or {}
    graph = run_store.run_by_id(row["run_id"]) if row.get("run_id") else None
    if graph is not None and graph["status"] == stop.STOPPING:
        run["graph_settle_skipped"] = True
        stop.on_execution_finished(run["group_id"], graph["run_id"])     # T#7: a Stop waiting for this end
        return
    attempt = fencing.attempt_row(row["attempt_id"]) if row.get("attempt_id") else None
    if run.get("graph_report_late") or (attempt and attempt["attempt_status"] == "invalidated"):
        # T#7: an invalidated attempt's or a settled execution's end settles nothing — nor does the finalize's last
        # settle after it (``graph_settle_skipped``).
        run["graph_settle_skipped"] = True
        return
    run["graph_end_owned"] = True
    if run.get("graph_run_end_pending") and graph is not None:
        ledger.begin_handoff(run["graph_exec_id"], graph["run_id"])      # T#7: settled after the run-end stage
        return
    settle(run["group_id"])


def _start_failure(run):
    """0063 B7: the request its launcher cancelled before the acquire. A node write records the AI start failure as a
    refused start does (``node_dispatch.record_ai_failure``, L0009 E-23: the token stays a person's, the run settles idle
    with ``error``); a review / rework hop leaves the result to a person (``start_review_loop``) and the run settles."""
    row = ledger.get(run["graph_exec_id"]) or {}
    payload = json.loads(row.get("request_json") or "{}")
    if payload.get("type") == "write":
        from modules.flow_gate.graph_workflow.services.node_dispatch import record_ai_failure
        record_ai_failure(payload["job"])
    else:
        settle(run["group_id"])


def handoff_end(run):
    """T#7, the finalize after its run-end stage (whatever happened there): the run is no longer kept working by this
    execution's hand-off. The finalize's own settle follows for an owned end."""
    if run.get("graph_exec_id"):
        ledger.end_handoff(run["graph_exec_id"])


def node_hold(run_id, node_key, attempt_id, operation, **options):
    """``graph_execution_hooks`` ``node_hold``: ``fencing.node_hold`` (0083 WP-4: its ``idle_ok`` / ``also`` options)."""
    return fencing.node_hold(run_id, node_key, attempt_id, operation, **options)


def transition(run, loop):
    if not run.get("graph_exec_id"):
        return
    with _lock(run):
        if run.get("graph_lease_lost") or run["cancel_event"].is_set():
            raise ledger.LeaseLost("cancelled execution cannot advance a review loop")
        current = ledger.get(run["graph_exec_id"])
        if current:
            # T#7: a review verdict reaches its next hop only while the run and attempt may take it (recorded here).
            fencing.require(current["attempt_id"], "review_transition", statuses=fencing.REPORTING)
        def body():
            row = ledger.get(run["graph_exec_id"])
            if not row:
                raise ledger.LeaseLost("review execution disappeared")
            # T#7: judged again inside the guard; a refusal is recorded once the guard rolled back (``guarded``).
            if row["status"] != "running":
                raise fencing.refuse(row["attempt_id"], "review_transition", "execution_" + str(row["status"]),
                                     exec_id=row["exec_id"])
            fencing.check(row["attempt_id"], "review_transition", statuses=fencing.REPORTING)
            if row.get("cancel_requested_at"):
                raise fencing.refuse(row["attempt_id"], "review_transition", "execution_cancel_requested",
                                     exec_id=row["exec_id"])
            if row["lease_token"] != run.get("graph_lease_token"):
                raise fencing.refuse(row["attempt_id"], "review_transition", "lease_token_stale",
                                     exec_id=row["exec_id"])
            if row["kind"] == loop["current_stage"] and loop.get("last_hop_outcome") == "failed":
                return None  # A no-output/provider retry renews the same execution.
            payload = json.loads(row["request_json"])
            _finish_owned(run)
            payload["loop"] = dict(loop)
            payload["review_run_ids"] = sorted(set(payload.get("review_run_ids") or []) | {run["run_id"]})
            ref = payload.get("assignment_ref")          # 0113 T#3: the same assignment on every hop (P-1 §7.2)
            hop_origin = _hop_origin(payload, loop)
            if hop_origin is not None:
                payload["invocation_origin"] = hop_origin
            successor = ledger.request(row["attempt_id"], row["run_id"], loop["current_stage"], payload,
                                       assignment=(ref["stage"], ref["seq"]) if ref else None)
            if hop_origin is not None:
                run["invocation_origin"] = hop_origin
            run_store._affected("UPDATE graph_attempt_runs SET ai_run_id = ? WHERE exec_id = ?",
                                [run["run_id"], successor])
            return successor
        successor = fencing.guarded(run["group_id"], body)
        # Publish the successor only after both ledger writes have committed.
        if successor:
            run["graph_exec_id"] = successor
            run.pop("graph_lease_token", None)
            run.pop("graph_execution_finished", None)
    if successor:
        settle(run["group_id"])


def heartbeat(ai_run_id):
    from modules.flow_gate.services import ai_invoke_service
    run = ai_invoke_service.get_run_record(ai_run_id)
    if not run or not run.get("graph_exec_id"):
        return
    try:
        with _lock(run):
            if not run.get("graph_lease_token") or run.get("graph_execution_finished"):
                return
            ledger.renew(run["graph_exec_id"], run["graph_lease_token"])
    except ledger.LeaseLost:
        run["graph_lease_lost"] = True
        ai_invoke_service.cancel_run(ai_run_id)
        raise


def start_failed(exec_id, error=None):
    if error is not None:
        fencing.recorded(error)          # T#7: a refusal judged inside the start's guard, recorded after its rollback
    if not exec_id:
        return False
    if isinstance(error, graph_execution_hooks.ExecutionLeaseLost):   # T#7: a fence refusal too
        ledger.cancel_ineligible(exec_id)
        return True
    detail = getattr(error, "detail", None)
    if isinstance(detail, dict) and detail.get("code") == "graph_worktree_unavailable" \
            and detail.get("reason") == "git_busy":
        return True  # Admission releases its claim; a later refill can retry this request.
    if isinstance(detail, dict) and detail.get("reason") == "worktree_failed":
        return True  # Workspace preparation already invalidated/reset the node.
    if isinstance(detail, dict) and detail.get("code") == "run_in_progress":
        return True  # Admission is busy; keep the durable request, including duplicate launches.
    row = ledger.get(exec_id)
    if not row or row["status"] != "requested":
        return True  # A duplicate admission is not a node failure.
    try:
        ledger.cancel(exec_id, None, "requested")
    except ledger.LeaseLost:
        return True  # Another launcher acquired between the read and the cancellation CAS.
    return False


def replay(row):
    payload = json.loads(row["request_json"])
    if payload["type"] == "write":
        from modules.flow_gate.graph_workflow.services.node_dispatch import start_ai
        start_ai({**payload["job"], "graph_exec_id": row["exec_id"]})
    elif payload["type"] == "hop":
        from modules.flow_gate.services.ai_invoke import review
        spawn = review._spawn_review_hop if row["kind"] == "review" else review._spawn_rework_hop
        spawn(payload["flow_id"], {**payload["bundle"], "_graph_exec_id": row["exec_id"]}, payload["gate"])
    elif payload["type"] == "loop":
        from flowgate.card_settings.legacy_adapter import start_review_loop
        start_review_loop(payload["project_id"], payload["flow_id"], payload["doc_id"], payload["issued_to"],
                          payload["loop"], lambda _reason: payload["basis"], payload["node_key"],
                          graph_exec_id=row["exec_id"])
    else:
        raise ValueError("unknown execution request type")


_RECOVER_ROWS_SQL = ("SELECT e.*, r.group_id FROM graph_attempt_runs e JOIN graph_runs r "
                     "ON r.run_id = e.run_id WHERE e.status IN ('requested', 'running') "
                     "AND r.status <> 'stopping'")


def recover():
    """Once after orphan AI cleanup and Graph port registration. Never infer death from TTL alone."""
    from modules.flow_gate.services import ai_invoke_service
    from modules.flow_gate.graph_workflow.services import run_apply
    if not get_store().table_exists("graph_attempt_runs"):
        return
    result_merge.recover()  # T#9: an integrating merge is finished or pending again, before a Stop goes on
    stop.recover()          # T#7: a stopping run goes on with its stop, never with a replay or a lease loss
    rows = run_store._all(_RECOVER_ROWS_SQL + " ORDER BY e.created_at, e.exec_id", [])
    for snapshot in rows:
        _recover_snapshot(snapshot, ai_invoke_service, run_apply)

    # Outside all Flow guards and DB transactions. T#10: invalidated and integrated (ended) attempts' trees.
    workspace.cleanup_finished()


def recover_execution(exec_id):
    """flowgate.v02.0129 T#2 (D0005 §3-4 step 4): the startup recovery of ONE execution whose
    restart recovery was held while its remote Job was alive, once that Job is settled and
    its lease expired. Same rules as ``recover`` for that row; nothing else is replayed."""
    from modules.flow_gate.services import ai_invoke_service
    from modules.flow_gate.graph_workflow.services import run_apply
    if not get_store().table_exists("graph_attempt_runs"):
        return
    for snapshot in run_store._all(_RECOVER_ROWS_SQL + " AND e.exec_id = ?", [exec_id]):
        _recover_snapshot(snapshot, ai_invoke_service, run_apply)
    workspace.cleanup_finished()


def _recover_snapshot(snapshot, ai_invoke_service, run_apply):
    jobs = []
    invalidated = []
    def body():
        row = ledger.get(snapshot["exec_id"])
        if not row or row["status"] not in ("requested", "running"):
            return
        if row["status"] == "requested":
            if _time_machine().busy(snapshot["group_id"]):
                return              # T#13: kept requested; replayed once the Time Machine operation is finished
            if ledger.eligible(row):
                live = ai_invoke_service.get_run_record(row.get("ai_run_id")) if row.get("ai_run_id") else None
                if live and live.get("status") != "finished":
                    return
                run_store._affected("UPDATE graph_attempt_runs SET ai_run_id = NULL WHERE exec_id = ? "
                                    "AND status = 'requested'", [row["exec_id"]])
                jobs.append(lambda: replay(row))
            else:
                ledger.cancel(row["exec_id"], None, "requested")
            return
        if not row.get("lease_expires_at") or row["lease_expires_at"] > ledger.now():
            return
        live = ai_invoke_service.get_run_record(row.get("ai_run_id"))
        ended = ai_invoke_runs.get(row.get("ai_run_id")) if row.get("ai_run_id") else None
        from modules.flow_gate.services.ai_invoke.terminal import provider_stopped
        if (live and (live.get("status") != "finished" or not provider_stopped(live))) or not ended:
            log.warning("Graph recovery has no confirmed AI termination: %s", row["exec_id"])
            return
        # flowgate.v02.0129 T#2 (D0005 §3-4 step 2): a remote Job not settled yet, or a
        # restart hold not resumed yet, is no confirmed termination either.
        from modules.flow_gate.services.ai_invoke import remote_supervisor
        if remote_supervisor.remote_alive(row.get("ai_run_id")):
            log.warning("Graph recovery waits for the remote Job of %s", row["exec_id"])
            return
        ledger.mark_lost(row["exec_id"], row["lease_token"])
        attempt = run_store._one("SELECT * FROM graph_node_attempts WHERE attempt_id = ?", [row["attempt_id"]])
        if not stop.invalidate_attempt(row["attempt_id"], "lease_expired"):
            return
        invalidated.append(row["attempt_id"])
        run = run_store.run_by_id(row["run_id"])
        node = next((n for n in run_store.node_runs(row["run_id"]) if n["node_key"] == attempt["node_key"]), None)
        if not node or node["status"] == "done":
            return
        ctx = run_apply.load_context(run, (db_groups.get_by_id(snapshot["group_id"]) or {}).get("project_id"), None, jobs)
        stop.reset_node(ctx, node, "lease_expired")
        run_apply.settle_run(ctx)  # T#2 command candidates preserve the step target set.
    try:
        # T#7: a lease-expired attempt is invalidated under the Stop gate's exclusive hold, as a Stop and a failed
        # worktree are (no side effect of its token runs across the commit).
        with fencing.exclusive(snapshot["group_id"], own_ok=True):
            with_flow_guard(snapshot["group_id"], body)
    except Exception:
        log.exception("Graph execution recovery failed for %s", snapshot["exec_id"])
        return  # A failed transaction never dispatches its jobs.
    # Outside the exclusive fence and Flow transaction, before replaying deferred jobs.
    # Keep this even when the invalidated attempt has no node (or the node is done).
    for attempt_id in invalidated:
        workspace.cleanup(attempt_id)
    for job in jobs:
        try:
            job()
        except Exception as exc:
            log.exception("Graph execution replay failed for %s", snapshot["exec_id"])
            try:
                start_failed(snapshot["exec_id"], exc)
            except Exception:
                log.exception("Graph replay failure could not be recorded for %s", snapshot["exec_id"])


def map_token(exec_id, token_id):
    if not exec_id:
        return
    from modules.flow_gate.services import token_service
    try:
        row = ledger.get(exec_id)
        if not row:
            raise ledger.LeaseLost("execution disappeared before token mapping")
        run = run_store.run_by_id(row["run_id"])
        def body():
            current = ledger.get(exec_id)
            if not current or current["status"] not in ("requested", "running"):
                raise fencing.refuse(row["attempt_id"], "map_token",
                                     "execution_" + str((current or {}).get("status") or "missing"), exec_id=exec_id)
            # T#7: a stopping run or an invalidated attempt maps no new token (recorded after the rollback).
            fencing.check(row["attempt_id"], "map_token")
            attempt = run_store._one("SELECT * FROM graph_node_attempts WHERE attempt_id = ? AND status = 'active'",
                                     [row["attempt_id"]])
            payload = json.loads(current["request_json"])
            doc_id = (payload.get("gate", {}).get("slot", {}).get("doc_id") or payload.get("doc_id"))
            node = next(n for n in run_store.node_runs(row["run_id"]) if n["node_key"] == attempt["node_key"])
            stage = "instruction" if doc_id and doc_id == node.get("instruction_doc_id") else "result"
            if not run_store.node_run_by_token(token_id):
                run_store.map_token(token_id, row["run_id"], attempt["node_key"], stage, map_kind=run_store.MAP_ATTEMPT)
        fencing.guarded(run["group_id"], body)
    except Exception:
        token_service.revoke(token_id, reason="graph_attempt_not_active")
        raise


def write_failure(row):
    """0063: a finished result writer with no result stays open, but is no longer working.
    Derive from the current attempt's durable records, also for runs ended before this fix.
    No new node state or persisted failure is needed; a new request hides the old outcome.

    0111 T#2 (T-A1): a writer that exited asking (stop code ``question_pending``) with questions it asked still open is
    ``question_pending`` — a question wait, no failure (``settle._failed``, ``needs_ai_dispatch``, the node wire's
    ``error_code``); once they are all answered it reads ``no_result``, retried as one (``retry_result_stage``).
    """
    return _write_end(row)[0]


def _write_end(row):
    """(``write_failure``'s answer, the finalized ``ai_invoke_runs`` row it was read from or None)."""
    if row.get("status") != "open" or row.get("result_doc_id") or row.get("stage") != "result":
        return None, None
    if not get_store().table_exists("graph_attempt_runs") or ledger.handing_off(row["run_id"]):
        return None, None
    attempt = run_store.active_attempt_id(row["run_id"], row["node_key"])
    if not attempt:
        return None, None
    if run_store._one("SELECT 1 AS present FROM graph_attempt_runs WHERE attempt_id = ? "
                      "AND status IN ('requested', 'running') LIMIT 1", [attempt]):
        return None, None
    latest = run_store._one("SELECT * FROM graph_attempt_runs WHERE attempt_id = ? "
                            "ORDER BY created_at DESC, exec_id DESC LIMIT 1", [attempt])
    if not latest or latest["kind"] != "write" or latest["status"] != "ended" or not latest.get("ai_run_id"):
        return None, None
    job = json.loads(latest["request_json"]).get("job") or {}
    if job.get("stage") != "result":
        return None, None
    ended = ai_invoke_runs.get(latest["ai_run_id"])
    if not ended or ended.get("status") != "finished":
        return None, None
    # A registration whose contact was lost must reconcile normally, never look like missing output.
    from modules.flow_gate.graph_workflow.services import result_binder, run_apply
    graph = run_store.run_by_id(row["run_id"])
    if not graph or graph["status"] == stop.STOPPING:
        return None, None
    ctx = run_apply.load_context(graph, None, None)
    if result_binder.next_missing_event(ctx, run_apply.read_run_state(ctx), row["node_key"]) is not None:
        return None, None
    if ended.get("end_reason") == "exited" and ended.get("exit_code") == 0             and ended.get("stop_code") in (None, "", "no_output_exhausted", QUESTION_PENDING)             and not ended.get("register_errors"):
        if ended.get("stop_code") == QUESTION_PENDING and _asked_open(graph["group_id"], latest["ai_run_id"]):
            return QUESTION_PENDING, ended
        return "no_result", ended
    return "ai_invoke_failed", ended


QUESTION_PENDING = "question_pending"
# flowgate.v02.0121 T#2: the one ``ai_invoke_failed`` end an explicit command writes again on the same attempt.
PROVIDER_FAILED = "provider_failed"


def provider_failure_retry_due(row):
    """flowgate.v02.0121 T#1 / T#2: the node's result writer ended because its provider itself failed — the finalized
    run exited on its own (``end_reason`` ``exited``) with the canonical stop code ``provider_failed`` (a non-zero CLI
    exit or a ``turn.failed`` / ``error`` event, ``finalize.provider_failure_of``: a model at capacity, a dropped
    stream) and registered nothing, nothing refused. Read from the same durable records as ``write_failure`` (it is
    ``ai_invoke_failed`` here: the current attempt has no requested / running execution, its latest one is that
    writer's ended result write, no registration is waiting to be reconciled, the run is not stopping). Every other
    ``ai_invoke_failed`` end keeps its own policy: a cancel, a pause, a timeout (``end_reason`` is not ``exited``),
    ``providers_exhausted``, a refused worker (``group_lease_denied``), an inbox stop or a refused registration
    (``register_errors``). A read only: nothing is requested or written here."""
    failure, ended = _write_end(row)
    return failure == "ai_invoke_failed" and ended is not None and ended.get("end_reason") == "exited"         and ended.get("stop_code") == PROVIDER_FAILED and not ended.get("register_errors")


# flowgate.v02.0121 T#4: the short causes a failed result writer's card shows (the node wire's ``dispatch.error_detail``)
PROVIDER_CAPACITY = "provider_capacity"
_SHOWN_STOPS = (PROVIDER_FAILED, "timeout", "providers_exhausted")


def write_failure_detail(row):
    """flowgate.v02.0121 T#4: the short cause of the node's ``ai_invoke_failed`` result writer — ``provider_capacity``
    (its provider said the model is at capacity: the canonical ``stop_reason`` the finalize wrote from the provider's
    own words, ``finalize._stop_reason_text``), else its stop code when it is one of ``_SHOWN_STOPS``; None for any
    other end (nothing is guessed). The provider's whole output stays in the run's log and records."""
    failure, ended = _write_end(row)
    if failure != "ai_invoke_failed" or not ended:
        return None
    code = ended.get("stop_code")
    if code == PROVIDER_FAILED and "at capacity" in str(ended.get("stop_reason") or "").lower():
        return PROVIDER_CAPACITY
    return code if code in _SHOWN_STOPS else None


def _asked_open(flow_id, ai_run_id):
    """0111 T-A1: the questions ``ai_run_id`` asked in the Flow are not all answered (an unreadable read keeps the wait:
    nothing retries a writer whose questions cannot be told answered)."""
    from modules.flow_gate.services.ai_invoke.finalize import open_questions_raised_by_run
    try:
        return bool(open_questions_raised_by_run(flow_id, ai_run_id))
    except Exception:  # noqa: BLE001 — unreadable: still waiting, never a failure nor a retry
        log.warning("graph writer question read failed flow=%s run=%s", flow_id, ai_run_id, exc_info=True)
        return True


def instruction_continuation_due(row, asker_ai_run_id, answered=True):
    """0111 T-A1: the instruction stage may go on from ``asker_ai_run_id``'s answers — the node's active attempt has no
    requested / running execution and its latest execution is ``asker_ai_run_id``'s instruction write, and (``answered``)
    every question it asked is answered. So the same answers told twice (a person's answer and the responder's end)
    continue once, an attempt opened again since continues nothing, and an earlier run of the stage answered again after
    a later one was requested (its instruction written again, that one perhaps asking in turn) continues nothing: never
    its approval, review or retry over the later run's document or questions. Judged inside the continuation's hold
    (``review_stage._answered_hold``) and the instruction retry's guard (``node_dispatch.retry_instruction_stage``);
    ``answered`` False: the wait's own read (``question_wait._waiting``)."""
    if not asker_ai_run_id or not get_store().table_exists("graph_attempt_runs"):
        return False
    attempt = run_store.active_attempt_id(row["run_id"], row["node_key"])
    if not attempt:
        return False
    if run_store._one("SELECT 1 AS present FROM graph_attempt_runs WHERE attempt_id = ? "
                      "AND status IN ('requested', 'running') LIMIT 1", [attempt]):
        return False
    latest = run_store._one("SELECT * FROM graph_attempt_runs WHERE attempt_id = ? "
                            "ORDER BY created_at DESC, exec_id DESC LIMIT 1", [attempt])
    if not latest or latest["kind"] != "write" or latest.get("ai_run_id") != asker_ai_run_id:
        return False
    if (json.loads(latest["request_json"]).get("job") or {}).get("stage") != "instruction":
        return False
    if not answered:
        return True
    graph = run_store.run_by_id(row["run_id"]) or {}
    return bool(graph.get("group_id")) and not _asked_open(graph["group_id"], asker_ai_run_id)


def settle(flow_id):
    from flowgate.card_settings.legacy_adapter import settle_graph_run
    settle_graph_run(flow_id)


def _time_machine():
    from flowgate.graph_time_machine import operation
    return operation


def _refuse_time_machine(flow_id, attempt_id, operation):
    """T#13, inside the step's guard: while a Graph Time Machine operation of the Flow is unfinished no execution is
    requested — not an opened node's write (a person's approval opening the next node of the idle run), not a person's
    explicit review / rework (which would take the idle run back to running under the operation). Recorded as a fence
    refusal once the guard rolled back; the person's request answers 409 ``graph_attempt_inactive``."""
    if _time_machine().busy(flow_id):
        raise fencing.refuse(attempt_id, operation, "time_machine_in_progress")


def result_refused(run, kind, payload):
    """T#7, inside the result binder's guard: a stopping run binds nothing, nor does a registration made with the
    token of an attempt that may not write any more. Recorded."""
    if fencing.run_refused(run, "result_" + kind, doc_id=payload.get("doc_id")):
        return True
    return kind == "inbox_registered" and fencing.token_refused(payload.get("token_id"), "result_" + kind)


def manual_git_blocker(run_id):
    """0066 D#1 §3-6(3): what keeps a person's manual merge off the Flow's worktree, or None. Read only: a
    requested/running execution, a run-end hand-off of a running / paused run, an approved result's merge into the
    Flow's branch (pending / integrating). An open manual or gate node alone (an attempt without an execution) and a
    failed integration waiting for a person do not block."""
    if ledger.working(run_id):
        return "graph_execution_active"
    run = run_store.run_by_id(run_id) or {}
    if run.get("status") in (run_store.RUNNING, run_store.PAUSED) and ledger.handing_off(run_id):
        return "graph_execution_handoff"
    if result_merge.in_progress(run_id):
        return "graph_integration_active"
    return None


def _settle():
    from flowgate.graph_run import settle
    return settle


_ACTIONS = {
    "manual_git_blocker": manual_git_blocker,
    "admission": scheduling.admission, "admit": scheduling.admit, "legacy_acquire": scheduling.legacy_acquire,
    # flowgate.v02test.0004 T#1: a refused start that only repeated a start of the same execution (no refusal record)
    "duplicate_start": scheduling.duplicate_start,
    "execution_wire": scheduling.wire, "opening": scheduling.opening, "can_open": scheduling.can_open,
    "prepare_workspace": workspace.prepare,
    "map_token": map_token, "validate_write": validate_write, "loop_state": loop_state,
    "settle": settle, "request_write": request_write, "request_hop": request_hop, "request_loop": request_loop,
    # flowgate.v02.0113 T#3: the review assignment's locked loop request and the explicit command's new attempt.
    "request_loop_locked": request_loop_locked, "reopen_attempt": reopen_attempt, "command_fence": command_fence,
    # flowgate.v02.0124 T#1: an explicit command requests a card's spent result review again (``run_service``).
    "rerequest_review": command.rerequest_review,
    "launch": launch, "finish": finish, "transition": transition, "heartbeat": heartbeat,
    "working": ledger.working, "start_failed": start_failed, "write_failure": write_failure,
    "instruction_continuation_due": instruction_continuation_due,       # 0111 T-A1
    "provider_failure_retry_due": provider_failure_retry_due,           # flowgate.v02.0121 T#2
    "write_failure_detail": write_failure_detail,                       # flowgate.v02.0121 T#4
    "report_refused": fencing.report_refused, "node_refused": fencing.node_refused, "result_refused": result_refused,
    "execution_attempt": fencing.execution_attempt, "handoff_end": handoff_end, "node_hold": node_hold,
    # T#9: an approved code node is done once its result is merged into the Flow's branch.
    "resume_approval": command.resume_approval, "continue_approval": command.continue_approval,
    # 0083 WP-3 (0061 T#2 개정): a person's decision on an idle run opens the next cards, the run woken first.
    "approval_wakes": command.approval_wakes, "wake_approval": command.wake_approval,
    "defer_done": result_merge.defer_done, "integration_waiting": result_merge.integration_waiting,
    "integration_wire": result_merge.integration_wire, "retry_integrations": result_merge.retry_integrations,
    "hold_command": result_merge.hold_command, "propagation_held": result_merge.propagation_held,
    # T#11: the Flow's held node keys, never opened (``run_apply.read_run_state`` -> ``RunState.held``).
    "held_nodes": hold.held,
    # 0085 T#3: gates waiting for a round split after an abandoned recovery, never opened either (not a Hold).
    "opening_held": lambda flow_id: _settle().opening_held(flow_id),
    # flowgate.v02.0119 T#3 (T-2): a run read's idle reason, a stored ``waiting_approval`` read as waiting_confirm /
    # needs_intervention (never converted).
    "idle_reason_read": lambda run: _settle().read_idle_reason(run),
    # T#13: an unfinished Graph Time Machine operation refuses a step / full command (``run_service``).
    "time_machine_refusal": lambda flow_id: _time_machine().require_no_operation(flow_id),
    # T#14: a chat's edit / edit-once source access while the Flow's run is locked (``token_service.issue``).
    "chat_source_refusal": editing.chat_source_refusal,
}
