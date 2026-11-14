"""The common guard of a card step that goes on after its AI's questions are answered (flowgate.v02gui.0015 T3, NR
flowgate.v02gui.0015.0006 §6-3, B flowgate.v02gui.0015.0001 §3-4).

An answer alone never counts as the action it asked for. A question's option may say what has to happen before the card
goes on (``requires``, ``q_service``: today only ``new_attempt`` — the card's work cannot go on in the attempt that asked,
a person or the system must open a new one, e.g. on a newer base). When an answer selected such an option, the step
the asking run left is taken only once that action happened: the node's active attempt is no longer the asker's.

The three re-invocations the answers take (``question_wait._answered``) share this one judgement, so none of them goes
around it:

- ``instruction`` (``review_stage.continue_instruction_after_answers``: the instruction written again, or approved with
  the result stage's start),
- ``work`` (``review_stage._after_work``: the result's review start or its automatic approval),
- ``no_result`` (``question_wait._retry_result``: the result writer run again).

The run-end step of a run whose questions were answered before it ended (``_after_instruction`` / ``_after_work``) and the
run read's reconciliation of a lost step (``review_stage.reconcile``) take the same guard. The existing duplicate and late
answer guards (``*_continuation_due``, ``_answered_hold``, ``person_hold``) stay as they are; this one only comes first.

A step the guard keeps from going on calls no AI and approves nothing. The card is ``needs_intervention`` with the reason
``action_required:new_attempt`` (``node_reason``: read from the node's active attempt's latest execution and its answers,
nothing stored for it, so a restart loses nothing), and one ``action_required`` run event records which run, attempt and
phase it held. The next action: the person's explicit step / full command opens an open card again as a new attempt
(``integration._needs_dispatch``: ``reopen_new_attempt``, the existing replacement path); a card in review (its result
stays, it is never reopened) is moved by a Stop, after which the next command opens it with a new attempt. Once the node
has a new attempt the old asker is no attempt's latest any more and nothing of it goes on.

A failed read of the answers holds nothing (the step goes on as before) and is logged: the guard never stops a card on
an unreadable record.
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
PHASES = ("instruction", "work", "no_result")
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
    """The common guard: True when the step ``run`` left (``phase``: instruction / work / no_result) must not go on —
    its answers require a new attempt and the node's active attempt is still the one ``run`` worked for. The caller then
    calls no AI and approves nothing; the hold is recorded once (``action_required`` run event)."""
    if phase not in PHASES or not node_key or requirement(run.get("run_id")) is None:
        return False                            # nothing required: no other read (the common case)
    run_id = legacy.active_run_id(flow_id)
    attempt_id = legacy.attempt_of_ai_run(run)
    reason = reason_for(run_id, node_key, run.get("run_id"), attempt_id)
    if reason is None:
        return False
    _log.info("card step held flow=%s node=%s run=%s attempt=%s phase=%s: %s", flow_id, node_key, run.get("run_id"),
              attempt_id, phase, reason)
    legacy.record_action_required(flow_id, node_key, attempt_id, run.get("run_id"), phase, reason,
                                  NEXT_ACTION["review" if phase == "work" else "open"])
    return True


def node_reason(flow_id: str, row: dict) -> Optional[str]:
    """For a run read, the node judgement and an explicit command: ``action_required:<requirement>`` of the node run
    ``row`` (open or in review) whose active attempt's latest execution is a write that is not requested / running and
    whose AI run's answers require a new attempt — the card the guard holds — else None (a failed read: None)."""
    if row.get("status") not in ("open", "review") or not row.get("run_id") or not row.get("node_key"):
        return None
    try:
        latest = legacy.latest_execution(row["run_id"], row["node_key"])
    except Exception:  # noqa: BLE001 — the read goes on; nothing is held on an unreadable ledger
        _log.warning("card action guard read failed flow=%s node=%s", flow_id, row.get("node_key"), exc_info=True)
        return None
    if not latest or latest.get("kind") != "write" or latest.get("status") in ("requested", "running"):
        return None
    return reason_for(row["run_id"], row["node_key"], latest.get("ai_run_id"), latest.get("attempt_id"))
