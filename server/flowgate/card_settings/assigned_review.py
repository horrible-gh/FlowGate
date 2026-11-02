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

``mode`` is the hold the step is judged in (§2.8.5): ``AUTO`` — at an AI run's end (``attempt_hold``, an idle run
refused); ``HELD`` — a person's step, an answered question, an opening's follow-up or a reconciliation
(``person_hold``: an idle run taken, its request made as an explicit one).
"""
from __future__ import annotations

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


def approval_waits_for(run_id: str, key: str, attempt_id: Optional[str], submission: Optional[dict],
                       stage: str, reason: Optional[str] = None) -> bool:
    """A B-1 of this submission (its mark) is recorded (L-2 §2.7.2: a mark-less B-1 is a pre-feature record)."""
    if not submission:
        return False
    for row in legacy.approval_wait_rows(run_id, key, attempt_id):
        if row.get("submission_mark") == submission and row.get("stage", "result") == stage \
                and (reason is None or row.get("reason") == reason):
            return True
    return False


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
    """After the commit: the requested loop is started (``start_review_loop`` on its ledger request)."""
    failure: dict = {}
    started = legacy.start_review_loop(legacy.project_of(flow_id), flow_id, doc_id, issuer, loop,
                                       lambda last: fields.review_basis_note(criteria, last), a["node_key"],
                                       graph_exec_id=exec_id, attempt_id=a["attempt_id"], failure=failure)
    if started is None:
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


# ── what is left of an assignment (L-2 §2.8.4) ───────────────────────────────

def drive_review_assignment(flow_id: str, a: dict, mode: str, jobs: list) -> None:
    """Outside every guard (the caller's hold, if any, is re-entrant). Only what is left, once."""
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
    """The verdict was consumed already: only the step left after it (a lost approval / result stage, a takeover)."""
    row = legacy.node_run(a["run_id"], a["node_key"])
    doc = _doc(review_target(a, row))
    verdict = a.get("verdict")
    if verdict in (sa.PASSED, sa.AUTO_APPROVED) and doc and not sa.needs_new_submission(a, doc):
        if doc.get("doc_review_status") not in APPROVED and (verdict == sa.AUTO_APPROVED or legacy.submission_passed(
                doc["doc_id"])) and not approval_waits_for(a["run_id"], a["node_key"], a["attempt_id"],
                                                             sa.basis_of(a), _b1_stage(a)):
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
            drive_review_assignment(flow_id, rr, HELD, jobs)                                   # RC-1
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
            drive_review_assignment(flow_id, ir, HELD, jobs)                                   # RC-4
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


def _reconcile_lost_result_end(flow_id: str, run_row: dict, row: dict, attempt_id: str) -> None:
    from flowgate.card_settings import review_stage  # noqa: PLC0415

    run_id, key, doc_id = run_row["run_id"], row["node_key"], row["result_doc_id"]
    doc = _doc(doc_id)
    if not doc or doc.get("doc_review_status") in APPROVED:
        return
    if approval_waits_for(run_id, key, attempt_id, sa.mark(doc), "result"):
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
    if approval_waits_for(run_id, key, attempt_id, sa.mark(doc), "result"):
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
