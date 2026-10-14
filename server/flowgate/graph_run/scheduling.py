"""0061 T#6 execution capacity and deterministic refill.

Existing requests start by (created_at, exec_id). New nodes use Kahn topological
order, choosing the smallest node_key at every tie. Only running executions
occupy slots; requested executions reserve room against opening more nodes.
Step refill intersects the command's saved targets with current candidates.
Paused runs start existing requests only; idle/stopping runs start nothing.
"""
from __future__ import annotations

import heapq
from contextvars import ContextVar

_admission_owner = ContextVar("graph_admission_owner", default=None)
from fastapi import HTTPException
from modules.flow_gate.db import groups, group_ai_leases
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard

DEFAULT_LIMIT = 4


def limit(flow_id):
    return int((groups.get_by_id(flow_id) or {}).get("graph_execution_limit") or DEFAULT_LIMIT)


def counts(run_id, attempt_id=None):
    sql = "SELECT status, COUNT(*) AS n FROM graph_attempt_runs WHERE run_id = ?"
    params = [run_id]
    if attempt_id is not None:
        sql += " AND attempt_id = ?"
        params.append(attempt_id)
    rows = run_store._all(sql + " AND status IN ('running', 'requested') GROUP BY status", params)
    found = {r["status"]: int(r["n"]) for r in rows}
    return {"running_execution_count": found.get("running", 0),
            "requested_execution_count": found.get("requested", 0)}


def wire(run_id, flow_id, attempt_id=None):
    return {**counts(run_id, attempt_id), "execution_limit": limit(flow_id)}


def requests(run_id):
    return run_store._all("SELECT * FROM graph_attempt_runs WHERE run_id = ? AND status = 'requested' "
                          "ORDER BY created_at, exec_id", [run_id])


def available(run_id, flow_id, reserve=False):
    n = counts(run_id)
    return max(0, limit(flow_id) - n["running_execution_count"]
               - (n["requested_execution_count"] if reserve else 0))


def ordered(revision, keys):
    pending = set(revision.node_keys())
    successors = {k: set() for k in pending}
    degree = {k: 0 for k in pending}
    for edge in revision.live_edges():
        if edge.source in pending and edge.target in pending and edge.target not in successors[edge.source]:
            successors[edge.source].add(edge.target)
            degree[edge.target] += 1
    ready = [k for k in pending if degree[k] == 0]
    heapq.heapify(ready)
    result = []
    wanted = set(keys)
    while ready:
        key = heapq.heappop(ready)
        pending.remove(key)
        if key in wanted:
            result.append(key)
        for target in sorted(successors[key]):
            degree[target] -= 1
            if degree[target] == 0:
                heapq.heappush(ready, target)
    if pending:
        raise ValueError("cyclic Graph cannot be scheduled")
    return result


def opening(ctx, keys):
    if ctx.run["status"] != "running":
        return []
    from modules.flow_gate.graph_workflow.domain.frontier import command_candidates
    from modules.flow_gate.graph_workflow.services.run_apply import read_run_state
    candidates = set(command_candidates(ctx.revision, read_run_state(ctx)))
    return ordered(ctx.revision, set(keys) & candidates)


def can_open(ctx):
    return ctx.run["status"] == "running" and available(ctx.run_id, ctx.flow_id, reserve=True) > 0


def queue_requests(ctx):
    if ctx.run["status"] not in ("running", "paused"):
        return
    from flowgate.graph_run.execution import replay
    # Jobs execute after commit. Admission CAS suppresses duplicate queued callbacks.
    for row in requests(ctx.run_id)[:available(ctx.run_id, ctx.flow_id)]:
        if not row.get("ai_run_id"):
            ctx.jobs.append(lambda row=row: replay(row))


def busy(run_id=None):
    raise HTTPException(409, detail={"code": "run_in_progress", "message": "An AI run is already in progress.",
                                     "run_id": run_id})


def legacy_acquire(acquire, **kwargs):
    """Use the same durable Flow guard as Graph acquire, preserving the legacy lease implementation."""
    if not get_store().table_exists("graph_attempt_runs"):
        return acquire(**kwargs)
    def body():
        row = run_store._one("SELECT e.ai_run_id FROM graph_attempt_runs e JOIN graph_runs r "
                             "ON r.run_id = e.run_id WHERE r.group_id = ? "
                             "AND e.status IN ('requested', 'running') LIMIT 1", [kwargs["group_id"]])
        if row:
            busy(row.get("ai_run_id"))
        return acquire(**kwargs)
    return with_flow_guard(kwargs["group_id"], body)


def admit(exec_id, ai_run_id, flow_id, attempt_id=None):
    """Claim a requested launcher before token issuance, without occupying a process slot."""
    from flowgate.graph_run import execution_store as ledger
    def body():
        row = ledger.get(exec_id)
        if not row or not ledger.eligible(row):
            raise ledger.LeaseLost("execution is no longer eligible")
        graph = run_store.run_by_id(row["run_id"])
        if graph["group_id"] != flow_id or (attempt_id and attempt_id != row["attempt_id"]):
            raise ledger.LeaseLost("execution identity mismatch")
        from flowgate.graph_time_machine.operation import busy as time_machine_busy
        if time_machine_busy(flow_id):
            busy()                  # T#13: kept requested (``start_failed``) until the Time Machine operation ends
        active = group_ai_leases.get_active(flow_id)
        if active:
            busy(active.get("run_id"))
        if row["status"] != "requested" or row.get("ai_run_id"):
            busy(row.get("ai_run_id"))
        if exec_id not in {r["exec_id"] for r in requests(row["run_id"])[:available(row["run_id"], flow_id)]}:
            busy()
        changed = run_store._affected("UPDATE graph_attempt_runs SET ai_run_id = ? "
                                      "WHERE exec_id = ? AND status = 'requested' AND ai_run_id IS NULL",
                                      [ai_run_id, exec_id])
        if changed != 1:
            busy()
        _admission_owner.set((exec_id, ai_run_id, flow_id))
        return row["attempt_id"]
    return with_flow_guard(flow_id, body)


def acquire(exec_id, ai_run_id):
    from flowgate.graph_run import execution_store as ledger
    row = ledger.get(exec_id)
    if not row:
        raise ledger.LeaseLost("execution disappeared")
    graph = run_store.run_by_id(row["run_id"])
    def body():
        current = ledger.get(exec_id)
        if not current or current["status"] != "requested" or not ledger.eligible(current):
            raise ledger.LeaseLost("execution is no longer requested")
        if current.get("ai_run_id") not in (None, ai_run_id):
            raise ledger.LeaseLost("execution belongs to another launcher")
        if group_ai_leases.get_active(graph["group_id"]):
            return None
        slots = available(row["run_id"], graph["group_id"])
        if exec_id not in {r["exec_id"] for r in requests(row["run_id"])[:slots]}:
            return None
        return ledger.acquire(exec_id, ai_run_id)
    return with_flow_guard(graph["group_id"], body)


def admission(start):
    marker = _admission_owner.set(None)
    try:
        return start()
    except Exception:
        owner = _admission_owner.get()
        if owner:
            exec_id, ai_run_id, flow_id = owner
            def release():
                # Never roll back an acquired winner, nor somebody else's admission.
                return run_store._affected(
                    "UPDATE graph_attempt_runs SET ai_run_id = NULL WHERE exec_id = ? "
                    "AND ai_run_id = ? AND status = 'requested'", [exec_id, ai_run_id])
            with_flow_guard(flow_id, release)
        raise
    finally:
        _admission_owner.reset(marker)
