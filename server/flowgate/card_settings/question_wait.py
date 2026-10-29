"""The card's question wait (flowgate.v02.0063): a Graph card whose AI asked questions goes on once they are answered.

A Graph card's AI run that registers questions (Q&A items, ``asker_ai_run_id``) and ends leaves the step that would
follow it until every question it asked has an answer (``finalize.open_questions_raised_by_run``):

- hold (``review_stage``): its instruction stage (the instruction's approval and the result stage), its result (the
  card's review or approval), its passed review loop (the approval) and its result writer that ended with no result
  (the next write) do not go on. Each question goes first to the card's review AI, then an independent enabled
  default AI, one at a time (``q_answer_invoke_service.dispatch_answer_run``, the [AI 답변 요청] run). Every candidate
  must differ from the question run's actual provider, including instruction/work runs and startup fallbacks.
  Without an independent responder (or known asker provider), ``mark_responder_failed`` leaves it open for a
  person's answer; no unchecked default dispatch occurs. A responder answers from documents and source and
  hands human decisions to a person through its escalate signal. A question
  has one claimed responder run; startup failures walk its independent enabled candidates once each (0111 T-A3),
  bounded by the active chain length. An exhausted, otherwise failed or handed-on question waits for a person's
  answer in the Q&A panel. The card of the document a question is on shows the [Q] mark (``run_service.question_nodes``).
- resume: once that run's questions are all answered — a person's answer (``on_question_answered``) or the responder
  run's end (``follow``) — the step it left goes on as it would have at the run's end. An idle Graph run is woken
  first (``legacy.wake_graph_run``); a paused, stopping or stopped one is not, and its card waits for a person.

Nothing is stored for it: what waits is read from the questions' asker run and the node's state, so a restart loses
nothing. A responder is never started while the Flow's AI is busy (the group lease, a Graph execution: the admission
would refuse it); every run end of the Flow looks again (``follow``).

flowgate.v02.0111 T#2 (T-A1, NR flowgate.v02.0111.0003 §2 A-1): a run that registered questions and ended before its
document was (fully) submitted ends ``exited`` with the stop code ``question_pending`` (``ended_asking``). That is a
question wait, not a failure: its instruction / work step waits as a clean end does (``_waiting``), its result writer is
one that ended with no result (``ended_without_result``), and once the questions are answered an instruction stage goes
on through ``review_stage.continue_instruction_after_answers`` (a written instruction: its approval and the result stage;
none, or the asking end: the instruction stage again on the same attempt) instead of judging the ended run again. Only
the run whose execution is still the attempt's latest waits so (``legacy.instruction_continuation_due``): an earlier run
of the stage whose answers are told again after a later one was requested goes on with nothing.

flowgate.v02.0111 T#3 (T-A2, NR flowgate.v02.0111.0003 §2 A-2): the wake commits the run running and leaves its guard, so a
settle in between (a run read's) may judge it idle before the step (0084 seq 8~11: the result stage refused ``run_idle``).
Every phase's step is therefore judged under ``legacy.person_hold`` with ``idle_ok`` — the instruction stage's
(``continue_instruction_after_answers``, T#2), a passed review's approval and a result writer's review or approval
(``review_stage._after_review`` / ``_after_work`` ``answered``), a writer with no result written again
(``_retry_result``) — whose own request takes an idle run back to running. The wake still decides whether the run goes on
(a paused, stopping or stopped run is not woken). A result writer's step goes on once: only while the writer's execution is
still its attempt's latest (``legacy.result_continuation_due``, judged here and again under the step's hold) — the same
answers told again after its review was requested (a person's and the responder's, or late after that review ended
without passing) request no second review.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from flowgate.card_settings import legacy_adapter as legacy
from flowgate.card_settings import repository

_log = logging.getLogger(__name__)

PASSED = "review_passed"
EXITED = "exited"
QUESTION_PENDING = "question_pending"
SOURCE_REVIEW_AI = "card_review_ai"

_lock = threading.RLock()


def ended_cleanly(run: dict) -> bool:
    return run.get("end_reason") == EXITED and not run.get("stop_code")


def ended_asking(run: dict) -> bool:
    """0111 T-A1: the run exited with questions it asked still open and its document not (fully) submitted (the stop
    code ``question_pending``): a question wait, never a failure."""
    return run.get("end_reason") == EXITED and run.get("stop_code") == QUESTION_PENDING


def ended_waiting(run: dict) -> bool:
    """The run's end leaves its step to wait for its questions: a clean end, or the asking end (0111 T-A1)."""
    return ended_cleanly(run) or ended_asking(run)


def ended_without_result(run: dict) -> bool:
    """The result writer exited on its own (``write_failure`` ``no_result`` / ``question_pending`` reads the same
    end)."""
    return run.get("end_reason") == EXITED and run.get("stop_code") in (None, "", "no_output_exhausted",
                                                                         QUESTION_PENDING)


def open_questions(flow_id: str, ai_run_id: Optional[str]) -> Optional[list[dict]]:
    """The questions ``ai_run_id`` asked in the Flow that are still unanswered, oldest first; None when unreadable."""
    try:
        return legacy.open_questions_of_ai_run(flow_id, ai_run_id)
    except Exception:  # noqa: BLE001 — unreadable: the card does not go on, nothing is dispatched
        _log.warning("card question read failed flow=%s run=%s", flow_id, ai_run_id, exc_info=True)
        return None


def hold(flow_id: str, run: dict, node: Optional[dict], *, asked_by_instruction: bool = False) -> bool:
    """At ``run``'s end: True when it asked questions still unanswered — the caller's step does not go on, and the
    first question is put to the card's responder."""
    items = open_questions(flow_id, run.get("run_id"))
    if items == []:
        return False
    _log.info("card step waits for answers flow=%s node=%s run=%s open=%s", flow_id, (node or {}).get("node_key"),
              run.get("run_id"), "unreadable" if items is None else len(items))
    if items and node is not None:
        with _lock:
            _dispatch(flow_id, node, run, items, asked_by_instruction)
    return True


def hold_without_result(flow_id: str, run: dict) -> None:
    """At the end of a node's result writer that registered no result of its own: its questions still unanswered go to
    the card's responder (the node stays open, its writer is run again once they are answered)."""
    if not ended_without_result(run):
        return
    nodes = legacy.nodes_of_ai_run(flow_id, run.get("run_id"))
    if len(nodes) == 1:
        hold(flow_id, run, nodes[0])


def _responder(flow_id: str, node: dict, asked_by_instruction: bool,
               asker_provider_id: Optional[str] = None) -> tuple[Optional[str], str]:
    """Review AI, then independent enabled defaults, for questions from every card stage.

    An instruction/run/assignee provider is eligible only as an enabled default that
    differs from the actual asker. Instruction-stage presence never overrides review AI.
    """
    stored = repository.values(flow_id, node["node_key"])
    project_id = legacy.project_of(flow_id)
    return legacy.independent_question_responder(project_id, asker_provider_id, [
        (stored.get("review_ai"), SOURCE_REVIEW_AI),
        (legacy.default_reviewer(project_id), "default"),
    ])


def _dispatch(flow_id: str, node: dict, run: dict, items: list[dict], asked_by_instruction: bool) -> bool:
    """Start the earliest unclaimed question's bounded independent responder chain.

    The answer service owns startup reassignment under the same item claim/lease.
    Exhaustion is marked failed on the question, never a Graph dispatch failure;
    this card stays in the T-A1 question wait and repeated follow-ups dispatch nothing.
    """
    target = next((item for item in items if not item.get("responder_state")), None)
    if target is None or not target.get("anchor_doc_id") or legacy.flow_ai_busy(flow_id):
        return False
    provider, source = _responder(
        flow_id, node, asked_by_instruction,
        target.get("asker_actual_provider_id") or run.get("provider_id"),
    )
    try:
        if provider is None:
            legacy.mark_question_responder_failed(target["id"], run.get("issued_to"))
            return False
        started = legacy.ask_responder(target["anchor_doc_id"], target["id"], run.get("issued_to") or "",
                                       provider, source)
    except Exception:  # noqa: BLE001 — a refusal is on the item (dispatch_answer_run); a person answers it
        _log.warning("card question responder not started flow=%s node=%s item=%s", flow_id, node.get("node_key"),
                     target.get("id"), exc_info=True)
        return False
    if started is None:
        return False
    _log.info("card question responder started flow=%s node=%s item=%s responder=%s (%s) run=%s", flow_id,
              node.get("node_key"), target.get("id"), provider, source, started.get("run_id"))
    return True


def _waiting(flow_id: str, run: dict) -> Optional[tuple[str, dict, Optional[dict]]]:
    """(phase, node, node run) of the card step ``run`` left at its end and that has not gone on since; else None."""
    run_id = run["run_id"]
    loop = run.get("document_review_loop")
    if loop:
        instruction = legacy.instruction_node_of_document(flow_id, run.get("doc_ref"))
        node = instruction[0] if instruction else legacy.node_of_result(flow_id, run.get("doc_ref"))
        if loop.get("stop_reason") != PASSED or node is None or legacy.review_loop_active(run.get("doc_ref")):
            return None
        return "review", node, None
    stage = legacy.instruction_node_of_ai_run(flow_id, run_id)
    if stage is not None:
        if not ended_waiting(run) or legacy.node_ai_run_active(flow_id, stage[0]["node_key"]):
            return None
        if not legacy.instruction_continuation_due(stage[1]["run_id"], stage[0]["node_key"], run_id, answered=False):
            return None                         # 0111 T-A1: an earlier run of the stage, a later one requested since
        return "instruction", stage[0], stage[1]
    found = legacy.node_of_ai_run(flow_id, run_id)
    if found is not None:
        node, doc_id = found
        if not ended_waiting(run) or legacy.review_loop_active(doc_id) \
                or legacy.node_ai_run_active(flow_id, node["node_key"]):
            return None
        run_row_id = legacy.active_run_id(flow_id)
        if run_row_id is None or not legacy.result_continuation_due(run_row_id, node["node_key"], run_id):
            return None                         # 0111 T#3: its review requested since (ended or not), or written again
        return "work", node, None
    found = legacy.no_result_node_of_ai_run(flow_id, run_id)
    if found is not None and ended_without_result(run):
        return "no_result", found[0], found[1]
    return None


def _answered(flow_id: str, asker_run_id: str) -> None:
    """``asker_run_id`` got an answer: the next of its questions goes to the responder, or — all answered — the step
    it left goes on."""
    from flowgate.card_settings import review_stage  # noqa: PLC0415 — review_stage imports this module

    run = legacy.ai_run_record(asker_run_id)
    if run is None or run.get("group_id") != flow_id:
        return
    waiting = _waiting(flow_id, run)
    if waiting is None:
        return
    phase, node, row = waiting
    items = open_questions(flow_id, asker_run_id)
    if items is None:
        return
    if items:
        # One at a time, each put to an AI once: a question with a responder still working, failed or handed to a
        # person stops the sequence until it is answered (v0.1 responder boundaries 4 / 5).
        if not any(item.get("responder_state") for item in items):
            _dispatch(flow_id, node, run, items, phase == "instruction")
        return
    if not legacy.wake_graph_run(flow_id):
        _log.info("card step not resumed flow=%s node=%s run=%s: the Graph run does not go on", flow_id,
                  node["node_key"], asker_run_id)
        return
    _log.info("card step resumed after answers flow=%s node=%s run=%s phase=%s", flow_id, node["node_key"],
              asker_run_id, phase)
    try:
        # 0111 T#3 (T-A2): every phase's step under ``legacy.person_hold`` with ``idle_ok`` — a run judged idle since the
        # wake (a run read's settle) takes it too, and its request takes the run back to running
        if phase == "review":
            review_stage._after_review(flow_id, run, answered=True)
        elif phase == "instruction":
            # 0111 T-A1: never the ended run judged again (an asking end would fail the stage): the step goes on
            review_stage.continue_instruction_after_answers(flow_id, run, node, row)
        elif phase == "work":
            review_stage._after_work(flow_id, run, answered=True)
        else:
            _retry_result(flow_id, run, node, row)
    finally:
        legacy.settle_graph_run(flow_id)


def _retry_result(flow_id: str, run: dict, node: dict, row: dict) -> None:
    """0111 T#3 (T-A2): the result writer that ended with no result writes again once its questions are answered, judged
    under ``legacy.person_hold`` (``retry_result_stage`` ``idle_ok``); its AI start runs after the hold."""
    attempt_id, key = legacy.attempt_of_ai_run(run), node["node_key"]
    jobs: list = []
    with legacy.person_hold(flow_id, row["run_id"], key, attempt_id, "result_retry") as owned:
        if not owned:
            return
        legacy.retry_result_stage(flow_id, row["run_id"], key, run.get("issued_to") or "", attempt_id, idle_ok=True,
                                  defer=jobs)
    for job in jobs:
        job()


def _dispatch_waiting(flow_id: str) -> None:
    """Every card step still waiting on questions no AI was asked yet (its responder could not start while the
    Flow's AI was busy): the earliest such question goes to its card's responder."""
    if legacy.flow_ai_busy(flow_id) or legacy.active_run_id(flow_id) is None:
        return
    askers: dict[str, list[dict]] = {}
    for item in open_questions_in_flow(flow_id):
        askers.setdefault(item["asker_ai_run_id"], []).append(item)
    for asker_run_id, items in askers.items():
        if any(item.get("responder_state") for item in items):
            continue                                    # under way, or a person's now (as in ``_answered``)
        run = legacy.ai_run_record(asker_run_id)
        waiting = _waiting(flow_id, run) if run is not None and run.get("group_id") == flow_id else None
        if waiting is not None and _dispatch(flow_id, waiting[1], run, items, waiting[0] == "instruction"):
            return


def open_questions_in_flow(flow_id: str) -> list[dict]:
    """Every unanswered AI-asked question of the Flow, oldest first; none on a failed read."""
    try:
        return legacy.open_ai_questions(flow_id)
    except Exception:  # noqa: BLE001 — nothing is dispatched on a failed read
        _log.warning("card question list failed flow=%s", flow_id, exc_info=True)
        return []


def follow(run: dict) -> None:
    """After any AI run of a Graph Flow ended (``review_stage.on_run_ended``): a question responder's end lets the
    card it answered for go on (or its next question go out), then questions still waiting for a responder go out."""
    flow_id = run.get("group_id")
    if not flow_id or not run.get("run_id"):
        return
    try:
        askers = legacy.askers_answered_by(run["run_id"])
        with _lock:
            for asker_run_id in sorted(askers):
                _answered(flow_id, asker_run_id)
            _dispatch_waiting(flow_id)
    except Exception:  # noqa: BLE001 — the run's own end is not failed by it
        _log.warning("card question follow-up failed flow=%s run=%s", flow_id, run.get("run_id"), exc_info=True)


def on_question_answered(answer: dict) -> None:
    """``ai_run_end_hooks.on_question_answered`` handler: a person answered an AI-asked question of a Graph Flow. The
    responder AI's own answer is followed at its run's end instead (``follow``), once it released the Flow's lease."""
    flow_id, asker_run_id = answer.get("group_id"), answer.get("asker_ai_run_id")
    if answer.get("author_kind") != "human" or not flow_id or not asker_run_id:
        return
    if not repository.available() or not legacy.graph_owned(flow_id):
        return
    try:
        with _lock:
            _answered(flow_id, asker_run_id)
    except Exception:  # noqa: BLE001 — the answer itself is committed
        _log.warning("card question resume failed flow=%s run=%s", flow_id, asker_run_id, exc_info=True)
