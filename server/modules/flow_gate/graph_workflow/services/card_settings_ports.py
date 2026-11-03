"""Card run settings ports (flowgate.v02.0053 T#2, D0006 §2.1 / §2.3 / §4.1).

The settings every Designer card (Node) keeps are a v0.2 feature: they live in the application
package ``flowgate.card_settings``. The Graph run reads them when it hands a node out, when it
binds a node's result and when it tells whether a node awaits approval, and ``modules.flow_gate``
never imports ``flowgate``, so this module offers
named ports the Graph calls and the composition root (``startup.py``) registers the handlers.

    uses_ai          whether the node uses AI (explicit assignment, or an unassigned runnable Designer card)
    run_settings     node dispatch, inside its Flow guard: what the node's AI start takes from its card
    result_type      result binding / reconciliation, inside its Flow guard: the document type the
                     node's result is (instruction omitted: the card's report), or None (the node's own).
                     The node row may carry its node run's card run stage as ``run_stage`` (flowgate.v02.0056
                     T#2): None there is a node run opened before migration 148, whose AI ran one stage
    ai_working       node approval state (``node_approval``): AI still works on a node in review —
                     the node's own AI run has not ended, or the card's AI review works on its result —
                     so the node is not waiting for a person's approval (flowgate.v02.0056 T#1)
    instruction_document   the instruction stage (flowgate.v02.0056 T#2), at its start and when its document
                     is registered: the card's instruction type and its instruction line's document
                     (``doc_type``, ``doc_id`` or None, ``written``: it has a body), or ``{}`` (no stage)
    approve_instruction    the instruction stage: approve the card's instruction automatically, without a
                     review (``flow_id, node, doc_id, actor_user_id`` -> True when approved); a refusal is
                     left to the card approval (False), the stage goes on
    node_running     a card title save, inside its Flow guard (0064 T-B4b, 0062 L0006 2.7 step 4): the card is
                     running — open in the active run, or in review while its AI review works — the same
                     judgement that locks the card settings
    person_result    result binding, inside its Flow guard before its settlement (flowgate.v02.0083 WP-4): a result
                     of the node was submitted, revised or put back in review (``mark_revised``); the card answers the
                     step that follows it after the commit (a person's work plan: its AI review or its automatic
                     approval) as a callable the binder runs with its post-commit jobs, or None
    approval_wait_reason   a run read (flowgate.v02.0105 T#2 B-2): why the automatic approval of the node run's result
                     was not made (the latest B-1 reason recorded for it), or None
    reconcile_stage  a run read and a Resume, outside every Flow guard (flowgate.v02.0105 T#2 B-3): the AI cards whose
                     run-end stage was lost are followed once (an automatic approval only as the run end makes it)
    intervention     the node judgement and a run read (flowgate.v02.0119 T#3, T-2): ``{"kind", "reason"}`` of a general
                     card in review that nothing completes — ``needs_intervention`` (its automatic rework ended without a
                     completion: a memo rework / re-review, a step / full command's re-review of a spent count
                     (``command_review``, flowgate.v02.0125 T#1) or the Time Machine moves it) or ``system_error`` (its
                     automatic completion path failed: retried by ``reconcile_stage``) — never a person's approval
    command_review   an explicit step / full command only, after its Flow guard and its ``reconcile_stage`` job
                     (flowgate.v02.0125 T#1, Q #544): the run's AI cards whose result review count was spent on a revision
                     no review answered yet are reviewed again (a new review assignment of the latest revision and its
                     loop request); never asked by a run read, a reselection, the reconciliation or a loop end

``run_settings`` answers a dict; every key is optional and an absent key keeps the existing default:

    provider_id         the AI that runs the node (else the node's assignee provider)
    step_timeout_sec    the run's time budget (else the AI invoke engine's single-run formula)
    restart_max         no-output restarts on the same AI (else none, as before)
    result_doc_type     the document type the AI writes (else the node's own type)
    instruction         the card runs an instruction stage first (flowgate.v02.0056 T#2):
                        ``{"doc_type": the instruction type, "provider_id": its AI or None (the assignee)}``
    settings            every setting of the card as stored (carried on the job for the record)

flowgate.v02.0113 T#3 (L-2): the stage assignments of a role card's attempt, kept in the v0.2 package:

    stage_dispatch   a node's opening, inside its run step's guard: the dispatch block when the card's attempt is an
                     assigned one, else None (the current dispatch)
    stage_result_start   the result stage's start, inside its guard: True when the assigned attempt took it
    stage_needs_dispatch   an explicit command's re-dispatch of an open node: ``dispatch`` / ``retry_same_attempt`` /
                     ``reopen_new_attempt`` / ``none``, or None (the current judgement)
    stage_retry      a same-attempt retry of a stage: None (the current provider), ``{}`` (no AI's stage: nothing is
                     written again) or ``{provider_id, assignment_ref, invocation_origin}``
    stage_view       a run read: the node run's ``wait_reason`` / ``stage_assignments`` / ``approval_wait_submission``
                     (P-1 §6), or ``{}`` (no handler: the keys are not added)
    stage_waits      the run's idle judgement of a node: True / False, or None (the current judgement)
    instruction_saved   after a commit: a document was saved, registered or written on its line (L-2 §2.7.1
                     ``CONTENT_SAVED`` / ``REGISTERED``); a person's instruction stage holding it is judged
    stage_failed     an AI start's failure record, inside its guard: the dispatch error of an assigned stage
                     (``ai_unavailable:<cause>``, its assignment failed), or None (the current ``ai_invoke_failed``)
    result_review_pending   the binder's reconciliation, inside its guard (0038-T T#3 A-4): the node's approved result
                     is a report approved before the node's attempt took it, and that attempt's own result review has
                     not passed it yet — the old approval completes nothing (True), else False (no handler: False)

* With no handler registered ``run_settings`` answers ``{}``, ``result_type`` None,
  ``ai_working`` False, ``instruction_document`` ``{}``, ``approve_instruction`` False and ``node_running``
  False, so a node starts and binds exactly as it did before (one stage); ``approval_wait_reason`` None and
  ``reconcile_stage`` does nothing; ``intervention`` answers ``needs_intervention`` with the latest recorded reason.
* A handler exception propagates: the callers run inside a run step's Flow guard or a run read.
* One handler per port. Registering again replaces it (startup may run more than once in a process).
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

CARDS_CREATED = "cards_created"                              # 0113 T#2: draft creation only
USES_AI = "uses_ai"
RUN_SETTINGS = "run_settings"
RESULT_TYPE = "result_type"
AI_WORKING = "ai_working"
INSTRUCTION_DOCUMENT = "instruction_document"
APPROVE_INSTRUCTION = "approve_instruction"
REVIEW_INSTRUCTION = "review_instruction"
NODE_RUNNING = "node_running"
PERSON_RESULT = "person_result"                                  # flowgate.v02.0083 WP-4
APPROVAL_WAIT_REASON = "approval_wait_reason"                    # flowgate.v02.0105 T#2 B-2
RECONCILE_STAGE = "reconcile_stage"                              # flowgate.v02.0105 T#2 B-3
STAGE_DISPATCH = "stage_dispatch"                                # flowgate.v02.0113 T#3 (L-2)
STAGE_RESULT_START = "stage_result_start"
STAGE_NEEDS_DISPATCH = "stage_needs_dispatch"
STAGE_RETRY = "stage_retry"
STAGE_VIEW = "stage_view"
STAGE_WAITS = "stage_waits"
STAGE_UNREADABLE = "unreadable"     # ``stage_waits``: the node's assignment could not be read (its error family)
INSTRUCTION_SAVED = "instruction_saved"
STAGE_FAILED = "stage_failed"
RESULT_REVIEW_PENDING = "result_review_pending"                  # 0038-T T#3 A-4
INTERVENTION = "intervention"                                    # flowgate.v02.0119 T#3 (T-2)
COMMAND_REVIEW = "command_review"                                # flowgate.v02.0125 T#1 (Q #544)
INTERVENTION_NEEDED = "needs_intervention"                       # the default kind (no handler, an unknown answer)
PORT_NAMES = (USES_AI, RUN_SETTINGS, RESULT_TYPE, AI_WORKING, INSTRUCTION_DOCUMENT, APPROVE_INSTRUCTION, REVIEW_INSTRUCTION, NODE_RUNNING,
              PERSON_RESULT, APPROVAL_WAIT_REASON, RECONCILE_STAGE, CARDS_CREATED, STAGE_DISPATCH, STAGE_RESULT_START,
              STAGE_NEEDS_DISPATCH, STAGE_RETRY, STAGE_VIEW, STAGE_WAITS, INSTRUCTION_SAVED, STAGE_FAILED,
              RESULT_REVIEW_PENDING, INTERVENTION, COMMAND_REVIEW)

_handlers: dict[str, Callable[..., Any]] = {}
_lock = threading.Lock()


def register(name: str, handler: Callable[..., Any]) -> None:
    if name not in PORT_NAMES:
        raise ValueError(f"unknown card settings port: {name}")
    if not callable(handler):
        raise TypeError(f"card settings port {name} handler is not callable")
    with _lock:
        _handlers[name] = handler


def unregister(name: str) -> None:
    with _lock:
        _handlers.pop(name, None)


def registered(name: str) -> bool:
    with _lock:
        return name in _handlers


def _handler(name: str) -> Optional[Callable[..., Any]]:
    with _lock:
        return _handlers.get(name)


def cards_created(group: dict, user_id: str, nodes: list[dict]) -> None:
    """Newly issued draft node keys, in the draft's guard. Unregistered: no action."""
    handler = _handler(CARDS_CREATED)
    if handler is not None:
        handler(group, user_id, nodes)


def uses_ai(flow_id: str, node: dict) -> bool:
    """One assignment judgement for dispatch, instruction/result binding and run-end processing.
    Without the v0.2 handler only an explicit AI assignment runs, as before."""
    handler = _handler(USES_AI)
    return bool(handler(flow_id, node)) if handler else node.get("assignee_kind") == "ai"


def run_settings(flow_id: str, node: dict) -> dict:
    """``node``: the run's node row (node_key, node_type, doc_type, assignee …)."""
    handler = _handler(RUN_SETTINGS)
    if handler is None:
        return {}
    return dict(handler(flow_id, node) or {})


def result_type(flow_id: str, node: dict) -> Optional[str]:
    """The type a result of ``node`` (the run's node row, with ``run_stage`` when the binder knows its node run)
    must have, or None: the node's own type."""
    handler = _handler(RESULT_TYPE)
    if handler is None:
        return None
    return handler(flow_id, node) or None


def ai_working(flow_id: str, node_key: str, doc_id: Optional[str]) -> bool:
    """AI still works on the node whose result in review is ``doc_id``: its own run or the card's review."""
    handler = _handler(AI_WORKING)
    if handler is None:
        return False
    return bool(handler(flow_id, node_key, doc_id))


def instruction_document(flow_id: str, node: dict) -> dict:
    """``node`` (the run's node row): ``{doc_type, doc_id, written}`` of its card's instruction, or ``{}``."""
    handler = _handler(INSTRUCTION_DOCUMENT)
    if handler is None:
        return {}
    return dict(handler(flow_id, node) or {})


def approve_instruction(flow_id: str, node: dict, doc_id: str, actor_user_id: str) -> bool:
    """Approve the card's instruction ``doc_id`` with no review (True when it is approved)."""
    handler = _handler(APPROVE_INSTRUCTION)
    if handler is None:
        return False
    return bool(handler(flow_id, node, doc_id, actor_user_id))


def node_running(flow_id: str, node_key: str) -> bool:
    """The card actually runs (flowgate.v02.0113 T#1: an AI execution of it requested or running, or its AI review works
    on its result — never merely open): its title and settings cannot change now."""
    handler = _handler(NODE_RUNNING)
    if handler is None:
        return False
    return bool(handler(flow_id, node_key))


def review_instruction(flow_id: str, node: dict, doc_id: str, actor: str, run_id: str, attempt_id=None) -> bool:
    """True means instruction review owns the handoff (including a refused/failed review start)."""
    handler = _handler(REVIEW_INSTRUCTION)
    return bool(handler(flow_id, node, doc_id, actor, run_id, attempt_id)) if handler else False


def person_result(flow_id: str, node: dict, doc_id: str) -> Optional[Callable[[], None]]:
    """``node`` (the run's node row) has ``doc_id`` as its result just submitted, revised or put back in review: the
    card's step after the commit, or None (no handler: None, the result waits for a person's approval as before)."""
    handler = _handler(PERSON_RESULT)
    if handler is None:
        return None
    job = handler(flow_id, node, doc_id)
    return job if callable(job) else None


def approval_wait_reason(flow_id: str, row: dict) -> Optional[str]:
    """Why the automatic approval of the node run ``row``'s result was not made, or None (no handler: None)."""
    handler = _handler(APPROVAL_WAIT_REASON)
    if handler is None:
        return None
    reason = handler(flow_id, row)
    return reason if isinstance(reason, str) and reason else None


def reconcile_stage(flow_id: str) -> None:
    """The Flow's AI cards whose run-end stage was lost are followed once (no handler: nothing)."""
    handler = _handler(RECONCILE_STAGE)
    if handler is not None:
        handler(flow_id)


def command_review(flow_id: str, run_id: str) -> None:
    """flowgate.v02.0125 T#1 (Q #544): the person's explicit step / full command (``run_service._go_on`` with a mode,
    after its Flow guard and its ``reconcile_stage`` job) asks again for the result review of the run's AI cards whose
    review count was spent on a revision no review answered yet (no handler: nothing). Only that command asks it: a run
    read, F5, a card's reselection, the reconciliation and a review loop's end never do."""
    handler = _handler(COMMAND_REVIEW)
    if handler is not None:
        handler(flow_id, run_id)


def intervention(flow_id: str, row: dict) -> dict:
    """flowgate.v02.0119 T#3 (T-2): ``{"kind": needs_intervention | system_error, "reason": …}`` of the node run ``row``,
    a general card in review that nothing completes (``node_approval.intervention`` judged that). No handler:
    ``needs_intervention`` with the latest recorded reason (``approval_wait_reason``) — never a person's approval."""
    handler = _handler(INTERVENTION)
    if handler is None:
        return {"kind": INTERVENTION_NEEDED, "reason": approval_wait_reason(flow_id, row)}
    found = handler(flow_id, row)
    return dict(found) if isinstance(found, dict) else {"kind": INTERVENTION_NEEDED, "reason": None}


# ── stage assignments (flowgate.v02.0113 T#3) ────────────────────────────────

def stage_dispatch(ctx, node: dict) -> Optional[dict]:
    """The opening of ``node`` for an assigned attempt (its dispatch block), or None: the current dispatch."""
    handler = _handler(STAGE_DISPATCH)
    return handler(ctx, node) if handler else None


def stage_result_start(ctx, node: dict, row: dict, instruction_doc_id: Optional[str], attempt_id: Optional[str]) -> bool:
    handler = _handler(STAGE_RESULT_START)
    return bool(handler(ctx, node, row, instruction_doc_id, attempt_id)) if handler else False


def stage_needs_dispatch(ctx, row: dict) -> Optional[str]:
    handler = _handler(STAGE_NEEDS_DISPATCH)
    return handler(ctx, row) if handler else None


def stage_retry(run_id: str, node_key: str, attempt_id: Optional[str], stage: str) -> Optional[dict]:
    handler = _handler(STAGE_RETRY)
    return handler(run_id, node_key, attempt_id, stage) if handler else None


def stage_view(flow_id: str, row: dict, attempt_id: Optional[str], approval_wait_reason: Optional[str]) -> dict:
    handler = _handler(STAGE_VIEW)
    return dict(handler(flow_id, row, attempt_id, approval_wait_reason) or {}) if handler else {}


def stage_waits(flow_id: str, row: dict):
    """L-2 §2.12: True / False (an assigned attempt's person wait or not), ``STAGE_UNREADABLE`` (its assignment could
    not be read: no person's wait, the node's error family), None (the current judgement)."""
    handler = _handler(STAGE_WAITS)
    return handler(flow_id, row) if handler else None


def stage_failed(job: dict) -> Optional[str]:
    handler = _handler(STAGE_FAILED)
    return handler(job) if handler else None


def result_review_pending(flow_id: str, run_id: str, node_key: str, doc_id: str) -> bool:
    """0038-T T#3 A-4: the node's approved result waits for its attempt's own review (no handler: False)."""
    handler = _handler(RESULT_REVIEW_PENDING)
    return bool(handler(flow_id, run_id, node_key, doc_id)) if handler else False


def instruction_saved(flow_id: Optional[str], doc_id: Optional[str], node_key: Optional[str] = None,
                      token_id: Optional[str] = None) -> None:
    """After a commit; never raises."""
    handler = _handler(INSTRUCTION_SAVED)
    if handler is None or not flow_id or not doc_id:
        return
    try:
        handler(flow_id, doc_id, node_key, token_id)
    except Exception:  # noqa: BLE001 — the save stands; the next run read reconciles
        import logging  # noqa: PLC0415
        logging.getLogger(__name__).warning("instruction save contact failed flow=%s doc=%s", flow_id, doc_id,
                                            exc_info=True)
