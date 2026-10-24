"""Graph Time Machine execution (0061 T#13): ``POST /runs/{run_id}/nodes/{node_key}/time-machine``.

One operation, in this order (L0007 §4.3: Git first, the DB after it; no Flow guard or DB transaction spans Git):

(a) the deciding Flow guard: the Flow is Graph-owned, T#12's scope and safety are judged again
    (``service.require_safe``; any reason -> 409 ``graph_time_machine_blocked``, nothing written), the plan is made
    (the integration commits that are the targets' own merges; one shared with an attempt outside the targets ->
    ``integration_commit_shared``) and the journal's ``started`` entry is written with it. From this commit the Flow
    refuses step / full commands, edits and other Time Machine requests (``require_no_operation``), and no integration
    of the Flow starts (``result_merge``) until the operation is finished;
(b) the Git step (``git_revert``: a slot released by gate cleanup is provisioned at its recorded real end HEAD
    before the normal cancel-session checks; the group's G protects the reverts) -> ``git_applied``; a revert that cannot be made
    safely is compensated and the operation ends ``blocked`` (409 ``graph_time_machine_blocked``, reason
    ``git_revert_blocked``, the branch as it was);
(c) the DB step (``rewind``) in one Flow guard under the Stop gate's exclusive hold, with the ``completed`` entry;
    then, outside it, the worktrees of the attempts it invalidated are removed (``workspace.cleanup``).

Git and the DB are not one transaction: a DB step that fails after Git (or a compensation or restore that could not
prove the starting state, a failed Git read, a Git step whose outcome is unknown) is recorded ``partial_failed`` (409
``graph_time_machine_partial_failed``) and the Flow stays refused; ``blocked`` (which frees the Flow) is recorded only
with ``git_revert``'s proof that nothing of the operation is left. At the server start (``recover``, before the Graph
execution recovery replays anything) every unfinished operation goes on from its journal: an interrupted Git step's
uncommitted state is put back by its marker, the Git step is resumed (the reverts it already made are found by their
trailer, checked against the ``git_applied`` entry, and not made again; with every one on the branch only the DB step
remains, with none it starts over) and the DB step follows. A Git session that cannot be opened at the start (G busy,
a dirty worktree that is not the operation's own) leaves the operation as it is for the next start.

The operation starts no run: the run stays idle, and the next step / full command opens the pending nodes with new
attempts. Response: ``{"time_machine": {operation, impact, reverts, what changed}, "run": <run with node_runs>}``.
"""
from __future__ import annotations

import logging
from typing import Optional

from modules.flow_gate.graph_workflow.api.graph_messages import time_machine_reasons
from modules.flow_gate.graph_workflow.services import run_apply
from modules.flow_gate.graph_workflow.services.engine_router import read_ownership
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.graph_workflow.services.graph_service import require_writable
from modules.flow_gate.graph_workflow.store import run_store

from flowgate.graph_run import fencing, workspace

from . import git_revert, journal, rewind, service
from .repository import incomplete_operations

log = logging.getLogger(__name__)

IN_PROGRESS, PARTIAL_FAILED_CODE, BLOCKED_CODE = (
    "graph_time_machine_in_progress", "graph_time_machine_partial_failed", "graph_time_machine_blocked")


def _unfinished(flow_id: str) -> list[dict]:
    """The Flow's unfinished operations; none before migration 144 (no run event log, no operation)."""
    if not run_store.get_store().table_exists("graph_run_events"):
        return []
    return incomplete_operations(flow_id)


def require_no_operation(flow_id: str) -> None:
    """Inside a Flow guard: a Flow with an unfinished operation (started, Git applied or partially failed) refuses
    step / full commands and edits (409 ``graph_time_machine_in_progress``). ``busy`` is the same judgement for the
    paths that answer nobody: no execution is requested, admitted or replayed and no integration wakes or starts
    (``graph_run.execution``, ``scheduling``, ``result_merge``)."""
    operations = _unfinished(flow_id)
    if operations:
        raise GraphApiError(409, IN_PROGRESS, {"flow_id": flow_id, "operations": operations})


def busy(flow_id: str) -> bool:
    return bool(_unfinished(flow_id))


def _plan(flow_id: str, assessment: dict, locale: str) -> dict:
    impact = assessment["impact"]
    attempts = {a["attempt_id"]: a for a in impact["attempts"]}
    commits = []
    for c in impact["commits"]:
        a = attempts.get(c["attempt_id"]) or {}
        # Only the attempt's own merge is on the branch for it: a result equal to its input was never merged (T#10).
        if c["kind"] != "integration" or c["commit_sha"] in (a.get("result_commit"), a.get("input_commit")):
            continue
        commits.append({"attempt_id": c["attempt_id"], "node_key": c["node_key"], "commit_sha": c["commit_sha"],
                        "result_commit": a.get("result_commit")})
    shas = [c["commit_sha"] for c in commits]
    shared = [r for r in run_store._all(
        "SELECT a.attempt_id, a.node_key, a.integrated_commit FROM graph_node_attempts a "
        "JOIN graph_runs r ON r.run_id = a.run_id WHERE r.group_id = ? AND a.integrated_commit IN ("
        + ", ".join("?" for _ in shas) + ") ORDER BY a.attempt_id", [flow_id, *shas])
        if r["attempt_id"] not in attempts] if shas else []
    if shared:
        raise _blocked(assessment, [{"code": "integration_commit_shared", "attempts": shared}], locale)
    return {"node_key": assessment["node_key"], "node_keys": impact["node_keys"], "attempt_ids": sorted(attempts),
            "documents": impact["documents"], "commits": commits,
            "gate_decisions": impact.get("gate_decisions", [])}


def _blocked(assessment: dict, reasons: list[dict], locale: str, **extra) -> GraphApiError:
    return GraphApiError(409, BLOCKED_CODE, {**assessment, **extra, "allowed": False,
                                             "reasons": time_machine_reasons(reasons, locale)})


def _project(flow_id: str) -> Optional[str]:
    from modules.flow_gate.db import groups as db_groups
    return (db_groups.get_by_id(flow_id) or {}).get("project_id")


def _record(flow_id: str, run_id: str, operation_id: str, status: str, actor: Optional[str], **extra) -> None:
    def body() -> None:
        run = run_store.run_by_id(run_id)
        journal.emit(run_apply.load_context(run, _project(flow_id), actor), operation_id, status, **extra)
    with_flow_guard(flow_id, body)


def _record_safely(*args, **kwargs) -> bool:
    try:
        _record(*args, **kwargs)
        return True
    except Exception:  # noqa: BLE001 — the last entry stays unfinished: the Flow stays refused, the start resumes it
        log.exception("Graph Time Machine journal entry failed: %s", args[2:4])
        return False


def _finish(flow_id: str, run_id: str, operation_id: str, plan: dict, actor: Optional[str], *,
            resume: bool = False, recorded: Optional[list[dict]] = None) -> dict:
    """(b) and (c) of one recorded operation. Returns its outcome (``operation_status`` and what it did).
    ``recorded``: on a resume, the reverts of the journal's ``git_applied`` entry (checked against the branch)."""
    outcome = {"operation_id": operation_id, "flow_id": flow_id, "run_id": run_id, "node_key": plan["node_key"]}
    git = git_revert.apply(flow_id, operation_id, plan["commits"], resume=resume, recorded=recorded or [])
    if git["status"] == git_revert.DEFERRED:
        log.warning("Graph Time Machine recovery waits for the group's Git session flow=%s operation=%s: %s",
                    flow_id, operation_id, git.get("reason"))
        return {**outcome, "operation_status": None, "git": git}
    if git["status"] == git_revert.BLOCKED:
        _record_safely(flow_id, run_id, operation_id, journal.BLOCKED, actor, phase="git", git=git)
        return {**outcome, "operation_status": journal.BLOCKED, "git": git}
    if git["status"] != git_revert.OK:
        _record_safely(flow_id, run_id, operation_id, journal.PARTIAL_FAILED, actor, phase="git", git=git)
        return {**outcome, "operation_status": journal.PARTIAL_FAILED, "phase": "git", "git": git}
    reverts = git["reverts"]
    if plan["commits"]:
        _record_safely(flow_id, run_id, operation_id, journal.GIT_APPLIED, actor, reverts=reverts,
                       pre_head=git.get("pre_head"))
    held: dict = {}

    def body() -> None:
        run = run_store.run_by_id(run_id)
        if run is None or run["group_id"] != flow_id:
            raise rewind.RewindRefused("run_missing")
        ctx = run_apply.load_context(run, _project(flow_id), actor)
        held["result"] = rewind.rewind(ctx, plan, reverts)
        journal.emit(ctx, operation_id, journal.COMPLETED, node_key=plan["node_key"], reverts=reverts,
                     result=held["result"])

    try:
        with fencing.exclusive(flow_id):
            with_flow_guard(flow_id, body)
    except Exception as exc:  # noqa: BLE001 — rolled back: Git applied, the DB not
        error = {"code": exc.code, **exc.params} if isinstance(exc, rewind.RewindRefused) else \
            {"code": "db_error", "detail": f"{type(exc).__name__}: {exc}"[:500]}
        log.warning("Graph Time Machine DB step failed flow=%s operation=%s: %s", flow_id, operation_id, error,
                    exc_info=not isinstance(exc, rewind.RewindRefused))
        _record_safely(flow_id, run_id, operation_id, journal.PARTIAL_FAILED, actor, phase="db", error=error,
                       reverts=reverts)
        return {**outcome, "operation_status": journal.PARTIAL_FAILED, "phase": "db", "error": error,
                "reverts": reverts}
    for attempt_id in held["result"]["invalidated_attempt_ids"]:
        try:
            workspace.cleanup(attempt_id)      # outside every guard; a tree left behind goes at the next start
        except Exception:  # noqa: BLE001
            log.warning("Graph Time Machine worktree cleanup failed attempt=%s", attempt_id, exc_info=True)
    return {**outcome, "operation_status": journal.COMPLETED, "reverts": reverts, **held["result"]}


def execute(group: dict, run_id: str, node_key: str, user_id: str, locale: str = "ko") -> dict:
    flow_id = group["group_id"]
    require_writable(flow_id)
    operation_id = journal.new_operation_id()

    def begin() -> tuple[dict, dict]:
        run = run_store.run_by_id(run_id)
        if run is None or run["group_id"] != flow_id:
            raise GraphApiError(404, "run_not_found", {"run_id": run_id})
        own = read_ownership(flow_id)
        if own.state != "graph":
            from modules.flow_gate.graph_workflow.services.run_service import _ownership_error
            raise _ownership_error(flow_id, own)
        assessment = service.require_safe(flow_id, node_key, locale)
        if assessment["run_id"] != run_id:
            raise _blocked(assessment, [{"code": "run_missing", "run_id": run_id}], locale)
        plan = _plan(flow_id, assessment, locale)
        plan["operation_id"] = operation_id
        ctx = run_apply.load_context(run, group.get("project_id"), user_id)
        journal.emit(ctx, operation_id, journal.STARTED, node_key=node_key, plan=plan)
        return assessment, plan

    assessment, plan = with_flow_guard(flow_id, begin)
    outcome = _finish(flow_id, run_id, operation_id, plan, user_id)
    status = outcome["operation_status"]
    if status == journal.BLOCKED:
        git = outcome["git"]
        raise _blocked(assessment, [{"code": "git_revert_blocked", **{k: git.get(k) for k in (
            "reason", "sub", "commit_sha", "compensated")}}], locale,
            operation_id=operation_id, operation_status=status)
    if status != journal.COMPLETED:
        raise GraphApiError(409, PARTIAL_FAILED_CODE, {**{k: v for k, v in outcome.items() if k != "git"},
                                                       "git": outcome.get("git"), "impact": assessment["impact"]})
    from modules.flow_gate.graph_workflow.services.run_service import run_payload
    return {"time_machine": {**outcome, "impact": assessment["impact"]},
            "run": run_payload(run_id, with_nodes=True)}


def recover() -> None:
    """Server start, before the Graph execution recovery (which replays nothing of a Flow still unfinished here):
    every unfinished operation goes on from its journal."""
    for flow_id, op in journal.unfinished():
        try:
            items = journal.entries(op["run_id"], op["operation_id"])
            started = journal.first(items, journal.STARTED)
            plan = (started or {}).get("plan")
            if not isinstance(plan, dict) or not isinstance(plan.get("commits"), list):
                log.error("Graph Time Machine operation %s has no plan; it stays unfinished", op["operation_id"])
                continue
            applied = journal.last(items, journal.GIT_APPLIED) or {}
            recorded = [r for r in applied.get("reverts") or () if isinstance(r, dict)]
            outcome = _finish(flow_id, op["run_id"], op["operation_id"], plan, None, resume=True,
                              recorded=recorded)
            log.info("Graph Time Machine recovery flow=%s operation=%s -> %s", flow_id, op["operation_id"],
                     outcome.get("operation_status"))
        except Exception:  # noqa: BLE001
            log.exception("Graph Time Machine recovery failed for %s", op.get("operation_id"))
