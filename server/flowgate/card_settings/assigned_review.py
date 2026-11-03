"""The review stages of an assigned attempt (flowgate.v02.0113 T#3, L-2 §2.2.2, §2.8, §2.9, §2.11).

- ``open_review_stage_locked`` / ``ensure_intent_locked`` (§2.2.2): inside one Flow guard a submission's review
  assignment and its execution intent — the review loop's ledger request (``execution.request_loop_locked``, refused
  under a savepoint: the assignment stays ``failed(review_not_started)``), or the automatic approval — commit together.
  An AI assignment left without its intent gets the one request it misses (the ledger's request of the same
  assignment first).
- ``drive_review_assignment`` / ``consume_loop_verdict`` (§2.8.4): what is left of an assignment, done once — a loop's
  verdict recorded on the assignment before its approval / rejection / B-1, the next step after an approval (the
  result stage after an instruction), a newer submission taken over. The run end, the next save and the
  reconciliation share them.
- ``after_result_work`` / ``after_loop_end`` / ``reconcile_row`` (§2.9, §2.11): the run-end and run-read branches of an
  assigned attempt; a pre-feature attempt never reaches them (``review_stage`` keeps its own functions).
- ``command_review`` (flowgate.v02.0125 T#1, Q #544): the person's explicit step / full command asks again for the
  result review of an AI card whose review count was spent on a revision its loop reworked and no review answered —
  only that command (``card_settings_ports.command_review``), never a run read nor the reconciliation.

``mode`` is the hold the step is judged in (§2.8.5): ``AUTO`` — at an AI run's end (``attempt_hold``, an idle run
refused); ``HELD`` — a person's step, an answered question, an opening's follow-up or a reconciliation
(``person_hold``: an idle run taken, its request made as an explicit one).
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from flowgate.card_settings import fields, repository
from flowgate.card_settings import legacy_adapter as legacy
from flowgate.card_settings import stage_assignment as sa

_log = logging.getLogger(__name__)
store = sa.store                                   # reads raise ``AssignmentUnreadable`` (L-2 §2.14)

AUTO, HELD = "auto", "held"
TAKE_OVER_LATER, STAGE_MOVED = "take_over_later", "stage_moved"
PASSED_STOP, AUTHOR_REVISION, STOPPED = "review_passed", "awaiting_author_revision", "stopped"
APPROVED = ("approved", "wf_done")
INSTRUCTION_NOT_APPROVED, STAGE_NOT_PERSON, ASSIGNMENT_UNREADABLE = (
    "instruction_not_approved", "stage_not_person", "assignment_unreadable")


# ── holds, guards, rows ──────────────────────────────────────────────────────

def hold_of(mode: str, flow_id: str, run_id: str, key: str, attempt_id: Optional[str], operation: str):
    if mode == AUTO:
        return legacy.attempt_hold(run_id, key, attempt_id, operation)
    return legacy.person_hold(flow_id, run_id, key, attempt_id, operation)


def _run_jobs(jobs: list) -> None:
    while jobs:
        job = jobs.pop(0)
        try:
            job()
        except Exception:  # noqa: BLE001 — a post-commit job never fails its caller
            _log.exception("card stage post-commit job failed")


def _doc(doc_id: Optional[str]) -> Optional[dict]:
    return legacy.document_row(doc_id)


def review_target(a: dict, row: Optional[dict]) -> Optional[str]:
    """The document a review assignment judges now: the node run's result / instruction, else its basis."""
    if row is not None:
        current = row.get("result_doc_id") if a["stage"] == sa.RESULT_REVIEW else row.get("instruction_doc_id")
        if current:
            return current
    return a.get("basis_doc_id")


def b1_stage_of(stage: str) -> str:
    """The B-1 ``stage`` of an assignment stage: the instruction's (its review included) or the result's."""
    return fields.INSTRUCTION_STAGE if stage in (sa.INSTRUCTION, sa.INSTRUCTION_REVIEW) else "result"


def _b1_stage(a: dict) -> str:
    return b1_stage_of(a["stage"])


def command_cycle(run_id: Optional[str], key: str, attempt_id: Optional[str], stage: str) -> tuple[int, int]:
    """flowgate.v02.0124 T#1 (rework, review P1): where the recovery cycle of the attempt's review in the B-1 ``stage``
    begins — the latest explicit command re-request of it (``sa.restart_review_stage``: its ``stage_assigned`` event
    with ``rerequest_cause`` ``user_command``), as ``(that event's event_seq, the seq it opened)``; ``(0, 0)`` when no
    command requested it again (the whole attempt is one cycle, as before).

    A command's re-request is a new review of the same submission (its mark unchanged when nothing edited it), and the
    records of the cycle it ended are not this cycle's: the needs_intervention reason the command answered
    (``review_not_passed:<budget spent>``) would otherwise stop every retry of a system error that follows it
    (``review_retry_due``, ``after_verdict``), and the tries counted before it would spend this cycle's limit. The
    records and rows themselves are kept (history); only what the retries count starts again. A needs_intervention
    reason recorded *in* the cycle still stops its retries, and nothing re-requests a needs_intervention by itself — only
    the next command, which opens the next cycle."""
    from modules.flow_gate.db.connection import get_store  # noqa: PLC0415
    from modules.flow_gate.graph_workflow.services import run_events  # noqa: PLC0415

    if not run_id or not attempt_id:
        return 0, 0
    found = (0, 0)
    for row in get_store()._fetch_all(
            "SELECT event_seq, payload_json FROM graph_run_events WHERE run_id = ? AND event_type = ? AND cause = ? "
            "ORDER BY event_seq", [run_id, run_events.RUN_STATUS, sa.EV_ASSIGNED]) or []:
        try:
            data = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict) or data.get(sa.REREQUEST_CAUSE) != sa.USER_COMMAND:
            continue
        if data.get("node_key") != key or data.get("attempt_id") != attempt_id \
                or b1_stage_of(str(data.get("stage") or "")) != stage:
            continue
        found = (int(row["event_seq"]), int(data.get("seq") or 0))
    return found


def _cycle_waits(run_id: str, key: str, attempt_id: Optional[str], stage: str) -> list[dict]:
    """The node's B-1 payloads (``legacy.approval_wait_rows``, oldest first) recorded in the current recovery cycle of
    the attempt's review in ``stage`` (``command_cycle``: after its latest command re-request; all when none)."""
    floor, _seq = command_cycle(run_id, key, attempt_id, stage)
    rows = legacy.approval_wait_rows(run_id, key, attempt_id)
    return rows if not floor else [row for row in rows if int(row.get("event_seq") or 0) > floor]


def approval_waits_for(run_id: str, key: str, attempt_id: Optional[str], submission: Optional[dict],
                       stage: str, reason: Optional[str] = None) -> bool:
    """A B-1 of this submission (its mark) is recorded (L-2 §2.7.2: a mark-less B-1 is a pre-feature record) — in the
    current recovery cycle (``command_cycle``; 0124 T#1 rework: a command's re-request records its own failures)."""
    if not submission:
        return False
    for row in _cycle_waits(run_id, key, attempt_id, stage):
        if row.get("submission_mark") == submission and row.get("stage", "result") == stage \
                and (reason is None or row.get("reason") == reason):
            return True
    return False


def approval_wait_reasons_for(run_id: str, key: str, attempt_id: Optional[str], submission: Optional[dict],
                              stage: str) -> list[str]:
    """flowgate.v02.0119 T#3 (T-2): the B-1 reasons recorded for this submission (its mark) and stage, oldest first —
    of the current recovery cycle (``command_cycle``, flowgate.v02.0124 T#1 rework)."""
    if not submission:
        return []
    return [str(row["reason"]) for row in _cycle_waits(run_id, key, attempt_id, stage)
            if row.get("submission_mark") == submission and row.get("stage", "result") == stage and row.get("reason")]


def record_once(flow_id: str, run_id: str, key: str, attempt_id: Optional[str], doc_id: Optional[str],
                reason: str, submission: Optional[dict], stage: str = "result") -> None:
    """B-1 with its submission mark, once per submission and reason."""
    if not doc_id or not submission or approval_waits_for(run_id, key, attempt_id, submission, stage, reason):
        return
    legacy.record_submission_wait(flow_id, key, doc_id, reason, submission, attempt_id=attempt_id, stage=stage)


def review_loop_active(a: Optional[dict]) -> bool:
    """The assignment's review loop is requested or still working (L-2 ``review_loop_active``)."""
    if not a or a["actor"] != sa.AI or a["stage_state"] != sa.ACTIVE:
        return False
    loop = legacy.loop_of_assignment(a["attempt_id"], a["stage"], a["seq"])
    return loop is None or loop.get("current_stage") != STOPPED


def _claim(flow_id: str, a: dict, verdict: str) -> bool:
    """Inside a new guard: the verdict recorded once (``done``); False when another consumer did it first."""
    def body() -> bool:
        cur = store.read(a["attempt_id"], a["stage"], a["seq"])
        if cur is None or cur.get("verdict") or cur["stage_state"] not in (sa.ACTIVE, sa.WAITING):
            return False
        return store.update_state(cur["attempt_id"], cur["stage"], cur["seq"], cur["stage_state"], sa.DONE,
                                  verdict=verdict)
    return bool(legacy.with_flow_guard(flow_id, body))


def _context(flow_id: str, run_id: str, key: str):
    run_row = legacy.run_by_id(run_id)
    node = legacy.run_node_row(flow_id, run_row, key) if run_row else None
    return run_row, node


# ── the review assignment and its intent (L-2 §2.2.2) ─────────────────────────

def review_loop_request(flow_id: str, a: dict) -> dict:
    """L-2 §2.8.3: the card's loop with the assignment's reviewer, rounds and rework AI (None: no rework)."""
    try:
        stored = repository.values(flow_id, a["node_key"])
    except Exception:  # noqa: BLE001 — the loop's other values take their defaults
        stored = {}
    return fields.review_loop({**stored, "review_count": a["review_rounds"]}, reviewer=a["provider_id"],
                              reworker=a.get("rework_provider_id"))


def open_review_stage_locked(flow_id: str, run_row: dict, node: dict, attempt_id: str, stage: str, doc: dict,
                             author: str, author_provider_id: Optional[str], mode: str, after: list, ctx=None):
    """Inside the Flow guard. The submission's review assignment (an existing one for the same submission) with its
    intent, ``TAKE_OVER_LATER`` (an earlier submission's loop works) or ``LEGACY``."""
    cur = sa.current_review_assignment(attempt_id, stage)
    if cur is not None and not sa.needs_new_submission(cur, doc):
        return cur
    if cur is not None and review_loop_active(cur):
        return TAKE_OVER_LATER
    seq = (int(cur["seq"]) if cur is not None else 0) + 1
    a = sa.start_stage(flow_id, run_row, node, attempt_id, stage, seq, author=author, basis=sa.mark(doc),
                       author_provider_id=author_provider_id, ctx=ctx)
    if a == sa.LEGACY:
        return sa.LEGACY
    return ensure_intent_locked(flow_id, run_row, node, a, doc, mode, after)


def ensure_intent_locked(flow_id: str, run_row: dict, node: dict, a: dict, doc: Optional[dict], mode: str,
                         after: list) -> dict:
    """Inside the Flow guard: the assignment's execution intent, recorded once (idempotent, E-43)."""
    from flowgate.graph_run import execution  # noqa: PLC0415 — the Graph run's ledger (v0.2)

    legacy.require_guard(flow_id)
    a = store.read(a["attempt_id"], a["stage"], a["seq"]) or a
    if a.get("intent_kind") or a["stage_state"] in (sa.DONE, sa.FAILED):
        return a
    if a["actor"] == sa.NO_REVIEW:
        store.set_intent(a["attempt_id"], a["stage"], a["seq"], store.INTENT_APPROVAL)
        return store.read(a["attempt_id"], a["stage"], a["seq"]) or a
    if a["actor"] != sa.AI:
        return a
    existing = store.ledger_exec_for(a["attempt_id"], a["stage"], a["seq"])
    if existing:
        store.set_intent(a["attempt_id"], a["stage"], a["seq"], store.INTENT_EXECUTION, existing)
        return store.read(a["attempt_id"], a["stage"], a["seq"]) or a
    doc = doc or _doc(a.get("basis_doc_id"))
    loop = review_loop_request(flow_id, a)
    criteria = loop["review_criteria"]
    purpose = sa.RESULT_REVIEW_ROUND if a["stage"] == sa.RESULT_REVIEW else sa.INSTRUCTION_REVIEW_ROUND
    # the loop runs for the run's issuer, as every card review loop does (0083 WP-4: never the author — a person
    # assignee may lack the review's reject permission); the approval's actor stays the token recipient (L-2 §2.7.4)
    issuer = legacy.run_issuer(run_row) or sa.issued_to_of(node, run_row)
    project_id = legacy.project_of(flow_id)
    try:
        exec_id = legacy.isolated(lambda: execution.request_loop_locked(
            project_id, flow_id, doc["doc_id"], issuer, loop,
            fields.review_basis_note(criteria, legacy.last_rejection(doc["doc_id"])), a["node_key"],
            assignment_ref=sa.ref_of(a), origin=sa.origin(a, purpose, flow_id, doc=doc, round_no=1),
            statuses="idle_excepted" if mode == HELD else "writable", attempt_id=a["attempt_id"]),
            "fg_review_request")
    except Exception as exc:  # noqa: BLE001 — E-42: the request is rolled back, the assignment stays failed
        _log.info("card review not started flow=%s node=%s: %s", flow_id, a["node_key"], exc)
        failed = sa.fail(a, sa.REVIEW_NOT_STARTED)
        after.append(lambda error=exc: execution.start_failed(None, error))   # a fence refusal recorded after the commit
        after.append(lambda: _review_not_started(flow_id, failed, doc))
        return failed
    store.set_intent(a["attempt_id"], a["stage"], a["seq"], store.INTENT_EXECUTION, exec_id)
    after.append(lambda: _start_loop(flow_id, a, doc["doc_id"], issuer, loop, criteria, exec_id))
    return store.read(a["attempt_id"], a["stage"], a["seq"]) or a


def _start_loop(flow_id: str, a: dict, doc_id: str, issuer: str, loop: dict, criteria: str, exec_id: str) -> None:
    """After the commit: start the requested loop, or await the owner of a duplicate start's verdict."""
    failure: dict = {}
    started = legacy.start_review_loop(legacy.project_of(flow_id), flow_id, doc_id, issuer, loop,
                                       lambda last: fields.review_basis_note(criteria, last), a["node_key"],
                                       graph_exec_id=exec_id, attempt_id=a["attempt_id"], failure=failure)
    if started is None and not failure.get("duplicate_start"):
        # L-2 §2.10 / E-13: an admission refusal keeps its own code (``start_refused:<code>``), any other failure of
        # the start is ``start_failed``; no other AI is asked, and no read restarts it (E-40)
        cause = failure.get("cause") or sa.START_FAILED
        failed = legacy.with_flow_guard(flow_id, lambda: sa.fail(store.read(a["attempt_id"], a["stage"], a["seq"])
                                                                 or a, cause))
        _review_not_started(flow_id, failed, _doc(doc_id))


def _review_not_started(flow_id: str, a: dict, doc: Optional[dict]) -> None:
    record_once(flow_id, a["run_id"], a["node_key"], a["attempt_id"], (doc or {}).get("doc_id") or a.get("basis_doc_id"),
                sa.REVIEW_NOT_STARTED, sa.basis_of(a), _b1_stage(a))
    if a["stage"] == sa.INSTRUCTION_REVIEW:
        legacy.with_flow_guard(flow_id, lambda: legacy.set_dispatch_error(
            a["run_id"], a["node_key"], "review_not_started:" + str(a.get("fail_cause") or "refused")))


# ── a review start that failed: a system error, retried (flowgate.v02.0119 T#3, T-2 rework) ──

REVIEW_RETRY = "review_retry"


def _setting_unreadable(a: dict) -> bool:
    return a["actor"] == sa.WAIT_SETUP and a.get("actor_reason") == sa.SETTING_UNREADABLE


def _retryable(a: Optional[dict]) -> bool:
    """A review assignment whose automatic review did not start: an AI review whose start failed, or a wait on its
    setting that could not be read (still waiting)."""
    if not a or a["stage"] not in sa.REVIEW_STAGES:
        return False
    if a["actor"] == sa.AI:
        return a["stage_state"] == sa.FAILED
    return _setting_unreadable(a) and a["stage_state"] == sa.WAITING


def review_start_tries(a: dict) -> int:
    """How many times the automatic review of ``a``'s submission (its basis) was tried in its stage and did not start:
    its review assignments that failed and those that waited on a setting that could not be read. Each try is an
    assignment of its own (a retry opens the next ``seq``), so the count is the rows themselves, apart from the B-1
    records (``record_once`` keeps one per submission and reason, which counts no try). flowgate.v02.0124 T#1 rework:
    only the rows of the current recovery cycle — from the ``seq`` the latest command re-request opened
    (``command_cycle``) — count."""
    basis = sa.basis_of(a)
    _floor, first = command_cycle(a["run_id"], a["node_key"], a["attempt_id"], _b1_stage(a))
    return sum(1 for r in store.for_node(a["run_id"], a["node_key"], a["attempt_id"])
               if r["stage"] == a["stage"] and int(r["seq"]) >= first and sa.basis_of(r) == basis
               and (r["stage_state"] == sa.FAILED or _setting_unreadable(r)))


def review_retry_due(a: dict) -> bool:
    """May the review of ``a``'s submission be tried again: no needs_intervention reason recorded for that submission
    (its mark and stage) and fewer than ``SYSTEM_ERROR_RETRY_LIMIT`` tries made — both of the current recovery cycle
    (flowgate.v02.0124 T#1 rework, ``command_cycle``: a command's re-request starts it again; a needs_intervention reason
    recorded after it still stops the retries)."""
    from flowgate.card_intervention import reasons as intervention  # noqa: PLC0415

    recorded = approval_wait_reasons_for(a["run_id"], a["node_key"], a["attempt_id"], sa.basis_of(a), _b1_stage(a))
    if any(not intervention.is_system_error(r) for r in recorded):
        return False
    return review_start_tries(a) < intervention.SYSTEM_ERROR_RETRY_LIMIT


def _stage_holds(a: dict, row: Optional[dict], doc: Optional[dict]) -> bool:
    """The node still waits on ``a``'s submission: in review with it as its result (an instruction: open in its
    instruction stage with it as its instruction), not approved."""
    if row is None or not doc or doc["doc_id"] != a.get("basis_doc_id") or doc.get("doc_review_status") in APPROVED:
        return False
    if a["stage"] == sa.RESULT_REVIEW:
        return row["status"] == "review" and row.get("result_doc_id") == doc["doc_id"]
    return (row["status"] == "open" and row.get("stage") == sa.INSTRUCTION
            and row.get("instruction_doc_id") == doc["doc_id"])


def _start_errors(a: dict) -> tuple:
    """The node's dispatch errors a failed instruction review start recorded (``_review_not_started``,
    ``stage_assignment.start_failed``)."""
    cause = str(a.get("fail_cause") or "refused")
    return ("review_not_started:" + cause, "ai_unavailable:" + cause)


def retry_review_stage(flow_id: str, a: dict, mode: str, jobs: list) -> None:
    """flowgate.v02.0119 T#3 (T-2 rework, review P1): the automatic review of ``a``'s submission is tried again while
    ``review_retry_due`` allows it: a new review assignment (the next ``seq``, the same author and basis, its role
    resolved again: a start that failed for a moment, a setting fixed since) with its intent, in one Flow guard under
    ``hold_of(mode)``; a setting wait it replaces is failed (``setting_unreadable``: its try counted, kept as history).
    A try that fails again is the next row (``review_start_tries``); past the limit the card stays a system error and
    nothing is tried again (an explicit command opens a new attempt). Nothing else is retried here: a needs_intervention
    reason, a node moved on, another consumer's newer assignment. flowgate.v02.0125 T#1 (Q #544) changed the 0119 T-2
    contract for one case only, outside this retry: a review count spent on a revision no review answered is asked
    again by the person's explicit step / full command (``command_review``), never by a read or a reconciliation."""
    if not _retryable(a) or not review_retry_due(a):
        return
    run_id, key = a["run_id"], a["node_key"]
    run_row, node = _context(flow_id, run_id, key)
    if run_row is None or node is None:
        return
    after: list = []
    with hold_of(mode, flow_id, run_id, key, a["attempt_id"], REVIEW_RETRY) as owned:
        if not owned:
            return

        def body():
            cur = sa.current_review_assignment(a["attempt_id"], a["stage"])
            if cur is None or int(cur["seq"]) != int(a["seq"]) or not _retryable(cur):
                return None                         # another consumer took it first
            row = legacy.node_run(run_id, key)
            doc = _doc(review_target(cur, row))
            if not _stage_holds(cur, row, doc):
                return None
            if cur["actor"] == sa.WAIT_SETUP:
                sa.fail(cur, sa.SETTING_UNREADABLE)
            nxt = sa.start_stage(flow_id, run_row, node, cur["attempt_id"], cur["stage"], int(cur["seq"]) + 1,
                                 author=cur.get("author") or "unknown", basis=sa.basis_of(cur),
                                 author_provider_id=cur.get("author_provider_id"))
            if not isinstance(nxt, dict):
                return None
            nxt = ensure_intent_locked(flow_id, run_row, node, nxt, doc, mode, after)
            if cur["stage"] == sa.INSTRUCTION_REVIEW and nxt.get("stage_state") != sa.FAILED:
                legacy.clear_dispatch_error(run_id, key, _start_errors(cur))
            return nxt

        nxt = legacy.with_flow_guard(flow_id, body)
        _run_jobs(after)
    if isinstance(nxt, dict):
        drive_review_assignment(flow_id, nxt, mode, jobs)


# ── an explicit command's re-request of a review that spent its budget (flowgate.v02.0124 T#1) ──
#
# 0003-NR §5.1: a card in ``needs_intervention`` whose result review loop spent its budget (its reason
# ``review_not_passed:`` retry_exhausted / review_count_exhausted / total_timeout) was moved by nothing a v0.2 screen
# sends — a step / full command was accepted and opened nothing. An explicit command now requests that review again as
# the assignment's next ``seq``, on the conditions of §5.1.2 (all of them, judged here inside the command's Flow guard);
# every other card is left as it is. The ended ``seq`` (its state, verdict and intent) is only read; nothing retries it
# automatically — only the next command requests it again.
#
# Recovery cycle (rework, review P1): the re-request begins a new recovery cycle of the card's review (``command_cycle``).
# A system error after it — ``seq + 1``'s start refused (``review_not_started``), its pass's automatic approval refused
# (``approval_refused``, ...) — is retried by the existing bounded paths (``retry_review_stage`` / ``after_verdict``, up
# to ``SYSTEM_ERROR_RETRY_LIMIT`` of this cycle): the ``review_not_passed`` record the command answered belongs to the
# cycle it ended and no longer stops them. The earlier ``seq`` rows and B-1 records stay as they are.
#
# Lock order (0003-NR §5.1.3-6, decided here): the command already holds its Flow guard (``run_service.start_run`` /
# ``_guarded_run``), and ``retry_review_stage`` takes the node's hold (the Stop gate's shared hold, ``hold_of``) *before*
# the guard. Taking that hold here would invert the order against a Stop / Time Machine rewind that holds the gate
# exclusively and waits for the guard (a deadlock), so no hold is taken: the re-request is a compare-and-set inside the
# guard only — the latest ``seq`` read in the guard, ``restart_review_stage``'s insert-if-absent of ``seq + 1`` (a loser
# requests nothing), the ledger request in the same transaction (``ensure_intent_locked`` ->
# ``execution.request_loop_locked``, which requires the guard). Every writer of a review assignment and of a Stop writes
# inside the same Flow guard, so none interleaves. The loop's start after the commit (``_start_loop``) is fenced by the
# engine again (``execution._request_document``: ``fencing.require`` / ``fencing.guarded``, gate before guard); a
# refusal there is the existing E-42 path (``seq + 1`` failed ``start_refused:<code>`` / ``start_failed``).

REREQUEST_SAVEPOINT = "fg_command_rerequest"


def rerequest_due(flow_id: str, run_row: dict, row: dict, attempt_id: Optional[str]) -> Optional[tuple]:
    """0003-NR §5.1.2 conditions 1~6 for the node run ``row`` (inside the command's Flow guard, nothing written):
    ``(node, current review assignment, result document)`` when its result review may be requested again, else None.

    1 in review with a result, a general card (no gate), not held; 2 its latest recorded reason a spent budget
    (``reasons.command_rerequest_due``); 3 an assigned attempt whose latest result review is an AI's, ended (``done``)
    with a ``not_passed:`` verdict; 4 an AI's document (``author`` ai) with its reviewer and rework AI; 5 the result is
    that review's basis document, not approved, and no AI works on it (its run, its review loop, its hand-off); 6 the
    run is running (the command took it there; a Time Machine operation refused the command already) and no
    execution of the attempt is requested or running."""
    from flowgate.card_intervention import reasons as intervention  # noqa: PLC0415
    from flowgate.card_settings import review_stage  # noqa: PLC0415

    key = row["node_key"]
    if row.get("status") != "review" or not row.get("result_doc_id") or run_row.get("status") != "running":
        return None
    node = legacy.run_node_row(flow_id, run_row, key)
    if node is None or node.get("node_type") == "gate" or key in legacy.held_nodes(flow_id):
        return None
    attempt_id = attempt_id or legacy.active_attempt_id(run_row["run_id"], key)
    if not attempt_id or sa.is_legacy_attempt(attempt_id):
        return None
    if not intervention.command_rerequest_due(legacy.approval_wait_reason(flow_id, row)):
        return None
    cur = sa.current_review_assignment(attempt_id, sa.RESULT_REVIEW)
    if cur is None or cur["actor"] != sa.AI or cur["stage_state"] != sa.DONE \
            or not str(cur.get("verdict") or "").startswith(sa.NOT_PASSED):
        return None
    if cur.get("author") != sa.AI or not cur.get("provider_id") or not cur.get("rework_provider_id"):
        return None
    doc = _doc(row["result_doc_id"])
    if not doc or doc["doc_id"] != cur.get("basis_doc_id") or doc.get("doc_review_status") in APPROVED:
        return None
    if review_stage.ai_working(flow_id, key, doc["doc_id"]) or _attempt_executing(attempt_id):
        return None
    return node, cur, doc


def _attempt_executing(attempt_id: str) -> bool:
    """An execution of the attempt is requested or running (``graph_attempt_runs``, the ledger's own judgement)."""
    from modules.flow_gate.db.connection import get_store  # noqa: PLC0415

    return bool(get_store()._fetch_one("SELECT 1 AS present FROM graph_attempt_runs WHERE attempt_id = ? "
                                       "AND status IN ('requested', 'running') LIMIT 1", [attempt_id]))


def rerequest_review_locked(ctx, row: dict) -> bool:
    """flowgate.v02.0124 T#1 (0003-NR §5.1.3-5), an explicit step / full command inside its Flow guard
    (``run_service.go_on_command`` -> ``graph_execution_hooks`` ``rerequest_review``): the result review of the node run
    ``row``'s card is requested again when ``rerequest_due`` admits it — ``seq + 1`` opened with the ended assignment's
    reviewer, rework AI and rounds (``sa.restart_review_stage``), its intent and ledger request recorded with it
    (``ensure_intent_locked``: ``assignment_ref`` ``seq + 1``, ``origin`` round 1, ``writable``: the run is running),
    the loop started after the commit (``_start_loop``, a post-commit job of the command). True when ``seq + 1`` was
    opened — the card is one of the command's targets (a step records it, ``command.record_step_targets``), its start
    refused included (E-42: ``seq + 1`` failed). False: nothing written. Lock order: see the section comment (no hold,
    a compare-and-set in the guard).

    The judgement and its writes run under one savepoint: a failure (an assignment that cannot be read, a store
    error) rolls back what this card wrote and leaves it as it was — the command goes on with the others (logged)."""
    flow_id = ctx.flow_id
    legacy.require_guard(flow_id)
    after: list = []

    def body() -> bool:
        found = rerequest_due(flow_id, ctx.run, row, None)
        if found is None:
            return False
        node, cur, doc = found
        nxt, inserted = sa.restart_review_stage(flow_id, cur, sa.mark(doc), ctx=ctx)
        if not inserted:
            return False                            # another writer opened that seq first: it requests, not we
        ensure_intent_locked(flow_id, ctx.run, node, nxt, doc, AUTO, after)
        return True

    numbered = ctx.run.get("last_event_seq")
    try:
        opened = bool(legacy.isolated(body, REREQUEST_SAVEPOINT))
    except Exception:  # noqa: BLE001 — this card is left as it was; the command goes on
        ctx.run["last_event_seq"] = numbered        # an event rolled back with the savepoint numbers nothing
        _log.warning("command re-request of the review failed flow=%s node=%s", flow_id, row.get("node_key"),
                     exc_info=True)
        return False
    if opened and after:
        ctx.jobs.append(lambda: _run_jobs(after))
    return opened


# ── a spent review asked again by the person's step / full command (flowgate.v02.0125 T#1, Q #544) ──
#
# Beside the flowgate.v02.0124 T#1 re-request above (the same command, inside its Flow guard, so it runs first): when
# that one opened ``seq + 1`` for the card, the latest result review is no longer the spent one and nothing is asked
# here; this asks what it does not admit (e.g. a spent assignment without its rework AI recorded, or a command held by
# its integrations). Lock order: this runs as a job after the command's guard and fence, and takes the node's hold
# before the Flow guard, as ``retry_review_stage`` does — never a hold inside the guard.

COMMAND_REVIEW = "command_review"
EXHAUSTED = "review_count_exhausted"
EXHAUSTED_VERDICT = sa.NOT_PASSED + EXHAUSTED                # the spent assignment's verdict (``consume_loop_verdict``)
EXHAUSTED_REASON = legacy.REVIEW_NOT_PASSED + EXHAUSTED      # its B-1 (``review_stage.not_passed``)
REVISED = "revised"


def command_review(flow_id: str, run_id: str) -> None:
    """``card_settings_ports.command_review`` handler (flowgate.v02.0125 T#1, Q #544; 0005-NR §3.5 / §4.1), outside
    every guard: the person's explicit step / full command (the menu's 다음 단계 / 전체 실행 — ``run_service._go_on``
    with a mode, after its Flow guard and its ``reconcile_stage`` job) is that card's explicit re-review request.

    It changes the 0119 T-2 contract (``retry_review_stage``, ``command._waiting_command``) for this command only: an
    AI card in review whose result review assignment ended ``not_passed:review_count_exhausted`` (its B-1
    ``review_not_passed:review_count_exhausted`` recorded) on a document its loop then reworked (``revised``) and no
    review answered yet (``command_review_due``) takes one new result review assignment — the next ``seq``, the card's
    review count, the same author and rework AI (``sa.start_stage``) — whose basis is the document's latest revision
    (its submission mark, never the spent assignment's basis), with its loop request (``ensure_intent_locked``,
    ``HELD``: an idle run taken back to running), in one Flow guard under the person's hold (``hold_of(HELD)``: the
    run still the Flow's active one, not stopped, no Time Machine operation). The conditions are judged again inside
    that guard, so concurrent or repeated commands open one ``seq`` for a revision and none for a revision already
    asked or judged. The loop's first hop follows the document (``compute_review_baseline``: ``revised`` -> review).

    The command's own scope is unchanged (``record_command`` / ``record_step_targets``): a pass completes the card
    (the automatic approval and ``ai_review_passed``) and its next cards open as the command's mode lets them (full: at
    once; step: pending until the next command). A run read, F5, a reselection, the reconciliation and a loop end never
    get here. A memo is no condition. Never raises."""
    try:
        if not repository.available() or not legacy.graph_owned(flow_id):
            return
        run_row = legacy.active_run(flow_id)
        if run_row is None or run_row["run_id"] != run_id or not legacy.person_stage_open(flow_id, run_row):
            return                              # not the Flow's active run, stopped, stopping, a Time Machine operation
        rows = legacy.node_runs(run_id)
    except Exception:  # noqa: BLE001 — the command stands; nothing is asked
        _log.warning("command re-review not read flow=%s run=%s", flow_id, run_id, exc_info=True)
        return
    for row in rows:
        if row["status"] != "review" or not row.get("result_doc_id"):
            continue
        try:
            _command_review_row(flow_id, run_row, row)
        except Exception:  # noqa: BLE001 — nothing asked for this node; the next command judges it again
            _log.warning("command re-review failed flow=%s node=%s", flow_id, row.get("node_key"), exc_info=True)


def command_review_due(flow_id: str, run_id: str, row: Optional[dict]) -> Optional[tuple]:
    """(the spent result review assignment, the document) when the node run ``row`` takes a command's re-review now,
    else None (0005-NR §3.5 guards 2 ~ 5, Q #544 5): an assigned attempt, the card not held; its latest result review
    an AI one, done with ``not_passed:review_count_exhausted`` (an AI author) and its B-1 recorded for its basis; the
    node still in review on that document (``_stage_holds``), the document ``revised``; its latest revision — by
    ``(doc_id, revision_no)``, never its submission mark — neither asked already (a result review assignment of any
    attempt and ``seq`` with that basis revision: ``store.asked_for_revision``) nor reviewed (any ``document_reviews``
    row of that revision: ``legacy.revision_reviewed``; a rejected revision submitted again is the same revision); no AI
    working on the card (its AI run, an execution request, a review loop: ``review_stage.ai_working``)."""
    from flowgate.card_settings import review_stage  # noqa: PLC0415

    if row is None or row.get("status") != "review" or not row.get("result_doc_id"):
        return None
    key = row["node_key"]
    attempt_id = legacy.active_attempt_id(run_id, key)
    if not attempt_id or sa.is_legacy_attempt(attempt_id) or key in legacy.held_nodes(flow_id):
        return None
    rr = sa.current_review_assignment(attempt_id, sa.RESULT_REVIEW)
    if rr is None or rr["actor"] != sa.AI or rr.get("author") != sa.AI or rr["stage_state"] != sa.DONE \
            or rr.get("verdict") != EXHAUSTED_VERDICT:
        return None
    doc = _doc(row["result_doc_id"])
    if not _stage_holds(rr, row, doc) or doc.get("doc_review_status") != REVISED:
        return None
    if EXHAUSTED_REASON not in approval_wait_reasons_for(run_id, key, attempt_id, sa.basis_of(rr), _b1_stage(rr)):
        return None
    # One request per revision (Q #544 5), judged by ``(doc_id, revision_no)`` only: a revision rejected and put back in
    # review (``rejected`` + submit -> ``revised``) keeps its number — its submission mark (``rejection_count``) and
    # ``submission_judged`` (a resubmission) change, but it is no new revision for this command.
    revision = int(doc.get("revision_no") or 0)
    if store.asked_for_revision(sa.RESULT_REVIEW, doc["doc_id"], revision):
        return None                             # this revision's review was asked already (any attempt, any seq)
    if legacy.revision_reviewed(doc["doc_id"], revision):
        return None                             # a review row answers this revision already (any verdict)
    if review_stage.ai_working(flow_id, key, doc["doc_id"]):
        return None
    return rr, doc


def _command_review_row(flow_id: str, run_row: dict, row: dict) -> None:
    run_id, key = run_row["run_id"], row["node_key"]
    if command_review_due(flow_id, run_id, row) is None:
        return
    attempt_id = legacy.active_attempt_id(run_id, key)
    run_ctx, node = _context(flow_id, run_id, key)
    if run_ctx is None or node is None:
        return
    jobs: list = []
    after: list = []
    with hold_of(HELD, flow_id, run_id, key, attempt_id, COMMAND_REVIEW) as owned:
        if not owned:
            return

        def body():
            if legacy.active_attempt_id(run_id, key) != attempt_id:
                return None                     # the node was opened again
            found = command_review_due(flow_id, run_id, legacy.node_run(run_id, key))
            if found is None:
                return None                     # another command took it first, or the card moved on
            cur, doc = found
            nxt = sa.start_stage(flow_id, run_ctx, node, attempt_id, sa.RESULT_REVIEW, int(cur["seq"]) + 1,
                                 author=cur.get("author") or sa.AI, basis=sa.mark(doc),
                                 author_provider_id=cur.get("author_provider_id"))
            if not isinstance(nxt, dict):
                return None
            return ensure_intent_locked(flow_id, run_ctx, node, nxt, doc, HELD, after)

        nxt = legacy.with_flow_guard(flow_id, body)
        _run_jobs(after)
    if isinstance(nxt, dict):
        drive_review_assignment(flow_id, nxt, HELD, jobs)
    _run_jobs(jobs)


# ── what is left of an assignment (L-2 §2.8.4) ───────────────────────────────

def drive_review_assignment(flow_id: str, a: dict, mode: str, jobs: list, retry: bool = False) -> None:
    """Outside every guard (the caller's hold, if any, is re-entrant). Only what is left, once.

    flowgate.v02.0119 T#3 (T-2 rework): ``retry`` (the run read's reconciliation, RC-1 / RC-4) — a review whose start
    failed (``failed``: ``review_not_started``) or whose setting could not be read (``wait_setup(setting_unreadable)``:
    ``review_result_unknown``) is a system error, tried again by ``retry_review_stage`` up to its limit."""
    a = store.read(a["attempt_id"], a["stage"], a["seq"]) or a
    run_id, key = a["run_id"], a["node_key"]
    row = legacy.node_run(run_id, key)
    doc_id = review_target(a, row)
    if a["actor"] == sa.AI:
        if a["stage_state"] == sa.ACTIVE and not a.get("intent_kind"):
            after: list = []
            run_row, node = _context(flow_id, run_id, key)
            if run_row is None or node is None:
                return
            with hold_of(mode, flow_id, run_id, key, a["attempt_id"], "review_intent") as owned:
                if not owned:
                    return
                a = legacy.with_flow_guard(flow_id, lambda: ensure_intent_locked(flow_id, run_row, node, a,
                                                                                 _doc(doc_id), mode, after))
                _run_jobs(after)
            if not a.get("intent_kind") or a["stage_state"] != sa.ACTIVE:
                return
            # the intent recovered from the ledger: its loop may have ended already (its verdict is consumed below)
        elif a["stage_state"] == sa.FAILED:
            _review_not_started(flow_id, a, _doc(doc_id))
            if retry:
                retry_review_stage(flow_id, a, mode, jobs)     # 0119 T-2: a system error, retried (bounded)
            return
        loop = legacy.loop_of_assignment(a["attempt_id"], a["stage"], a["seq"])
        if loop is None or (loop.get("current_stage") != STOPPED and not a.get("verdict")):
            return
        consume_loop_verdict(flow_id, a, loop, mode, jobs)
        return
    if a["actor"] == sa.NO_REVIEW:
        if not a.get("verdict"):
            auto_approve(flow_id, a, mode, jobs)
        else:
            after_verdict(flow_id, a, mode, jobs)
        return
    if a["actor"] == sa.WAIT_SETUP and a.get("actor_reason") == sa.SETTING_UNREADABLE:
        record_once(flow_id, run_id, key, a["attempt_id"], doc_id, legacy.REVIEW_RESULT_UNKNOWN, sa.basis_of(a),
                    _b1_stage(a))
        if retry:
            retry_review_stage(flow_id, a, mode, jobs)         # 0119 T-2: the setting is read again (bounded)
        return
    if a["actor"] == sa.WAIT_UNAVAILABLE and a["stage"] == sa.INSTRUCTION_REVIEW:
        error = "ai_unavailable:" + str(a.get("cause"))
        if row is not None and row.get("dispatch_error_code") != error:
            legacy.with_flow_guard(flow_id, lambda: legacy.set_dispatch_error(run_id, key, error))


def consume_loop_verdict(flow_id: str, a: dict, loop: dict, mode: str, jobs: list) -> None:
    from flowgate.card_settings import question_wait, review_stage  # noqa: PLC0415

    if a.get("verdict"):
        return after_verdict(flow_id, a, mode, jobs)
    row = legacy.node_run(a["run_id"], a["node_key"])
    doc = _doc(review_target(a, row))
    if loop.get("run_id") and question_wait.open_questions(flow_id, loop["run_id"]) != []:
        return                                              # 0111 T#3: its answers come first
    stop = loop.get("stop_reason")
    if stop == PASSED_STOP and not sa.needs_new_submission(a, doc) and doc and legacy.submission_passed(doc["doc_id"]):
        reviewed = int(doc.get("revision_no") or 0)        # the revision the pass answered (K-6, L-2 §2.8.4)
        if _claim(flow_id, a, sa.PASSED):
            if approve_for(flow_id, a, doc, mode, reviewed) == MOVED:
                take_over_submission(flow_id, a, mode, jobs)    # a newer submission: its own review, no approval
                return
            after_approved(flow_id, a, _doc(doc["doc_id"]), mode, jobs)
    elif stop == PASSED_STOP:
        if _claim(flow_id, a, sa.PASSED):
            take_over_submission(flow_id, a, mode, jobs)
    elif a.get("author") != sa.AI and AUTHOR_REVISION in (stop, loop.get("stop_detail")):
        # the engine's no-rework issues end (0083 WP-4: stop_reason review_count_exhausted, detail awaiting_author_revision)
        if _claim(flow_id, a, sa.ISSUES):                  # rejected by the engine; no B-1 (L-2 §2.8.4)
            take_over_submission(flow_id, a, mode, jobs)
    else:
        if _claim(flow_id, a, sa.NOT_PASSED + str(stop or "unknown")):
            record_once(flow_id, a["run_id"], a["node_key"], a["attempt_id"], (doc or {}).get("doc_id"),
                        review_stage.not_passed(loop), sa.basis_of(a), _b1_stage(a))
            if a.get("author") != sa.AI:
                take_over_submission(flow_id, a, mode, jobs)


MOVED = "submission_moved"                         # the reviewed submission is no longer the document's current one


def approve_for(flow_id: str, a: dict, doc: Optional[dict], mode: str, reviewed: Optional[int] = None):
    """The system's automatic approval of the reviewed document (result: ``legacy.approve`` AUTOMATION; instruction:
    ``review_stage.approve_instruction``), under ``hold_of(mode)``; a refusal is its B-1 (this submission's mark).

    K-6 / L-2 §2.8.4: only the reviewed submission is approved. ``reviewed`` (the revision the verdict answered; the
    assignment's basis when None) is judged again under the hold and handed to the approval as its expected revision,
    which the approval's own transaction checks before and after its write. A document changed since (a person's
    edit, a resubmission) is not approved: ``MOVED`` — the caller hands the newer submission to its own review."""
    from flowgate.card_settings import review_stage  # noqa: PLC0415

    if not doc:
        return False
    if doc.get("doc_review_status") in APPROVED and a["stage"] == sa.INSTRUCTION_REVIEW:
        return True
    run_row, node = _context(flow_id, a["run_id"], a["node_key"])
    if run_row is None or node is None:
        return False
    issuer = sa.issued_to_of(node, run_row)
    basis = sa.basis_of(a) or {}
    expected = int(reviewed if reviewed is not None else basis.get("revision_no", doc.get("revision_no") or 0))
    with hold_of(mode, flow_id, a["run_id"], a["node_key"], a["attempt_id"],
                 "instruction_approval" if a["stage"] == sa.INSTRUCTION_REVIEW else "result_approval") as owned:
        if not owned:
            return False
        current = _doc(doc["doc_id"])
        if current is None:
            return MOVED
        if current.get("doc_review_status") in APPROVED and a["stage"] == sa.INSTRUCTION_REVIEW:
            return True
        # flowgate.v02.0119 T#2 (T-1): a result approved before (0038-T T#3 A-4, a report taken already approved) is no
        # node completion by itself — this attempt's pass completes it — and is judged as any other result first: the
        # reviewed revision must still be the document's and the submission the one the assignment reviewed.
        if int(current.get("revision_no") or 0) != expected \
                or (a.get("author") != sa.AI and sa.needs_new_submission(a, current)):
            return MOVED
        submission = sa.mark(current)
        if a["stage"] == sa.INSTRUCTION_REVIEW:
            done = review_stage.approve_instruction(flow_id, node, current["doc_id"], issuer, mark=submission,
                                                    attempt_id=a["attempt_id"], expected_revision=expected)
        else:
            # ``issuer`` (the token recipient: a person card's assignee) is the authority's principal only; the record's
            # requested_by is the run's started_by and its author kind the assignment's (L-2: ``author``).
            done = legacy.approve(flow_id, current["doc_id"], issuer, legacy.AUTOMATION, node_key=a["node_key"],
                                  attempt_id=a["attempt_id"], mark=submission, expected_revision=expected,
                                  author=a.get("author"))
        return MOVED if done == legacy.REVISION_MOVED else done


def auto_approve(flow_id: str, a: dict, mode: str, jobs: list) -> None:
    row = legacy.node_run(a["run_id"], a["node_key"])
    doc = _doc(review_target(a, row))
    if _claim(flow_id, a, sa.AUTO_APPROVED):
        if approve_for(flow_id, a, doc, mode) == MOVED:
            take_over_submission(flow_id, a, mode, jobs)      # saved again meanwhile: the newer submission's own
            return
        after_approved(flow_id, a, _doc((doc or {}).get("doc_id")), mode, jobs)


def after_approved(flow_id: str, a: dict, doc: Optional[dict], mode: str, jobs: list) -> None:
    """An approved instruction's next step: the result stage (a repeated call does nothing). A result's approval moves
    the node through the binder's contact — 0038-T T#3 A-4: a report approved before the attempt took it makes no new
    approval (no contact), so once this attempt's review passed it the binder's reconciliation moves the node."""
    if a["stage"] == sa.RESULT_REVIEW and doc and doc.get("doc_review_status") in APPROVED \
            and legacy.taken_judged(a["run_id"], a["node_key"], a["attempt_id"], doc["doc_id"]):
        jobs.append(lambda: legacy.reconcile_results(flow_id))
        return
    if a["stage"] != sa.INSTRUCTION_REVIEW or not doc:
        return
    if doc.get("doc_review_status") not in APPROVED:
        return              # 0119 T-2 rework: its automatic approval failed — a system error, retried (``after_verdict``)
    row = legacy.node_run(a["run_id"], a["node_key"])
    if row is None or row["status"] != "open" or row.get("stage") != sa.INSTRUCTION:
        return
    run_row, node = _context(flow_id, a["run_id"], a["node_key"])
    if run_row is None or node is None:
        return
    with hold_of(mode, flow_id, a["run_id"], a["node_key"], a["attempt_id"], "instruction_result_stage") as owned:
        if not owned:
            return
        legacy.start_result_stage(flow_id, a["run_id"], a["node_key"], doc["doc_id"], sa.issued_to_of(node, run_row),
                                  a["attempt_id"], defer=jobs, idle_ok=(mode == HELD))


def after_verdict(flow_id: str, a: dict, mode: str, jobs: list) -> None:
    """The verdict was consumed already: only the step left after it (a lost approval / result stage, a takeover).
    flowgate.v02.0119 T#3 (T-2): an approval its B-1 tells failed on the automatic path (a system error: ``approval_refused``,
    ``issuer_missing``, …) is tried again while ``card_intervention.reasons.retry_due`` allows it — each failure records
    its reason again (``legacy.approve``), so the retries end at ``SYSTEM_ERROR_RETRY_LIMIT`` records; it was tried once
    before. A needs_intervention reason is never tried again.

    The records counted are those of the submission the approval itself records them for (``approve_for``: the mark of
    the document as it is now, ``sa.mark``) — not the review assignment's basis: an AI result reworked inside its loop
    (a new revision, a rejection counted) keeps its assignment (``needs_new_submission`` is False for an AI author), so
    the basis would find none of its failures and the retries would never end (0119 T-2 rework, review P1). Another
    submission's or attempt's records are not this one's (``approval_wait_reasons_for``: its mark and attempt), nor those
    of a recovery cycle a command's re-request ended (``command_cycle``, flowgate.v02.0124 T#1 rework)."""
    from flowgate.card_intervention import reasons as intervention  # noqa: PLC0415

    row = legacy.node_run(a["run_id"], a["node_key"])
    doc = _doc(review_target(a, row))
    verdict = a.get("verdict")
    if verdict in (sa.PASSED, sa.AUTO_APPROVED) and doc and not sa.needs_new_submission(a, doc):
        if doc.get("doc_review_status") not in APPROVED and (verdict == sa.AUTO_APPROVED or legacy.submission_passed(
                doc["doc_id"])) and intervention.retry_due(approval_wait_reasons_for(
                    a["run_id"], a["node_key"], a["attempt_id"], sa.mark(doc), _b1_stage(a))):
            reviewed = None if verdict == sa.AUTO_APPROVED else int(doc.get("revision_no") or 0)
            if approve_for(flow_id, a, doc, mode, reviewed) == MOVED:
                take_over_submission(flow_id, a, mode, jobs)
                return
        after_approved(flow_id, a, _doc(doc["doc_id"]), mode, jobs)
    elif a.get("author") != sa.AI:
        take_over_submission(flow_id, a, mode, jobs)


def take_over_submission(flow_id: str, a: dict, mode: str, jobs: list) -> None:
    """L-2 §2.8.4: a person's (or unknown author's) newer submission made while the loop worked takes its own review."""
    from flowgate.card_settings import manual_stage  # noqa: PLC0415

    if a.get("author") == sa.AI:
        return
    row = legacy.node_run(a["run_id"], a["node_key"])
    if row is None:
        return
    if a["stage"] == sa.RESULT_REVIEW and row["status"] != "review":
        return
    if a["stage"] == sa.INSTRUCTION_REVIEW and (row["status"] != "open" or row.get("stage") != sa.INSTRUCTION):
        return
    doc = _doc(review_target(a, row))
    if not doc or doc.get("doc_review_status") in APPROVED or doc.get("doc_review_status") == "rejected":
        return
    if not sa.needs_new_submission(a, doc):
        return
    if a["stage"] == sa.RESULT_REVIEW:
        manual_stage.follow_person_result(flow_id, a["run_id"], a["node_key"], doc["doc_id"], a["attempt_id"])
    else:
        manual_stage.proceed_written_instruction_held(flow_id, a["run_id"], a["node_key"], a["attempt_id"],
                                                      doc["doc_id"], legacy.written_by(doc["doc_id"]), jobs, mode)


# ── run-end branches of an assigned attempt (L-2 §2.9) ───────────────────────

def after_result_work(flow_id: str, run_id: str, node: dict, doc_id: str, attempt_id: str, author: dict,
                      mode: str, reconcile: bool = False) -> None:
    """``_after_work`` of an assigned attempt: the result assignment done and the result's review assignment opened
    with its intent in one guard, then what is left of it."""
    key = node["node_key"]
    jobs: list = []
    after: list = []
    try:
        with hold_of(mode, flow_id, run_id, key, attempt_id, "review_start") as owned:
            if not owned:
                return

            def body():
                run_row = legacy.run_by_id(run_id)
                row = legacy.node_run(run_id, key)
                if run_row is None or row is None or row["status"] != "review" or row.get("result_doc_id") != doc_id:
                    return STAGE_MOVED
                ra = store.read(attempt_id, sa.RESULT, 0)
                if ra is not None and ra["stage_state"] in (sa.ACTIVE, sa.WAITING):
                    sa.move(ra, sa.DONE)
                return open_review_stage_locked(flow_id, run_row, node, attempt_id, sa.RESULT_REVIEW, _doc(doc_id),
                                                author.get("author") or "unknown", author.get("provider_id"), mode,
                                                after)

            a = legacy.with_flow_guard(flow_id, body)
            _run_jobs(after)
            if isinstance(a, dict):
                drive_review_assignment(flow_id, a, mode, jobs)
    except sa.AssignmentUnreadable:
        if reconcile:
            raise                                   # the reconciliation skips the node (its caller logs)
        _log.warning("result review not started flow=%s node=%s: assignment unreadable", flow_id, key, exc_info=True)
        jobs.clear()
        _record_unreadable(flow_id, run_id, key, attempt_id, doc_id, sa.RESULT)
        return
    _run_jobs(jobs)


def _record_unreadable(flow_id: str, run_id: str, key: str, attempt_id: Optional[str], doc_id: Optional[str],
                       stage: str) -> None:
    """T §4.6: a post-commit step whose assignment could not be read approved and started nothing; its B-1
    ``assignment_unreadable`` is recorded once per submission (its mark, attempt and stage). Never raises."""
    try:
        doc = _doc(doc_id)
        if doc is not None:
            record_once(flow_id, run_id, key, attempt_id, doc["doc_id"], ASSIGNMENT_UNREADABLE, sa.mark(doc),
                        b1_stage_of(stage))
    except Exception:  # noqa: BLE001
        _log.warning("assignment_unreadable not recorded flow=%s node=%s", flow_id, key, exc_info=True)


def result_run_not_clean(flow_id: str, run_id: str, key: str, attempt_id: str, doc_id: str, reason: str) -> None:
    """A result writer that did not end on its own: its assignment failed (``invoke_failed``) and its B-1 recorded."""
    ra = store.read(attempt_id, sa.RESULT, 0)
    if ra is not None:
        legacy.with_flow_guard(flow_id, lambda: sa.fail(store.read(attempt_id, sa.RESULT, 0) or ra, sa.INVOKE_FAILED))
    record_once(flow_id, run_id, key, attempt_id, doc_id, reason, sa.mark(_doc(doc_id)))


def instruction_failed(flow_id: str, attempt_id: Optional[str], cause: str = sa.INVOKE_FAILED) -> None:
    """The instruction stage's AI failed: its assignment failed (the node's dispatch error stays the current one)."""
    if not attempt_id or sa.is_legacy_attempt(attempt_id):
        return
    ia = store.read(attempt_id, sa.INSTRUCTION, 0)
    if ia is not None and ia["actor"] == sa.AI:
        legacy.with_flow_guard(flow_id, lambda: sa.fail(store.read(attempt_id, sa.INSTRUCTION, 0) or ia, cause))


def after_loop_end(flow_id: str, run: dict, ref: tuple, answered: bool) -> None:
    """``_after_review`` of an assigned loop: its assignment (found by the ledger reference) consumes the verdict."""
    attempt_id, stage, seq = ref
    jobs: list = []
    mode = HELD if answered else AUTO
    try:
        a = store.read(attempt_id, stage, seq)
        if a is None:
            return
        # the loop the run-end contact names (its ledger reference gave ``ref``): its verdict, even before the ledger
        # records the loop's AI run; any other state is what ``drive_review_assignment`` finishes
        ended = legacy.review_loop_row(run["run_id"]) if run.get("run_id") else None
        if a["actor"] == sa.AI and a.get("intent_kind") and ended and ended.get("current_stage") == STOPPED:
            consume_loop_verdict(flow_id, a, {**ended, "run_id": run["run_id"]}, mode, jobs)
        else:
            drive_review_assignment(flow_id, a, mode, jobs)
    except sa.AssignmentUnreadable:
        # T §4.6: the loop's verdict is neither consumed nor approved; its B-1 once for the reviewed submission
        _log.warning("review loop end not taken flow=%s attempt=%s: assignment unreadable", flow_id, attempt_id,
                     exc_info=True)
        jobs.clear()
        node = legacy.attempt_node(attempt_id)
        row = legacy.node_run(*node) if node else None
        if row is not None:
            doc_id = row.get("result_doc_id") if stage == sa.RESULT_REVIEW else row.get("instruction_doc_id")
            _record_unreadable(flow_id, row["run_id"], row["node_key"], attempt_id, doc_id, stage)
        return
    _run_jobs(jobs)


# ── the run read's reconciliation of an assigned attempt (L-2 §2.11) ─────────

def reconcile_row(flow_id: str, run_row: dict, row: dict) -> None:
    """RC-1 ~ RC-8 for one node of an assigned attempt, in ``HELD``. A read error skips the node (the caller logs)."""
    from flowgate.card_settings import manual_stage, review_stage  # noqa: PLC0415

    run_id, key = run_row["run_id"], row["node_key"]
    attempt_id = legacy.active_attempt_id(run_id, key)
    if review_stage._handoff_pending(flow_id, key) or legacy.node_ai_run_active(flow_id, key):
        return
    jobs: list = []
    if row["status"] == "review" and row.get("result_doc_id"):
        doc = _doc(row["result_doc_id"])
        rr = sa.current_review_assignment(attempt_id, sa.RESULT_REVIEW)
        ra = store.read(attempt_id, sa.RESULT, 0)
        if rr is not None and not sa.needs_new_submission(rr, doc):
            drive_review_assignment(flow_id, rr, HELD, jobs, retry=True)                       # RC-1
        elif rr is not None and review_loop_active(rr):
            return
        elif ra is not None and ra["actor"] != sa.PERSON and ra["stage_state"] == sa.DONE and rr is None \
                and not legacy.result_ai_run(flow_id, run_id, key, row["result_doc_id"]):
            _reconcile_taken_result(flow_id, run_row, row, attempt_id)                         # 0038-T A-4
        elif ra is not None and ra["actor"] == sa.AI:
            _reconcile_lost_result_end(flow_id, run_row, row, attempt_id)                      # RC-2
        elif ra is not None and ra["actor"] == sa.PERSON and ra["stage_state"] != sa.FAILED:
            manual_stage.follow_person_result(flow_id, run_id, key, row["result_doc_id"], attempt_id,
                                              trigger=manual_stage.RECONCILE)                  # RC-3
        elif ra is not None and doc and (doc.get("doc_review_status") not in APPROVED):
            record_once(flow_id, run_id, key, attempt_id, doc["doc_id"], STAGE_NOT_PERSON, sa.mark(doc))
    elif row["status"] == "open" and row.get("stage") == sa.INSTRUCTION:
        ia = store.read(attempt_id, sa.INSTRUCTION, 0)
        ir = sa.current_review_assignment(attempt_id, sa.INSTRUCTION_REVIEW)
        ins = _doc(row.get("instruction_doc_id"))
        if ir is not None and ins and ir.get("basis_doc_id") == ins["doc_id"] and not sa.needs_new_submission(ir, ins):
            drive_review_assignment(flow_id, ir, HELD, jobs, retry=True)                       # RC-4
        elif ir is not None and review_loop_active(ir):
            return
        elif ia is None or ia["stage_state"] == sa.FAILED:
            pass                                    # E-40 (T-1 failed, reused or not): only a new attempt recovers it
        elif ia.get("reused_doc_id"):
            manual_stage.proceed_written_instruction(flow_id, run_id, key, attempt_id, ia["reused_doc_id"], None,
                                                     HELD, trigger=manual_stage.RECONCILE)     # RC-5
        elif ia["actor"] == sa.PERSON:
            hint = manual_stage.required_instruction(flow_id, run_row, key, row)
            if hint:
                manual_stage.follow_person_instruction(flow_id, run_id, key, hint, attempt_id,
                                                       manual_stage.RECONCILE)                 # RC-6
        elif ia["actor"] == sa.AI:
            _reconcile_lost_instruction_end(flow_id, run_row, row, attempt_id)                 # RC-7
    elif row["status"] == "open" and row.get("stage") == sa.RESULT:
        ra = store.read(attempt_id, sa.RESULT, 0)
        if ra is not None and ra["actor"] == sa.PERSON and ra["stage_state"] == sa.WAITING:
            manual_stage.reconcile_saved_report(flow_id, run_row, row, attempt_id)              # RC-8
    _run_jobs(jobs)


def _reconcile_due(run_id: str, key: str, attempt_id: Optional[str], doc: dict) -> bool:
    """flowgate.v02.0119 T#3 (T-2 rework, review P1): may the lost review opening of the submission ``doc`` be taken
    now? Not when a needs_intervention reason is recorded for it (``run_not_clean``, ``review_not_passed``, ...): the
    card waits for a memo rework / re-review, an explicit step / full command's re-review of a spent count
    (``command_review``, flowgate.v02.0125 T#1) or the Time Machine. A system error recorded (``assignment_unreadable`` of
    a post-commit step, ...) no longer stops it for good, as any record did before. Its tries are bounded apart from
    the records: the opening either makes the review assignment (the next read drives it, RC-1, where a review that
    does not start is counted, ``review_start_tries``) or fails on a read and writes nothing (the node is skipped)."""
    from flowgate.card_intervention import reasons as intervention  # noqa: PLC0415

    recorded = approval_wait_reasons_for(run_id, key, attempt_id, sa.mark(doc), "result")
    return not any(not intervention.is_system_error(r) for r in recorded)


def _reconcile_lost_result_end(flow_id: str, run_row: dict, row: dict, attempt_id: str) -> None:
    from flowgate.card_settings import review_stage  # noqa: PLC0415

    run_id, key, doc_id = run_row["run_id"], row["node_key"], row["result_doc_id"]
    doc = _doc(doc_id)
    if not doc or doc.get("doc_review_status") in APPROVED:
        return
    if not _reconcile_due(run_id, key, attempt_id, doc):
        return
    work = legacy.result_ai_run(flow_id, run_id, key, doc_id)
    if work is None or not review_stage.ended_cleanly(work) or legacy.open_questions_of_ai_run(flow_id, work["run_id"]):
        return
    node = legacy.run_node_row(flow_id, run_row, key)
    if node is None:
        return
    after_result_work(flow_id, run_id, node, doc_id, attempt_id, _ai_author(work, doc_id), HELD, reconcile=True)


def _reconcile_taken_result(flow_id: str, run_row: dict, row: dict, attempt_id: str) -> None:
    """0038-T T#3 A-4: a result stage of an AI or a wait that took its written report (its assignment done, no AI run
    wrote the bound result) whose review opening after the commit was lost: opened now, as at the taking — a report
    approved before the attempt took it as well (its old approval is no review of this attempt)."""
    run_id, key, doc_id = run_row["run_id"], row["node_key"], row["result_doc_id"]
    doc = _doc(doc_id)
    if not doc:
        return
    if doc.get("doc_review_status") in APPROVED and not legacy.taken_judged(run_id, key, attempt_id, doc_id):
        return
    if not _reconcile_due(run_id, key, attempt_id, doc):
        return
    node = legacy.run_node_row(flow_id, run_row, key)
    if node is None:
        return
    after_result_work(flow_id, run_id, node, doc_id, attempt_id, legacy.written_by(doc_id), HELD, reconcile=True)


def taken_review_pending(flow_id: str, run_id: str, key: str, doc_id: str) -> bool:
    """``card_settings_ports.result_review_pending`` (the binder, inside its guard): the node's result ``doc_id`` is a
    report approved before the node's current attempt took it (``legacy.taken_judged``) and this attempt's result
    review of it has not passed nor approved it automatically yet — its old approval completes nothing. Anything
    that cannot be read is pending (the node waits; never completed on a judgement that is not this attempt's)."""
    try:
        attempt_id = legacy.active_attempt_id(run_id, key)
        if not attempt_id or sa.is_legacy_attempt(attempt_id) or not legacy.taken_judged(run_id, key, attempt_id,
                                                                                        doc_id):
            return False
        rr = sa.current_review_assignment(attempt_id, sa.RESULT_REVIEW)
        return not (rr is not None and rr.get("basis_doc_id") == doc_id
                    and not sa.needs_new_submission(rr, _doc(doc_id))
                    and rr.get("verdict") in (sa.PASSED, sa.AUTO_APPROVED))
    except Exception:  # noqa: BLE001 — not known: the old approval completes nothing
        _log.warning("taken result review not read flow=%s node=%s", flow_id, key, exc_info=True)
        return True


def _reconcile_lost_instruction_end(flow_id: str, run_row: dict, row: dict, attempt_id: str) -> None:
    from flowgate.card_settings import manual_stage, review_stage  # noqa: PLC0415

    run_id, key = run_row["run_id"], row["node_key"]
    work = legacy.stage_ai_run(flow_id, run_id, key)
    if work is None or not review_stage.ended_cleanly(work) or legacy.attempt_of_ai_run(work) != attempt_id:
        return
    if legacy.open_questions_of_ai_run(flow_id, work["run_id"]):
        return
    node = legacy.run_node_row(flow_id, run_row, key)
    if node is None:
        return
    doc_id = review_stage.written_instruction(flow_id, node, row)
    if doc_id is None:
        return
    manual_stage.proceed_written_instruction(flow_id, run_id, key, attempt_id, doc_id, _ai_author(work, doc_id), HELD,
                                             trigger=manual_stage.RECONCILE)


def _ai_author(work: dict, doc_id: str) -> dict:
    """L-2 §2.4.3 / T §4.2: who wrote the document's current revision at an AI stage's end. The ended AI run is evidence
    only through the current revision's own write record — its (or its loop's rework) token consumed for that revision,
    with nothing written after it (``written_by``). Without a write uniquely tied to the current revision (a missing
    record, a failed read, a person's later edit, an order that cannot be told) the author is not the run's AI:
    ``unknown`` (one round, no rework) or the person the record proves."""
    found = legacy.written_by(doc_id)
    if found.get("author") == "ai" and work.get("provider_id") and found.get("provider_id") != work.get("provider_id"):
        _log.info("written by another AI than the ended run doc=%s run=%s", doc_id, work.get("run_id"))
    return found
