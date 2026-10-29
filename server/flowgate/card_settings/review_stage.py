"""The card's review stage (flowgate.v02.0053 T#2): what follows a Graph node's AI result.

A Graph node's AI run writes the result and the result binder puts the node in review. The card then
decides what follows (``fields.after_result``), once that run has ended and released the Flow's group
AI lease (``ai_run_end_hooks``, registered by ``integration``):

- review count not 0: the card's review AI reviews the result with the v0.1 document review loop —
  the same engine the AI invoke dialog starts — with the card's review count and review basis, the
  rework going to the AI that runs the node (``fields.review_loop``). Every review hop, the first one
  included, is told that basis (``fields.review_basis_note``). The review AI is the card's, or
  the project's default reviewer when the card names none;
- when that review loop ends: passed -> the result is approved; any other end (hold, count spent,
  stalled, failure, stop) -> it waits for a person's approval of the card;
- no review -> the result is approved at once.

Both only follow a node's AI run that ended on its own (``ended_cleanly``: end reason ``exited``, no stop
code). A run that was cancelled, timed out, paused, ran out of providers or failed after its result was
bound starts no review and approves nothing: the result waits for a person's approval of the card. This is
the AI run's own end, not the Graph run's [다음 단계] stop — a node AI run that ends cleanly while the Graph
run is stopped is approved as ever (the node is done, only its propagation waits).

flowgate.v02.0056 T#1 (0002-CH turn 5~10): the after review setting (follow / human) is gone and what
follows is always the former follow; a card that stored human no longer stops here, as agreed.

An approval is the v0.1 approval (``legacy_adapter.approve``) followed by the same Graph contact a
person's approval reaches, so the node moves on exactly as if a person had approved it — while the run
is stopped the node is done and only its propagation waits (``frontier.resolve_node_event`` defer).
Only AI nodes read their card (as at dispatch). The card is locked while its review runs (``settings``).

``ai_working`` is the judgement the Graph asks (``card_settings_ports.ai_working``) whether a node in
review is still worked on by AI — its own AI run, or the card's review loop — and so not waiting for a
person's approval (the node's ``awaiting_approval`` and the card approval API share it).

flowgate.v02.0056 T#2 (0002-CH turn 5 / 10 O2): before its result, an AI card with its instruction used runs an
instruction stage (``node_dispatch``). When the AI run of that stage ends (``_after_instruction``):

- it ended on its own (``ended_cleanly``) and the card's instruction is written (``card_documents.lines.has_body``:
  the stage's recorded instruction, its instruction line's document, or an instruction the node's tokens
  registered) -> the instruction is approved at once, with no review — the card's review count is the
  result's alone — and the Graph starts the result stage with that instruction attached;
- otherwise (stopped, failed, nothing written) -> the stage failed: the node records it as an AI start
  failure (``ai_invoke_failed``) and the result stage does not start. (0111 T-A1: not for questions it asked — the
  ``question_pending`` end, or nothing written by a clean end that asked: they wait, see below.)

``approve_instruction`` is that automatic approval (also the Graph's ``card_settings_ports.approve_instruction``
handler, for an instruction found written when the stage starts): the card approval's own document approval
(``card_approval``: a written, never submitted instruction is submitted first), under the server's automatic approval
authority (flowgate.v02.0105 T#2 B-4; the run issuer as its actor) as the Flow's completion context, binding nothing. A refusal leaves the instruction to the card
approval; the result stage still starts.

flowgate.v02.0056 T#3: the card's review and rework hops open with the card's memos turned on for AI reading, as
its instruction and result stages do (``legacy_adapter.start_review_loop``, ``card_memo_hooks``). A single
per-document AI run (``action_scope`` edit, no review loop) on a card's document is no stage of the card: its end
starts no review and approves nothing (``on_run_ended`` follows only a node's instruction stage, a node's ``new``
run and a review loop). A node's ``new`` run reviews or approves only the result it registered itself
(``legacy.node_of_ai_run``): a result a person registered on the node while the run went on (the card line write)
is never reviewed or approved by the run's end; it waits for the card approval.

flowgate.v02.0061 T#2 (the Graph run's idle judgement, 0002-CH turn 29): the run-end port is called after the AI run
released its lease, so between that release and this stage (the review loop's start, the automatic approval) neither
the node's own AI run nor a review works on the result, and the Graph run would take the card for one awaiting a
person's approval and go idle. Right before the release (``on_run_ending``) a node's run this stage will follow — its
result run that ended on its own, or its review loop that passed — marks the node as still worked on
(``ai_working``) until ``on_run_ended`` has followed it; the mark is cleared there whatever happened (and expires
after ``STAGE_HANDOFF_TTL_SEC`` should that hook never come), and the Graph run is then judged again
(``legacy.settle_graph_run``): running while the review it started works, otherwise waiting for the approval.

flowgate.v02.0061 T#7: a Graph execution's end reaches this stage only when it was owned to its close (the finalize's
``graph_end_owned``), and until this stage is over the Graph keeps the run working (the execution's run-end hand-off),
so the instruction -> result and result -> review / approval steps of a run's last execution find it running. Every
step here is judged for the attempt the ended execution was made for (``legacy.attempt_of_ai_run``): the automatic
approvals (the instruction's with the result stage's start, the result's) run under the Stop gate's shared hold
(``legacy.attempt_hold``) and so commit before a Stop's ``stopping`` does or are refused after it; the review loop's
start is refused unless that attempt still works on the result (``execution.request_loop``). An old attempt's late
end — the node opened again after a Stop — approves, starts and records nothing of the new one (refused, recorded).

flowgate.v02.0063: a run that asked questions still unanswered when it ended leaves its step — the instruction's
approval and the result stage, the result's review or approval, a passed review's approval — until they are answered
(``question_wait``: the card's instruction AI answers first, the step goes on after the last answer); a result writer
that ended with no result and asked questions has them answered too and writes again. Every run end of the Flow is
followed by ``question_wait.follow`` (a responder's end, a question waiting for one).

flowgate.v02.0111 T#2 (T-A1, NR flowgate.v02.0111.0003 §2 A-1): an instruction stage's AI run that asked and ended before
its instruction was (fully) written (``exited`` / ``question_pending``) is no failure of the stage: it waits for its
answers (``question_wait.hold``) and records nothing. Once they are answered the stage goes on from
``continue_instruction_after_answers``, never from the ended run judged again: a written instruction of a clean end is
reviewed or approved and the result stage starts; otherwise the instruction AI writes again on the same attempt
(``node_dispatch.retry_instruction_stage``, once). Both under ``legacy.person_hold`` with ``idle_ok``, and only while the
answered run's execution is still the attempt's latest (``legacy.instruction_continuation_due``, judged under that hold):
an earlier run's answers told again after the stage was written again do nothing. Any other stop code fails the stage
as before.

flowgate.v02.0083 WP-4 (a new path, kept apart from the AI cards' one above): a work plan card a person writes follows its
result as an AI card does once the result binder bound it (``person_result``, the ``card_settings_ports`` port, inside the
binder's Flow guard; the step itself is a job after its commit, ``follow_person_result``). Every submission — the result,
a new revision of it (``revised``), the same document put back in review (``mark_revised``) — is followed:

- review count not 0 (any count, or until it passes: the same here): one AI review of that submission, a loop of its own
  with no rework AI (``review_count`` 1, ``rework_provider_id`` None). A pass is approved by ``_after_review`` as an AI
  card's is (``legacy.node_of_result`` takes a person's reviewed plan node); ``issues`` rejects the document and ends the
  loop (the engine's ``awaiting_author_revision`` stop) — no AI reworks it, the node stays in review and its author
  revises and submits again; a hold or any other end waits for a person's approval. The AI never repeats on its own;
- review 'none': the automatic approval at once (``legacy.approve``: the run issuer's ``document.approve`` resolved as
  the approve button resolves it, the v0.1 transition rules and the TR hook; without it nothing is tried and a person
  approves). Never the author's own authority.

A person's run / test card is not followed: a person approves it. The guards are the existing parts: the binder marks the
node as worked on (``_handoffs``, as 0061 T#2's run end does) before its own settlement, so the run is not judged waiting
for an approval until the job has started or refused the step; the job takes nothing on a stopped run or while a Time
Machine operation is unfinished (``legacy.person_stage_open``, ``wake_graph_run``'s conditions); it is judged for the
attempt the result was bound on (its start ``review_start``, its approval ``result_approval``; a pass is approved under the
review execution's own attempt, ``review_approval``) — an old attempt's step is refused and recorded; and a review starts
only when none works on the document, the document is not rejected (a submission not yet made) and no verdict answers its
current submission yet (``legacy.submission_judged``), so a bind told twice starts one loop. On an idle run the review is
requested as a person's explicit review request (Q439, ``legacy.explicit_request``), which takes the run back to running.
Both steps are judged under the Stop gate's shared hold, kept for the step (``legacy.person_hold``: the attempt, an idle run
taken too, and the run's Stop / Time Machine conditions judged again under the hold), so a Stop or a Time Machine rewind
between the job's start and its approval refuses it (recorded) instead of being crossed.

The submissions it follows: the binder's (a result, a new revision, ``mark_revised``, and the same document's new revision
an inbox edit registers — ``inbox_edited``), its reconciliation's (a submission bound after a lost contact), and one made
while a loop worked on an earlier one: when that loop ends, the node's current submission no verdict answers takes its
review (``_take_over_submission``; a pass of an older revision of the same document approves nothing).

flowgate.v02.0105 T#2 (NR flowgate.v02.0101.0003 §3.9, Q #511 o3). 0083 T#4 contract revised for the Graph AI cards: an AI
card's automatic approvals — its result's (review 'none', a passed review) and its instruction's — are made under the server's
automatic approval authority (``legacy.AUTOMATION_APPROVE_PERMISSIONS``: ``document.approve`` alone), no longer the run
issuer's ``document.approve``; the issuer stays the approval's actor, an empty issuer approves nothing (``issuer_missing``)
and every other condition of the approval (workflow head, body / test gate, revision, TR2 validation) stays. A person's
work plan result keeps the issuer's authority (0083 WP-4), and a person's card approval keeps its own ``document.approve``
check (403). Where an automatic approval is not made, why is added as a run event (B-1, ``legacy.record_approval_wait``):
``issuer_lacks_approve`` (a person's work plan only), ``issuer_missing``, ``approval_refused``, ``review_not_started``,
``review_not_passed:<stop reason>``, ``run_not_clean:<stop code / end reason>``, ``review_result_unknown``.

B-3: a run read reconciles a stage the run end never followed — its ``on_run_ended`` lost (a restart, the hand-off mark
expired, an end the Graph did not own) — once (``reconcile``): only for an AI node in review with its own result whose
writer ended on its own, no AI working on it and no B-1 reason recorded yet. It approves automatically only as the run end
does: the card's review 'none' as stored (no row, or the integer 0) with no review loop of the result, or the result's
latest loop passed for the node's current attempt and the current revision (``submission_passed``). A card that needs a
review and has no loop of the result yet takes its review loop once, never an approval. Anything else — a review never
started, not finished, not passed, or a setting / loop that cannot be told — approves nothing and records its reason once,
so the next read tries nothing again. The instruction stage follows the same rules on ``instruction_review_count``, on an
idle run too (``legacy.person_hold``): its review starts as an explicit request, its approval is followed by the result
stage's start (``start_result_stage`` ``idle_ok``), and an approved instruction whose result stage never started takes it.
"""
# T0017 supersedes the original no-review instruction policy described above:
# instruction_review_count/AI gate all configured card-owned instructions (including design),
# while zero preserves the existing approval behavior. See work_plan_deploy/CARD_STAGES.md.
from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Optional

from flowgate.card_approval import legacy_adapter as approvals
from flowgate.card_documents import lines as card_lines
from flowgate.card_settings import fields
from flowgate.card_settings import legacy_adapter as legacy
from flowgate.card_settings import question_wait, repository, settings

_log = logging.getLogger(__name__)

PASSED = "review_passed"
EXITED = "exited"
APPROVED = ("approved", "wf_done")
# flowgate.v02.0061 T#2: how long a node stays marked as followed when on_run_ended never comes.
STAGE_HANDOFF_TTL_SEC = 600

_handoffs: dict[tuple[str, str], tuple[str, float]] = {}      # (flow, node) -> (AI run id, deadline)
_handoff_lock = threading.Lock()


def ended_cleanly(run: dict) -> bool:
    """The AI run ended on its own: the worker's end reason is ``exited`` (not cancelled, timeout,
    user_paused, all_providers_failed, worker_error, …) and finalization gave it no stop code (cancelled,
    timeout, user_paused, providers_exhausted, provider_failed, question_pending, an inbox stop, …). The
    outcome alone does not tell it: a run that bound its result and was then stopped still counts it."""
    return run.get("end_reason") == EXITED and not run.get("stop_code")


def on_run_ending(run: dict) -> None:
    """``ai_run_end_hooks.on_run_ending`` handler (flowgate.v02.0061 T#2): a run of a Graph Flow is about to release
    its lease. A node's run this stage will follow marks the node as still worked on until ``on_run_ended``."""
    flow_id = run.get("group_id")
    if not flow_id or not run.get("run_id") or not repository.available() or not legacy.graph_owned(flow_id):
        return
    node_key = _followed_node(flow_id, run)
    if node_key is not None:
        with _handoff_lock:
            _handoffs[(flow_id, node_key)] = (run["run_id"], time.monotonic() + STAGE_HANDOFF_TTL_SEC)


def _followed_node(flow_id: str, run: dict) -> Optional[str]:
    """The node whose result this stage follows after ``run``: its review loop passed (the approval follows), or its
    result run ended on its own with its own result (the review or the approval follows)."""
    if run.get("document_review_loop"):
        loop = legacy.review_loop_row(run["run_id"]) or run.get("document_review_loop") or {}
        instruction = legacy.instruction_node_of_document(flow_id, run.get("doc_ref"))
        node = (instruction[0] if instruction else legacy.node_of_result(flow_id, run.get("doc_ref"))) if loop.get("stop_reason") == PASSED else None
        return node["node_key"] if node else None
    if run.get("mode") != "single" or run.get("action_scope") != "new" or not ended_cleanly(run):
        return None
    if legacy.instruction_node_of_ai_run(flow_id, run["run_id"]) is not None:
        return None                             # the instruction stage: the node stays open
    found = legacy.node_of_ai_run(flow_id, run["run_id"])
    return found[0]["node_key"] if found else None


def _handoff_pending(flow_id: str, node_key: str) -> bool:
    with _handoff_lock:
        mark = _handoffs.get((flow_id, node_key))
        if mark is not None and mark[1] <= time.monotonic():
            _handoffs.pop((flow_id, node_key), None)
            return False
    return mark is not None


def _clear_handoffs(run_id: Optional[str]) -> bool:
    with _handoff_lock:
        keys = [k for k, (owner, _deadline) in _handoffs.items() if owner == run_id]
        for key in keys:
            _handoffs.pop(key, None)
    return bool(keys)


def on_run_ended(run: dict) -> None:
    """``ai_run_end_hooks.on_run_ended`` handler: a run of a Graph Flow ended and released its lease."""
    flow_id = run.get("group_id")
    if not flow_id or not repository.available() or not legacy.graph_owned(flow_id):
        _clear_handoffs(run.get("run_id"))
        return
    try:
        if run.get("document_review_loop"):
            _after_review(flow_id, run)
        elif run.get("mode") == "single" and run.get("action_scope") in ("new", "edit"):
            stage = legacy.instruction_node_of_ai_run(flow_id, run["run_id"])
            if stage is not None:
                _after_instruction(flow_id, run, *stage)
            elif run.get("action_scope") == "new":
                _after_work(flow_id, run)
    finally:
        if _clear_handoffs(run.get("run_id")):
            legacy.settle_graph_run(flow_id)    # 0061 T#2: running while its review works, else the approval wait
        question_wait.follow(run)               # 0063: a responder's end, a question waiting for its responder


def _after_instruction(flow_id: str, run: dict, node: dict, row: dict) -> None:
    """The instruction stage's AI run ended: approve the written instruction and start the result stage, or
    record the stage's failure (flowgate.v02.0056 T#2)."""
    key, issuer = node["node_key"], run.get("issued_to") or ""
    # 0061 T#7: the attempt this AI run worked for; an AI run of an attempt the node no longer has (opened again
    # after a Stop) approves, records and starts nothing (refused and recorded by the Graph).
    attempt_id = legacy.attempt_of_ai_run(run)
    if question_wait.ended_asking(run):
        # 0111 T-A1: it asked and ended before its instruction was (fully) written — a question wait, not the stage's
        # failure. Answered already (none open): the step goes on at once, as after the last answer.
        if question_wait.hold(flow_id, run, node, asked_by_instruction=True):
            return
        continue_instruction_after_answers(flow_id, run, node, row)
        return
    if not ended_cleanly(run):
        _log.info("card instruction stage failed flow=%s node=%s run=%s: run ended %s/%s",
                  flow_id, key, run.get("run_id"), run.get("end_reason"), run.get("stop_code"))
        legacy.instruction_stage_failed(flow_id, row["run_id"], key, attempt_id)
        return
    if question_wait.hold(flow_id, run, node, asked_by_instruction=True):
        return                                  # 0063: its questions are answered first
    doc_id = written_instruction(flow_id, node, row)
    if doc_id is None:
        _log.info("card instruction stage failed flow=%s node=%s run=%s: no instruction written",
                  flow_id, key, run.get("run_id"))
        legacy.instruction_stage_failed(flow_id, row["run_id"], key, attempt_id)
        return
    if review_instruction(flow_id, node, doc_id, issuer, row["run_id"], attempt_id):
        return
    jobs: list = []
    with legacy.attempt_hold(row["run_id"], key, attempt_id, "instruction_approval") as owned:
        if not owned:
            return
        approve_instruction(flow_id, node, doc_id, issuer)
        legacy.start_result_stage(flow_id, row["run_id"], key, doc_id, issuer, attempt_id, defer=jobs)
    for job in jobs:                                # the result stage's AI start, after the hold
        job()


def continue_instruction_after_answers(flow_id: str, run: dict, node: dict, row: dict) -> None:
    """flowgate.v02.0111 T#2 (T-A1): the instruction stage's AI run ``run`` asked questions and every one is answered now
    (``question_wait._answered``, or none left open at its end): the stage goes on without judging the ended run again.

    - (a) it ended on its own and the card's instruction is written (``written_instruction``): the instruction's review,
      or its automatic approval and the result stage's start — as the reconciliation takes them (``person_hold``: an idle
      run taken too, ``start_result_stage`` ``idle_ok``), so a run judged idle meanwhile does not refuse it;
    - (b) nothing written, or it ended asking (``question_pending``: what it wrote never read the answers, so it is never
      approved): the instruction AI writes again on the same attempt (``retry_instruction_stage``, once per asking run:
      a second call for the same answers finds that run no longer the attempt's latest execution) — into the stage's
      instruction document when there is one (edit), else a new one. No new attempt.

    Both only while ``run`` is still the stage's run to go on from (``legacy.instruction_continuation_due``: its
    execution the attempt's latest, none requested / running since, its questions all answered), judged first and again
    under each step's own hold (``_answered_hold``): an earlier run's answers told again after a later run of the stage
    was requested — that one perhaps written partly and asking in turn — approve, review and request nothing, and the
    later run's questions are still waited on."""
    key, issuer = node["node_key"], run.get("issued_to") or ""
    attempt_id = legacy.attempt_of_ai_run(run)
    run_id, asker = row["run_id"], run.get("run_id")
    if not legacy.instruction_continuation_due(run_id, key, asker):
        _log.info("card instruction stage not continued flow=%s node=%s run=%s: not the stage's latest run, or asking",
                  flow_id, key, asker)
        return
    doc_id = written_instruction(flow_id, node, row) if ended_cleanly(run) else None
    if doc_id is None:
        _log.info("card instruction stage written again flow=%s node=%s run=%s: answered, %s",
                  flow_id, key, asker, run.get("stop_code") or "no instruction written")
        jobs: list = []
        with _answered_hold(flow_id, run_id, key, attempt_id, "instruction_retry", asker) as owned:
            if not owned:
                return
            legacy.retry_instruction_stage(flow_id, run_id, key, issuer, asker,
                                           instruction_target(flow_id, node, row), attempt_id, defer=jobs)
        for job in jobs:                            # the instruction stage's AI start, after the hold
            job()
        return
    if review_instruction(flow_id, node, doc_id, issuer, run_id, attempt_id,
                          hold=_answered_hold(flow_id, run_id, key, attempt_id, "answered_instruction_review_start",
                                              asker)):
        return
    _reconciled_result_stage(flow_id, run_id, key, node, doc_id, issuer, attempt_id,
                             hold=_answered_hold(flow_id, run_id, key, attempt_id, "answered_instruction_approval",
                                                 asker))


@contextlib.contextmanager
def _answered_hold(flow_id: str, run_id: str, key: str, attempt_id: Optional[str], operation: str,
                   asker_ai_run_id: Optional[str]):
    """0111 T-A1: ``legacy.person_hold`` (an idle run taken too) with the answered continuation judged again under it
    (``legacy.instruction_continuation_due``), so the step and its judgement share the Stop gate's hold. Yields whether
    the step may go on; a run no longer the stage's latest is no refusal of the attempt and records nothing."""
    with legacy.person_hold(flow_id, run_id, key, attempt_id, operation) as owned:
        yield bool(owned) and legacy.instruction_continuation_due(run_id, key, asker_ai_run_id)


def instruction_target(flow_id: str, node: dict, row: dict) -> Optional[str]:
    """0111 T-A1: the instruction document an instruction stage written again edits — the one the stage recorded, its
    instruction line's document, or the latest instruction the node's tokens registered (a partial submission) — with
    or without a body; None: a new one is written."""
    found = settings.instruction_document(flow_id, node)
    if not found.get("doc_type"):
        return None
    candidates = [row.get("instruction_doc_id"), found.get("doc_id")]
    candidates += [d["doc_id"] for d in legacy.registered_instructions(flow_id, row["run_id"], node["node_key"],
                                                                       found["doc_type"])]
    for doc_id in dict.fromkeys(c for c in candidates if c):
        if approvals.document(doc_id) is not None:
            return doc_id
    return None


def written_instruction(flow_id: str, node: dict, row: dict) -> Optional[str]:
    """The card's instruction the stage wrote, when it has a body: the one the stage recorded (the line's
    document it wrote into, or the one its tokens registered), its instruction line's document, or — after a
    lost registration contact — the latest instruction the node's tokens registered."""
    found = settings.instruction_document(flow_id, node)
    if not found.get("doc_type"):
        return None
    candidates = [row.get("instruction_doc_id"), found.get("doc_id")]
    candidates += [d["doc_id"] for d in legacy.registered_instructions(flow_id, row["run_id"], node["node_key"],
                                                                       found["doc_type"])]
    for doc_id in dict.fromkeys(c for c in candidates if c):
        if card_lines.has_body(approvals.document(doc_id)):
            return doc_id
    return None


def approve_instruction(flow_id: str, node: dict, doc_id: str, actor_user_id: str) -> bool:
    """Approve the card's instruction ``doc_id`` at once, with no review (``card_settings_ports.approve_instruction``):
    True when it is approved. flowgate.v02.0105 T#2 (B-4, Q #511 o3 — 0083 T#4 개정): under the server's automatic
    approval authority (``legacy.AUTOMATION_APPROVE_PERMISSIONS``), the issuer ``actor_user_id`` kept as the approval's
    actor. With no issuer (``issuer_missing``), or on any refusal (``approval_refused``), it is left to the card approval
    (False) and why is recorded (B-1)."""
    key = node.get("node_key")
    if not actor_user_id:
        _log.info("card instruction left to the card approval flow=%s node=%s doc=%s: no run issuer", flow_id, key, doc_id)
        legacy.record_approval_wait(flow_id, key, doc_id, legacy.ISSUER_MISSING, stage=fields.INSTRUCTION_STAGE)
        return False
    doc = approvals.document(doc_id)
    if doc is None:
        return False
    if doc.get("doc_review_status") in APPROVED:
        return True
    try:
        approvals.approve_document(flow_id, doc, actor_user_id, set(legacy.AUTOMATION_APPROVE_PERMISSIONS),
                                   legacy.LOCALE, system=True)
    except Exception:  # noqa: BLE001 — the instruction waits for the card approval
        _log.warning("card instruction approval refused flow=%s node=%s doc=%s", flow_id, key, doc_id, exc_info=True)
        legacy.record_approval_wait(flow_id, key, doc_id, legacy.APPROVAL_REFUSED, stage=fields.INSTRUCTION_STAGE)
        return False
    legacy.record_approval_authority(flow_id, key, doc_id, actor_user_id, stage=fields.INSTRUCTION_STAGE)
    return True


def _after_work(flow_id: str, run: dict) -> None:
    """The node's AI run ended: start the card's review, or approve at once."""
    found = legacy.node_of_ai_run(flow_id, run["run_id"])
    if found is None:
        question_wait.hold_without_result(flow_id, run)     # 0063: a writer with no result that asked questions
        return                                  # not a node's AI run, or no result of its own bound
    node, doc_id = found
    attempt_id = legacy.attempt_of_ai_run(run)                                          # 0061 T#7
    if not ended_cleanly(run):
        _log.info("card review stage skipped flow=%s node=%s run=%s: run ended %s/%s, awaiting approval",
                  flow_id, node["node_key"], run.get("run_id"), run.get("end_reason"), run.get("stop_code"))
        legacy.record_approval_wait(flow_id, node["node_key"], doc_id, run_not_clean(run), attempt_id=attempt_id)
        return                                  # stopped or failed: a person approves the card
    run_id = legacy.active_run_id(flow_id)
    if run_id is None:
        return
    if question_wait.hold(flow_id, run, node):
        return                                  # 0063: its questions are answered first
    stored = repository.values(flow_id, node["node_key"])
    step = fields.after_result(fields.card_kind(node.get("node_type"), node.get("doc_type")), stored)
    if step == fields.REVIEW:
        _start_result_review(flow_id, run_id, node, doc_id, stored, run.get("issued_to") or "", run.get("provider_id"),
                             attempt_id)
    elif step == fields.APPROVE:
        with legacy.attempt_hold(run_id, node["node_key"], attempt_id, "result_approval") as owned:
            if owned:
                legacy.approve(flow_id, doc_id, run.get("issued_to") or "", legacy.AUTOMATION,
                               node_key=node["node_key"], attempt_id=attempt_id)


def run_not_clean(run: dict) -> str:
    """B-1 ``run_not_clean:<stop code, else end reason>`` of an AI run that did not end on its own."""
    return legacy.RUN_NOT_CLEAN + str(run.get("stop_code") or run.get("end_reason") or "unknown")


def _start_result_review(flow_id: str, run_id: str, node: dict, doc_id: str, stored: dict, issuer: str,
                         provider_id: Optional[str], attempt_id: Optional[str], hold=None) -> None:
    """The card's review loop of its AI result, for ``attempt_id``. With no reviewer or rework AI, or a refused start,
    nothing reviews it and ``review_not_started`` is recorded (B-1). ``hold``: the context the start is judged in (the
    B-3 reconciliation's), else ``attempt_owns``."""
    key = node["node_key"]
    project_id = legacy.project_of(flow_id)
    reviewer = stored.get("review_ai") or legacy.default_reviewer(project_id)
    reworker = stored.get("run_ai") or node.get("assignee_provider_id") or provider_id
    if not reviewer or not reworker:
        _log.warning("card review not started flow=%s node=%s: no reviewer or rework AI", flow_id, key)
        legacy.record_approval_wait(flow_id, key, doc_id, legacy.REVIEW_NOT_STARTED, attempt_id=attempt_id)
        return
    loop = fields.review_loop(stored, reviewer=reviewer, reworker=reworker)
    criteria = loop["review_criteria"]

    def start():
        return legacy.start_review_loop(project_id, flow_id, doc_id, issuer, loop,
                                        lambda last: fields.review_basis_note(criteria, last), key, attempt_id=attempt_id)

    if hold is None:
        if not legacy.attempt_owns(run_id, key, attempt_id, "review_start"):
            return
        started = start()
    else:
        with hold as owned:
            if not owned:
                return
            with legacy.explicit_request():     # Q439: an idle run (waiting for the approval) takes it back to running
                started = start()
    if started is None:
        legacy.record_approval_wait(flow_id, key, doc_id, legacy.REVIEW_NOT_STARTED, attempt_id=attempt_id)


def _after_review(flow_id: str, run: dict) -> None:
    """A review loop on a node's result ended: a passed result is approved."""
    doc_id = run.get("doc_ref")
    instruction = legacy.instruction_node_of_document(flow_id, doc_id)
    if instruction is not None:
        node, row = instruction
        loop = legacy.review_loop_row(run["run_id"]) or run.get("document_review_loop") or {}
        if loop.get("stop_reason") != PASSED:
            legacy.record_approval_wait(flow_id, node["node_key"], doc_id, not_passed(loop),
                                        stage=fields.INSTRUCTION_STAGE)
            return
        if question_wait.hold(flow_id, run, node, asked_by_instruction=True):
            return
        jobs: list = []
        with legacy.attempt_hold(row["run_id"], node["node_key"], legacy.attempt_of_ai_run(run), "instruction_review_approval") as owned:
            if not owned:
                return
            approve_instruction(flow_id, node, doc_id, run.get("issued_to") or "")
            legacy.start_result_stage(flow_id, row["run_id"], node["node_key"], doc_id,
                                      run.get("issued_to") or "", legacy.attempt_of_ai_run(run), defer=jobs)
        for job in jobs:
            job()
        return
    node = legacy.node_of_result(flow_id, doc_id)
    if node is None:
        _take_over_submission(flow_id, run)     # 0083 WP-4: a person's newer submission made while it worked
        return
    loop = legacy.review_loop_row(run["run_id"]) or run.get("document_review_loop") or {}
    person = legacy.person_plan_reviewed(flow_id, node)
    attempt_id = legacy.attempt_of_ai_run(run)
    if loop.get("stop_reason") != PASSED:
        if not person:                          # B-1: an AI card's review that ended without passing (H1)
            legacy.record_approval_wait(flow_id, node["node_key"], doc_id, not_passed(loop), attempt_id=attempt_id)
        _take_over_submission(flow_id, run)     # 0083 WP-4: likewise (a person's revision made in place)
        return                                  # hold, count spent, stalled, failure: a person approves
    run_id = legacy.active_run_id(flow_id)
    if run_id is None:
        return
    if person and not legacy.submission_passed(doc_id):
        _take_over_submission(flow_id, run)     # 0083 WP-4: the pass answers an older revision; the current one waits
        return
    if question_wait.hold(flow_id, run, node):
        return                                  # 0063: its questions are answered first
    # 0061 T#7: the review execution's attempt must still work on the node; the approval commits under the Stop gate.
    # flowgate.v02.0105 T#2 (B-4): an AI card's under the system's authority, a person's work plan under the issuer's.
    with legacy.attempt_hold(run_id, node["node_key"], attempt_id, "review_approval") as owned:
        if owned:
            legacy.approve(flow_id, doc_id, run.get("issued_to") or "", None if person else legacy.AUTOMATION,
                           node_key=node["node_key"], attempt_id=attempt_id)


def not_passed(loop: dict) -> str:
    """B-1 ``review_not_passed:<stop reason>`` of an ended loop, or ``review_result_unknown`` when it tells none."""
    reason = loop.get("stop_reason")
    return legacy.REVIEW_NOT_PASSED + str(reason) if reason else legacy.REVIEW_RESULT_UNKNOWN


def review_running(_flow_id: str, _node_key: str, doc_id: Optional[str]) -> bool:
    """A review loop is still working on ``doc_id`` (the node's result)."""
    return bool(doc_id) and legacy.review_loop_active(doc_id)


def ai_working(flow_id: str, node_key: str, doc_id: Optional[str]) -> bool:
    """``card_settings_ports.ai_working`` handler: the node's own AI run has not ended yet (it still
    holds the Flow's group AI lease), the card's review loop works on its result ``doc_id``, or this stage is
    about to follow the node's run that just released the lease (flowgate.v02.0061 T#2, ``on_run_ending``)."""
    return (_handoff_pending(flow_id, node_key) or legacy.node_ai_run_active(flow_id, node_key)
            or review_running(flow_id, node_key, doc_id))


# ── a person's work plan (flowgate.v02.0083 WP-4) ───────────────────────────

PERSON_HANDOFF = "person:"                      # the hand-off mark of a person's result: "person:<its document>"


def person_result(flow_id: str, node: dict, doc_id: str):
    """``card_settings_ports.person_result`` handler (0083 WP-4), inside the result binder's Flow guard before its
    settlement: a result of ``node`` was submitted, revised or put back in review. A work plan card a person writes
    whose run may take the step marks the node as worked on and answers the job that follows it after the commit
    (``follow_person_result``, for the attempt bound now); anything else None (an AI card follows its AI run's end, a
    person's run / test card waits for a person's approval)."""
    key = node.get("node_key")
    if not key or not doc_id or not repository.available():
        return None
    if fields.card_kind(node.get("node_type"), node.get("doc_type")) != fields.PLAN or settings.uses_ai(flow_id, node):
        return None
    if fields.after_result(fields.PLAN, repository.values(flow_id, key)) is None:
        return None
    run = legacy.active_run(flow_id)
    if run is None or not legacy.person_stage_open(flow_id, run):
        return None                             # stopped, stopping, a Time Machine operation: a person decides
    return _person_job(flow_id, run["run_id"], key, doc_id, legacy.active_attempt_id(run["run_id"], key))


def _person_job(flow_id: str, run_id: str, node_key: str, doc_id: str, attempt_id: Optional[str]):
    """The node is marked as worked on (0061 T#2's ``_handoffs``, owner ``person:<doc>``) and the step that follows the
    submission ``doc_id`` for ``attempt_id`` is answered."""
    with _handoff_lock:
        _handoffs[(flow_id, node_key)] = (PERSON_HANDOFF + doc_id, time.monotonic() + STAGE_HANDOFF_TTL_SEC)
    return lambda: follow_person_result(flow_id, run_id, node_key, doc_id, attempt_id)


def _take_over_submission(flow_id: str, run: dict) -> None:
    """0083 WP-4: a review loop of a person's work plan ended (``_after_review``) while the plan was submitted again — a
    new revision, or the same document revised in place — which no loop took then (one loop at a time). That current
    submission now takes its own review through the same post-commit step (``follow_person_result``), for the attempt the
    ended review's execution was made for: a Stop, a Time Machine operation or an attempt invalidated since takes nothing
    over (the step's guards and ``legacy.person_hold``), and a submission a verdict already answers starts nothing."""
    attempt_id = legacy.attempt_of_ai_run(run)
    found = legacy.attempt_node(attempt_id)
    if found is None:
        return
    run_id, key = found
    node = legacy.node_of_key(flow_id, key)
    if node is None or not legacy.person_plan_reviewed(flow_id, node):
        return                                  # an AI card's loop: a person approves what it left
    active, row = legacy.active_run(flow_id), legacy.node_run(run_id, key)
    if active is None or active["run_id"] != run_id or row is None or row["status"] != legacy.REVIEW_NODE_STATUS:
        return
    doc_id = row.get("result_doc_id")
    if not doc_id or (approvals.document(doc_id) or {}).get("doc_review_status") in APPROVED:
        return
    if legacy.review_loop_active(doc_id) or legacy.submission_judged(doc_id):
        return                                  # another loop works on it, or a verdict answers it
    _person_job(flow_id, run_id, key, doc_id, attempt_id)()


def follow_person_result(flow_id: str, run_id: str, node_key: str, doc_id: str, attempt_id: Optional[str]) -> None:
    """After the binder's commit (0083 WP-4): the card's review of a person's submission starts, or its automatic
    approval is made; the node's mark is then cleared whatever happened and the run judged again (running while the
    review works, else waiting for a person's approval)."""
    try:
        _follow_person_result(flow_id, run_id, node_key, doc_id, attempt_id)
    except Exception:  # noqa: BLE001 — a post-commit step: the result stays in review for a person
        _log.warning("person work plan stage failed flow=%s node=%s doc=%s", flow_id, node_key, doc_id, exc_info=True)
    finally:
        if _clear_handoffs(PERSON_HANDOFF + doc_id):
            legacy.settle_graph_run(flow_id)


def _follow_person_result(flow_id: str, run_id: str, node_key: str, doc_id: str, attempt_id: Optional[str]) -> None:
    run = legacy.active_run(flow_id)
    if run is None or run["run_id"] != run_id or not legacy.person_stage_open(flow_id, run):
        return                                  # another run, stopped, stopping, a Time Machine operation
    row = legacy.node_run(run_id, node_key)
    node = legacy.node_of_key(flow_id, node_key)
    if row is None or node is None or row["status"] != legacy.REVIEW_NODE_STATUS or row.get("result_doc_id") != doc_id:
        return                                  # not (any more) the node's result in review
    doc = approvals.document(doc_id) or {}
    if doc.get("doc_review_status") in APPROVED:
        return
    stored = repository.values(flow_id, node_key)
    step = fields.after_result(fields.PLAN, stored)
    issuer = legacy.run_issuer(run)
    if step == fields.REVIEW:
        _start_person_review(flow_id, run, node_key, {**doc, "doc_id": doc_id}, stored, issuer, attempt_id)
    elif step == fields.APPROVE:
        # 0061 T#7: the attempt bound must still work on the node, on a run that may take the stage, judged under the
        # Stop gate's shared hold kept until the approval — its transition, TR hook and Graph contact — is over.
        with legacy.person_hold(flow_id, run_id, node_key, attempt_id, "result_approval") as owned:
            if owned:                           # the issuer's own authority, or nothing is tried (issuer_lacks_approve)
                legacy.approve(flow_id, doc_id, issuer, node_key=node_key, attempt_id=attempt_id)


def _start_person_review(flow_id: str, run: dict, node_key: str, doc: dict, stored: dict, issuer: str,
                         attempt_id: Optional[str]) -> None:
    """One AI review of a person's submission: a loop of its own, review count 1, no rework AI (0083 WP-4)."""
    doc_id = doc["doc_id"]
    if legacy.review_loop_active(doc_id):
        return                                  # a review works on it: one loop
    if doc.get("doc_review_status") == "rejected" or legacy.submission_judged(doc_id):
        return                                  # not submitted again yet, or this submission has its verdict
    project_id = legacy.project_of(flow_id)
    reviewer = stored.get("review_ai") or legacy.default_reviewer(project_id)
    if not reviewer:
        _log.warning("person work plan review not started flow=%s node=%s: no reviewer", flow_id, node_key)
        return
    loop = fields.review_loop({**stored, "review_count": 1}, reviewer=reviewer, reworker=None)
    criteria = loop["review_criteria"]
    # The attempt bound, the run and its Stop / Time Machine conditions judged under the Stop gate's shared hold, kept
    # while the review is requested (its own request judges the attempt again, ``execution.request_loop``).
    with legacy.person_hold(flow_id, run["run_id"], node_key, attempt_id, "review_start") as owned:
        if not owned:
            return
        with legacy.explicit_request():         # Q439: an idle run takes it and goes back to running
            legacy.start_review_loop(project_id, flow_id, doc_id, issuer, loop,
                                     lambda last: fields.review_basis_note(criteria, last), node_key,
                                     attempt_id=attempt_id)


def review_instruction(flow_id: str, node: dict, doc_id: str, actor: str, run_id: str, attempt_id=None,
                       hold=None) -> bool:
    """A configured instruction review gates the result; 0 retains existing automatic approval.

    Reuse the durable review/rework loop, with instruction-specific AI/count and rework provider.
    Missing providers, failed admission and a rejected review leave the instruction pending.
    ``hold`` (flowgate.v02.0105 T#2 B-3): the context the start is judged in — the reconciliation's ``legacy.person_hold``
    (an idle run taken too, its Stop / Time Machine conditions judged again), the loop then requested as an explicit
    request (Q439: the idle run goes back to running) — else ``attempt_owns``.
    """
    stored = repository.values(flow_id, node["node_key"])
    count = stored.get("instruction_review_count") or 0
    if count == 0 or (approvals.document(doc_id) or {}).get("doc_review_status") in APPROVED:
        return False
    if hold is None and not legacy.attempt_owns(run_id, node["node_key"], attempt_id, "instruction_review_start"):
        return True
    if legacy.review_loop_active(doc_id):
        return True
    project_id = legacy.project_of(flow_id)
    reviewer = stored.get("instruction_review_ai") or legacy.default_reviewer(project_id)
    reworker = fields.instruction_provider(stored) or node.get("assignee_provider_id")
    if not reviewer or not reworker:
        _log.warning("instruction review missing provider flow=%s node=%s", flow_id, node["node_key"])
        legacy.record_approval_wait(flow_id, node["node_key"], doc_id, legacy.REVIEW_NOT_STARTED,
                                    stage=fields.INSTRUCTION_STAGE)
        return True
    loop = fields.review_loop({**stored, "review_count": count}, reviewer=reviewer, reworker=reworker)
    criteria = loop["review_criteria"]

    def start():
        return legacy.start_review_loop(project_id, flow_id, doc_id, actor, loop,
                                        lambda last: fields.review_basis_note(criteria, last), node["node_key"],
                                        attempt_id=attempt_id)

    if hold is None:
        started = start()
    else:
        with hold as owned:
            if not owned:
                return True                     # stopped, stopping, a Time Machine operation, another attempt: recorded
            with legacy.explicit_request():     # Q439: an idle run (waiting for a person) takes it back to running
                started = start()
    if started is None:
        legacy.record_approval_wait(flow_id, node["node_key"], doc_id, legacy.REVIEW_NOT_STARTED,
                                    stage=fields.INSTRUCTION_STAGE)
    return True


# ── a lost run-end stage (flowgate.v02.0105 T#2 B-3, NR flowgate.v02.0101.0003 §3.9.2) ──────────

NO_REVIEW, NEEDS_REVIEW = "none", "review"
REVIEW_COUNTS = (1, 2, 3, -1)                   # a review loop's own review_count (its table CHECK)


def review_setting(flow_id: str, node_key: str, field: str = "review_count") -> Optional[str]:
    """The card's review setting as stored, not as ``fields.after_result`` reads it (that one takes a value it cannot
    read for 'none'): ``NO_REVIEW`` (no row, or the integer 0), ``NEEDS_REVIEW`` (the integer 1, 2, 3 or -1), else None —
    unknown (a row that does not parse, a string, any other number)."""
    value = repository.stored(flow_id, node_key, field)
    if value is repository.ABSENT:
        return NO_REVIEW
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    if value == 0:
        return NO_REVIEW
    return NEEDS_REVIEW if value in REVIEW_COUNTS else None


def reconcile(flow_id: str) -> None:
    """``card_settings_ports.reconcile_stage`` handler (B-3), on a run read and a Resume, outside every Flow guard: each AI
    node whose run-end stage was lost is followed once. Never raises (the read goes on)."""
    try:
        if not repository.available() or not legacy.graph_owned(flow_id):
            return
        run_row = legacy.active_run(flow_id)
        if run_row is None or not legacy.person_stage_open(flow_id, run_row):
            return                              # stopped, stopping, a Time Machine operation: nothing is taken
        rows = legacy.node_runs(run_row["run_id"])
    except Exception:  # noqa: BLE001 — a run read is never failed by its reconciliation
        _log.warning("card stage reconciliation failed flow=%s", flow_id, exc_info=True)
        return
    for row in rows:
        try:
            if row["status"] == legacy.REVIEW_NODE_STATUS and row.get("result_doc_id"):
                _reconcile_result(flow_id, run_row["run_id"], row)
            elif row["status"] == legacy.OPEN_NODE_STATUS and row.get("stage") == fields.INSTRUCTION_STAGE:
                _reconcile_instruction(flow_id, run_row["run_id"], row)
        except Exception:  # noqa: BLE001 — nothing taken for this node; the next read judges it again
            _log.warning("card stage reconciliation failed flow=%s node=%s", flow_id, row.get("node_key"), exc_info=True)


def _ai_card(flow_id: str, node_key: str) -> Optional[dict]:
    node = legacy.node_of_key(flow_id, node_key)
    if node is None or not settings.uses_ai(flow_id, node):
        return None
    return node


def _reconcile_result(flow_id: str, run_id: str, row: dict) -> None:
    """B-3 for a node in review with a result (3.9.2 ① ~ ④)."""
    key, doc_id = row["node_key"], row["result_doc_id"]
    node = _ai_card(flow_id, key)
    if node is None or fields.after_result(fields.card_kind(node.get("node_type"), node.get("doc_type")), {}) is None:
        return                                  # a person's card, a gate, R / B: no automatic approval
    if (approvals.document(doc_id) or {}).get("doc_review_status") in APPROVED or ai_working(flow_id, key, doc_id):
        return
    attempt_id = legacy.active_attempt_id(run_id, key)
    if legacy.approval_waits(run_id, key, doc_id, attempt_id):
        return                                  # tried once already: never again
    work = legacy.result_ai_run(flow_id, run_id, key, doc_id)
    if work is None or not ended_cleanly(work) or legacy.open_questions_of_ai_run(flow_id, work["run_id"]):
        return                                  # not its own AI result, not ended on its own, its questions wait
    issuer = work.get("issued_to") or ""

    def unknown() -> None:
        legacy.record_approval_wait(flow_id, key, doc_id, legacy.REVIEW_RESULT_UNKNOWN, attempt_id=attempt_id)

    try:
        setting = review_setting(flow_id, key)
        loops = legacy.review_loops(doc_id)
        if setting == NO_REVIEW:
            if loops:
                return unknown()                # its review started before the card was set to 'none'
            return _reconciled_approval(flow_id, run_id, key, doc_id, issuer, attempt_id)
        if setting is None:
            return unknown()
        if not loops:                           # ③ the run end was lost before its review started: start it, never approve
            return _start_result_review(flow_id, run_id, node, doc_id, repository.values(flow_id, key), issuer,
                                        work.get("provider_id"), attempt_id,
                                        hold=legacy.person_hold(flow_id, run_id, key, attempt_id, "reconcile_review_start"))
        latest = loops[0]
        if latest.get("current_stage") != legacy.STOPPED_LOOP_STAGE:
            return                              # ④-b not finished
        if latest.get("stop_reason") != PASSED:
            return legacy.record_approval_wait(flow_id, key, doc_id, not_passed(latest), attempt_id=attempt_id)
        if not _loop_of_attempt(latest, attempt_id) or not legacy.submission_passed(doc_id):
            return unknown()                    # ④-d an earlier attempt's pass, or a pass of an earlier revision
        return _reconciled_approval(flow_id, run_id, key, doc_id, issuer, attempt_id)     # ② passed
    except Exception:  # noqa: BLE001 — ④-d a read that failed: nothing is approved, nor tried again
        _log.warning("card result reconciliation failed flow=%s node=%s", flow_id, key, exc_info=True)
        unknown()


def _loop_of_attempt(loop: dict, attempt_id: Optional[str]) -> bool:
    """The loop's review run worked for ``attempt_id`` (the node's current attempt)."""
    return legacy.attempt_of_ai_run(legacy.ai_run_record(loop.get("run_id")) or {}) == attempt_id


def _reconciled_approval(flow_id: str, run_id: str, key: str, doc_id: str, issuer: str,
                         attempt_id: Optional[str]) -> None:
    """① / ② the automatic approval the lost run end would have made, judged as a person's stage is (an idle run waiting
    for the approval taken too, the run's Stop / Time Machine conditions judged again under the Stop gate's hold)."""
    with legacy.person_hold(flow_id, run_id, key, attempt_id, "reconcile_approval") as owned:
        if owned:
            legacy.approve(flow_id, doc_id, issuer, legacy.AUTOMATION, node_key=key, attempt_id=attempt_id)


def _reconcile_instruction(flow_id: str, run_id: str, row: dict) -> None:
    """B-3 for a node open in its instruction stage whose instruction AI run ended on its own and was never followed: the
    same rules on ``instruction_review_count`` (3.9.2, 지시서 단계)."""
    key = row["node_key"]
    node = _ai_card(flow_id, key)
    if node is None or _handoff_pending(flow_id, key) or legacy.node_ai_run_active(flow_id, key):
        return
    work = legacy.stage_ai_run(flow_id, run_id, key)
    attempt_id = legacy.active_attempt_id(run_id, key)
    if work is None or not ended_cleanly(work) or legacy.attempt_of_ai_run(work) != attempt_id:
        return                                  # still writing, failed (the stage's own failure), an old attempt's
    if legacy.open_questions_of_ai_run(flow_id, work["run_id"]):
        return
    doc_id = written_instruction(flow_id, node, row)
    if doc_id is None:
        return
    issuer = work.get("issued_to") or ""
    if (approvals.document(doc_id) or {}).get("doc_review_status") in APPROVED:
        # approved, and the node still in its instruction stage: the result stage's start after it was lost
        return _reconciled_result_stage(flow_id, run_id, key, node, doc_id, issuer, attempt_id, approve=False)
    if legacy.approval_waits(run_id, key, doc_id) or legacy.review_loop_active(doc_id):
        return

    def unknown() -> None:
        legacy.record_approval_wait(flow_id, key, doc_id, legacy.REVIEW_RESULT_UNKNOWN, stage=fields.INSTRUCTION_STAGE)

    try:
        setting = review_setting(flow_id, key, "instruction_review_count")
        loops = legacy.review_loops(doc_id)
        if setting is None or (setting == NO_REVIEW and loops):
            return unknown()
        if setting == NEEDS_REVIEW and not loops:    # ③ starts it once (an idle run taken too), or records why not
            review_instruction(flow_id, node, doc_id, issuer, run_id, attempt_id,
                               hold=legacy.person_hold(flow_id, run_id, key, attempt_id,
                                                       "reconcile_instruction_review_start"))
            return
        if setting == NEEDS_REVIEW:
            latest = loops[0]
            if latest.get("current_stage") != legacy.STOPPED_LOOP_STAGE:
                return
            if latest.get("stop_reason") != PASSED:
                return legacy.record_approval_wait(flow_id, key, doc_id, not_passed(latest),
                                                   stage=fields.INSTRUCTION_STAGE)
            if not _loop_of_attempt(latest, attempt_id) or not legacy.submission_passed(doc_id):
                return unknown()
    except Exception:  # noqa: BLE001
        _log.warning("card instruction reconciliation failed flow=%s node=%s", flow_id, key, exc_info=True)
        return unknown()
    _reconciled_result_stage(flow_id, run_id, key, node, doc_id, issuer, attempt_id)     # ① / ②


def _reconciled_result_stage(flow_id: str, run_id: str, key: str, node: dict, doc_id: str, issuer: str,
                             attempt_id: Optional[str], approve: bool = True, hold=None) -> None:
    """① / ② of the instruction stage: the instruction's automatic approval (``approve``; not again for one approved
    already) and the result stage's start, judged as ``_reconciled_approval`` judges — an idle run waiting for a person
    taken too, the run's Stop / Time Machine conditions and the current attempt judged again under the Stop gate's hold.
    The result stage's execution request takes an idle run back to running (``start_result_stage`` ``idle_ok``); its AI
    start runs after the hold. Once the node is in its result stage, the next read finds nothing to reconcile.
    ``hold``: the context it is judged in (0111 T-A1: after the stage's questions are answered, ``_answered_hold``), else
    ``legacy.person_hold``."""
    jobs: list = []
    if hold is None:
        hold = legacy.person_hold(flow_id, run_id, key, attempt_id, "reconcile_instruction_approval")
    with hold as owned:
        if not owned:
            return
        if approve:
            approve_instruction(flow_id, node, doc_id, issuer)
        legacy.start_result_stage(flow_id, run_id, key, doc_id, issuer, attempt_id, defer=jobs, idle_ok=True)
    for job in jobs:                            # the result stage's AI start, after the hold
        job()
