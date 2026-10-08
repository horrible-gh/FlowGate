"""Run Coordinator (D0004 §2.2 실행 서비스, L0009 §2.6.5 / §2.6.7, P0008 §7 S5).

Start, stop and resume each run in one Flow guard; the checks come first and write nothing (the
order of L0009 §4.6 / §4.7 fixes which error wins). Policies come from the run's snapshot only.
Until D0004 §8 is answered every policy is ``undecided``, so a start is refused with
``run_policy_undecided`` naming the policies this revision needs (P0008 S5-1).
"""
from __future__ import annotations

import logging
from typing import Optional

from modules.flow_gate.graph_workflow.domain.frontier import RunEvent, resolve, resolve_node_event
from modules.flow_gate.graph_workflow.domain.run_policies import (
    PolicyUndecided,
    RULE_REF,
    policies_of,
    required_policies,
    undecided_for,
)
from modules.flow_gate.graph_workflow.store import definition_store, run_store

from . import node_dispatch, result_binder
from .engine_router import Ownership, read_ownership
from .errors import GraphApiError
from .flow_guard import with_flow_guard
from .graph_service import new_key, require_writable
from .policy_snapshot import check_active_runs, policy_set_for_run
from .run_apply import RunContext, apply_decision, dispatch_block, emit_node_state, emit_run_status, load_context
from .run_apply import read_run_state

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


def _missing(ctx: RunContext, entries: dict) -> list[dict]:
    root = ctx.root_document() if ctx.binding else None
    required = required_policies(ctx.revision, (ctx.binding or {}).get("node_key"),
                                 (root or {}).get("doc_review_status"))
    return undecided_for(required, entries)


def start_run(group: dict, user_id: str, revision_no: Optional[int]) -> dict:
    """``POST /flows/{flow_id}/runs`` -> 201 ``{"run": …}`` (P0008 S5-1~S5-3, L0009 §2.6.5)."""
    flow_id, project_id = group["group_id"], group.get("project_id")
    require_writable(flow_id)
    _restore_check_once()
    jobs: list = []

    def body() -> dict:
        own = read_ownership(flow_id)                                             # 1~3 담당
        if own.state != "graph":
            raise _ownership_error(flow_id, own)
        active = run_store.active_run(flow_id)                                    # 4 진행 중 실행
        if active is not None:
            raise GraphApiError(409, "run_already_active",
                                {"flow_id": flow_id, "run_id": active["run_id"], "status": active["status"]})
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
        if probe.binding and root is None:
            raise GraphApiError(409, "root_binding_lost", {"flow_id": flow_id, "node_key": probe.binding["node_key"],
                                                           "doc_id": probe.binding["doc_id"]})
        run = run_store.create_run(new_key(RUN_ID_PREFIX, set()), flow_id, number, user_id, snapshot,
                                   list(probe.node_rows))
        ctx = load_context(run, project_id, user_id, jobs)
        emit_run_status(ctx, None, "running", "user_start")
        try:
            if ctx.binding is not None:
                _root_bound_start(ctx, root, policies)
            apply_decision(ctx, resolve(ctx.revision, read_run_state(ctx), RunEvent("start"), policies))
        except PolicyUndecided as exc:                                            # E-21 (defence)
            raise _undecided(flow_id, number, [{"policy": exc.key, "rule_ref": RULE_REF[exc.key]}])
        return {"run": run_payload(ctx.run_id, with_nodes=True)}

    result = with_flow_guard(flow_id, body)
    run_jobs(jobs)
    return result


def _root_bound_start(ctx: RunContext, root: dict, policies: dict) -> None:
    """L0009 §2.6.5 step 2: the bound node takes its root document's state; no token."""
    key, doc_id = ctx.binding["node_key"], root["doc_id"]
    review = root.get("doc_review_status")
    to = "done" if review in ("approved", "wf_done") else "review"
    if run_store.set_root_bound(ctx.run_id, key, to, doc_id) != 1:
        raise AssertionError(f"root bound start missed node {key}")
    seq = emit_node_state(ctx, key, "pending", to, "root_bound_start", doc_id)
    if to == "done":
        run_store.insert_propagation(ctx.run_id, seq, key, "approved", "pending")
    if review == "rejected":
        event = RunEvent("review_rejected", key, doc_id=doc_id)
        apply_decision(ctx, resolve_node_event(ctx.revision, read_run_state(ctx), event, policies, defer=True))


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


def stop_run(group: dict, run_id: str, reason: Optional[str], user_id: str) -> dict:
    """``POST /runs/{run_id}/stop`` (P0008 S5-5, L0009 §2.6.7). Open nodes keep their tokens."""
    require_writable(group["group_id"])

    def body_of(run: dict, jobs: list) -> dict:
        if run["status"] != "running":
            raise GraphApiError(409, "run_not_running", {"run_id": run_id, "status": run["status"]})
        if run_store.set_run_status(run_id, "running", "stopped") != 1:
            raise AssertionError("stop missed its run")
        ctx = load_context(run, group.get("project_id"), user_id, jobs)
        emit_run_status(ctx, "running", "stopped", "user_stop", reason)
        return {"run": run_payload(run_id, with_nodes=False)}

    return _guarded_run(run_id, body_of)


def resume_run(group: dict, run_id: str, reason: Optional[str], user_id: str) -> dict:
    """``POST /runs/{run_id}/resume``: reconcile -> running -> re-dispatch -> drain (L0009 §2.6.7)."""
    require_writable(group["group_id"])
    _restore_check_once()

    def body_of(run: dict, jobs: list) -> dict:
        if run["status"] != "stopped":                                           # 1
            raise GraphApiError(409, "run_not_stopped", {"run_id": run_id, "status": run["status"]})
        own = read_ownership(run["group_id"])                                     # 2
        if own.state != "graph":
            raise _ownership_error(run["group_id"], own)
        entries = run_store.policy_entries(run_id)
        policies = policies_of(entries)                                          # 3 (E-33 -> 500)
        ctx = load_context(run, group.get("project_id"), user_id, jobs)
        missing = _missing(ctx, entries)                                         # 4 (defence)
        if missing:
            raise _undecided(run["group_id"], int(run["revision_no"]), missing)
        try:
            result_binder.reconcile_links(ctx, policies)                         # 5-1 while stopped
            if run_store.set_run_status(run_id, "stopped", "running") != 1:
                raise AssertionError("resume missed its run")
            emit_run_status(ctx, "stopped", "running", "user_resume", reason)
            for row in run_store.node_runs(run_id):                              # 5-3 re-dispatch
                if row["status"] == "open" and row.get("dispatch_token_issued") == 0:
                    dispatch = node_dispatch.dispatch(ctx, row["node_key"])
                    run_store.set_dispatch(run_id, row["node_key"], dispatch)
                    emit_node_state(ctx, row["node_key"], "open", "open", "redispatched",
                                    row.get("result_doc_id"), dispatch)
            apply_decision(ctx, resolve(ctx.revision, read_run_state(ctx), RunEvent("resume"), policies))
        except PolicyUndecided as exc:                                            # E-21 (defence)
            raise _undecided(run["group_id"], int(run["revision_no"]),
                             [{"policy": exc.key, "rule_ref": RULE_REF[exc.key]}])
        return {"run": run_payload(run_id, with_nodes=False)}

    return _guarded_run(run_id, body_of)


# ── Reads (P0008 S5-4) ───────────────────────────────────────────────────────

def run_payload(run_id: str, with_nodes: bool) -> Optional[dict]:
    run = run_store.run_by_id(run_id)
    if run is None:
        return None
    rows = run_store.node_runs(run_id)
    out = {
        "run_id": run["run_id"], "flow_id": run["group_id"], "revision_no": run["revision_no"],
        "status": run["status"], "started_at": run["started_at"], "started_by": run.get("started_by"),
        "stopped_at": run.get("stopped_at"), "finished_at": run.get("finished_at"),
        "open_node_keys": [r["node_key"] for r in rows if r["status"] == "open"],
        "last_event_seq": run["last_event_seq"],
    }
    if with_nodes:
        out["node_runs"] = [node_run_wire(r) for r in rows]
    return out


def node_run_wire(row: dict) -> dict:
    return {"node_key": row["node_key"], "status": row["status"], "opened_at": row.get("opened_at"),
            "finished_at": row.get("finished_at"), "result_doc_id": row.get("result_doc_id"),
            "root_bound": bool(row.get("root_bound")), "dispatch": dispatch_block(row)}


def current_run(group: dict) -> dict:
    flow_id = group["group_id"]
    result_binder.reconcile_if_needed(flow_id, group.get("project_id"))
    run = run_store.active_run(flow_id)
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
