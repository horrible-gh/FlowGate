"""Automatic continuation of answered card questions (0071 T6).

A new_attempt answer requests a bounded, fenced attempt replacement. If replacement
is unavailable or the restart budget is spent, ordinary review/approval continues.
Paused/stopped runs remain untouched. The old action-required wait is removed.
"""
from __future__ import annotations

import logging
from typing import Optional

from flowgate.card_settings import legacy_adapter as legacy

_log = logging.getLogger(__name__)

NEW_ATTEMPT = "new_attempt"
REQUIREMENTS = (NEW_ATTEMPT,)
REASON_PREFIX = "action_required:"
NEW_ATTEMPT_REASON = REASON_PREFIX + NEW_ATTEMPT
PHASES = ("instruction", "work", "no_result", "loop_wait")
# The next action told with the guard's record and the chat's answer outlook (the card's own sentence is the screen's).
NEXT_ACTION = {
    "open": "explicit_command_opens_new_attempt",       # a step / full command: the open card is opened as a new attempt
    "review": "stop_then_command",                      # a card in review: Stop, then the next command opens it anew
}


def requirement(asker_ai_run_id: Optional[str]) -> Optional[str]:
    """The action the latest answers to ``asker_ai_run_id``'s questions require before its step goes on —
    ``NEW_ATTEMPT`` when one selected an option that requires it — or None (none required, no asker, a failed read)."""
    if not asker_ai_run_id:
        return None
    try:
        found = legacy.answered_requirements(asker_ai_run_id)
    except Exception:  # noqa: BLE001 — an unreadable answer holds nothing (the step goes on as before)
        _log.warning("card action requirement read failed asker=%s", asker_ai_run_id, exc_info=True)
        return None
    return NEW_ATTEMPT if NEW_ATTEMPT in (found or ()) else None


def reason_for(run_id: Optional[str], node_key: Optional[str], asker_ai_run_id: Optional[str],
               attempt_id: Optional[str]) -> Optional[str]:
    """``action_required:<requirement>`` when ``asker_ai_run_id``'s answers require a new attempt and the node's active
    attempt is still ``attempt_id`` (the asker's): the action did not happen. None otherwise — nothing required, a new
    attempt opened since (the action happened: the old asker goes on with nothing anyway), or nothing to judge."""
    need = requirement(asker_ai_run_id)
    if need is None or not run_id or not node_key or not attempt_id:
        return None
    if legacy.active_attempt_id(run_id, node_key) != attempt_id:
        return None
    return REASON_PREFIX + need


def blocks(flow_id: str, run: dict, node_key: Optional[str], phase: str) -> bool:
    """True when replacement consumed this step or the run cannot resume.
    False lets the existing stage continue when retry is unavailable/exhausted."""
    if phase not in PHASES or not node_key or requirement(run.get("run_id")) is None:
        return False                            # nothing required: no other read (the common case)
    run_id = legacy.active_run_id(flow_id)
    attempt_id = legacy.attempt_of_ai_run(run)
    reason = reason_for(run_id, node_key, run.get("run_id"), attempt_id)
    if reason is None:
        return False
    return _automatic_retry(flow_id, run, node_key, run_id, attempt_id)


def _automatic_retry(flow_id, run, node_key, run_id, attempt_id):
    """0071 T6: answers continue automatically. Reuse fenced replacement; when it
    cannot replace (including a stored result), let the ordinary review/approval proceed.
    Count durable invalidations, so duplicate delivery and process restarts share the budget.
    """
    from flowgate.card_settings import fields, repository
    from flowgate.graph_run import execution, fencing
    from modules.flow_gate.graph_workflow.services import run_apply
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from modules.flow_gate.graph_workflow.store import run_store

    if not legacy.wake_graph_run(flow_id):
        return True                             # never advance a paused/stopped run
    jobs = []

    def replace():
        active = run_store.active_run(flow_id)
        if (active is None or active["run_id"] != run_id
                or active["status"] not in ("running", "idle")
                or active.get("idle_reason") == "stopped"):
            return True
        if run_store.active_attempt_id(run_id, node_key) != attempt_id:
            return True                         # duplicate/late answer: old step is finished
        stored = repository.values(flow_id, node_key)
        budget = stored.get("restart", fields.DEFAULT_RESTART_COUNT)
        used = run_store._one(
            "SELECT COUNT(*) AS n FROM graph_node_attempts WHERE run_id = ? AND node_key = ? "
            "AND invalidated_reason = 'answer_new_attempt'", [run_id, node_key])["n"]
        if budget != -1 and int(used) >= int(budget):
            return False
        row = next((r for r in run_store.node_runs(run_id) if r["node_key"] == node_key), None)
        if row is None or row["status"] != "open":
            return False
        ctx = run_apply.load_context(active, legacy.project_of(flow_id), run.get("issued_to"), jobs)
        execution.reopen_attempt(ctx, row, automatic=True)
        return run_store.active_attempt_id(run_id, node_key) != attempt_id

    try:
        # The gate supports replacement during this thread's own side effect.
        # Wait for other readers; never silently skip retry under a shared hold.
        with fencing.exclusive(flow_id, own_ok=True):
            replaced = with_flow_guard(flow_id, replace)
    except Exception:
        _log.warning("automatic answer retry unavailable flow=%s node=%s", flow_id, node_key, exc_info=True)
        return False
    for job in jobs:                             # committed replacement; AI starts outside locks
        job()
    return replaced


def node_reason(flow_id: str, row: dict) -> Optional[str]:
    """Read-only compatibility port: answers no longer require human intervention."""
    return None  # 0071 T6: retry or ordinary continuation, never an action-required wait.
