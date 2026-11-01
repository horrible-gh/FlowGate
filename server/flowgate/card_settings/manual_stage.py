"""A person's stages of an assigned attempt (flowgate.v02.0113 T#3, L-2 §2.7).

A stage assigned to a person (``person``: not used, no AI, a former person's card) completes by what the person saved —
never by a value typed and not saved — and moves on with no person's [승인]:

- an instruction saved (``follow_person_instruction``: registered with the node's token, its line written, its body
  saved — ``instruction_saved``) → its review assignment (no review: the system approves it) → the result stage;
- a result bound (``person_result``, the binder's port) → its review assignment → the system's approval.

``manual_judgeable`` (C1 ~ C5) runs under ``person_hold`` (Stop / Time Machine judged again): the run open, the current
attempt, the stage assigned to a person and open, no open question, and the submission not judged yet — a submission
whose review assignment exists only finishes what that assignment left (``EXISTING`` → ``drive_review_assignment``).
Every guard write (the stage done, the review assignment and its intent) is its own Flow guard inside the hold.
"""
from __future__ import annotations

import logging
from typing import Optional

from flowgate.card_documents import lines as card_lines
from flowgate.card_documents import slots as card_slots
from flowgate.card_settings import assigned_review as ar
from flowgate.card_settings import legacy_adapter as legacy
from flowgate.card_settings import report_baseline
from flowgate.card_settings import stage_assignment as sa

_log = logging.getLogger(__name__)
store = sa.store                                   # reads raise ``AssignmentUnreadable`` (L-2 §2.14)

YES = "yes"
RUN_CLOSED, OLD_ATTEMPT, LEGACY, STAGE_NOT_STARTED, STAGE_NOT_PERSON = (
    "run_closed", "old_attempt", "legacy", "stage_not_started", "stage_not_person")
STAGE_CLOSED, QUESTIONS, EXISTING, TAKE_OVER_LATER, JUDGED = (
    "stage_closed", "questions", "existing", "take_over_later", "judged")
STAGE_FAILED = "stage_failed"                      # a failed person stage (T-1 / T-3): only a new attempt recovers it
OK, MISSING_REPORT, MISSING_INSTRUCTION = "ok", "missing_report", "missing_instruction"
RECONCILE = "reconcile"                            # the run read's trigger: an unreadable assignment skips the node
INSTRUCTION_NOT_APPROVED = ar.INSTRUCTION_NOT_APPROVED
APPROVED = ar.APPROVED


# ── judgement (L-2 §2.7.2 / §2.7.3) ───────────────────────────────────────────

def duplicate(stage: str, attempt_id: str, run_id: str, key: str, doc: Optional[dict]):
    """C5: (verdict, the review assignment) — ``JUDGED`` / ``EXISTING`` / ``TAKE_OVER_LATER`` / None. 0038-T T#3 A-4: a
    result approved before the attempt took it (``legacy.taken_judged``) is not judged by this attempt — its review
    is opened once (``EXISTING`` after that), as for any submission."""
    if not doc:
        return None, None
    if doc.get("doc_review_status") in APPROVED and not (
            stage == sa.RESULT and legacy.taken_judged(run_id, key, attempt_id, doc["doc_id"])):
        return JUDGED, None
    rs = sa.INSTRUCTION_REVIEW if stage == sa.INSTRUCTION else sa.RESULT_REVIEW
    cur = sa.current_review_assignment(attempt_id, rs)
    if cur is not None and not sa.needs_new_submission(cur, doc):
        return EXISTING, cur
    if cur is not None and ar.review_loop_active(cur):
        return TAKE_OVER_LATER, cur
    b1 = "instruction" if stage == sa.INSTRUCTION else "result"
    if ar.approval_waits_for(run_id, key, attempt_id, sa.mark(doc), b1):
        return JUDGED, None
    return None, None


def manual_judgeable(flow_id: str, run_id: str, key: str, attempt_id: Optional[str], stage: str,
                     doc: Optional[dict]):
    """C1 ~ C5: (``YES`` or the reason, the review assignment for ``EXISTING``). Records nothing."""
    run = legacy.active_run(flow_id)
    if run is None or run["run_id"] != run_id or not legacy.person_stage_open(flow_id, run):
        return RUN_CLOSED, None
    if not attempt_id or attempt_id != legacy.active_attempt_id(run_id, key):
        return OLD_ATTEMPT, None
    if sa.is_legacy_attempt(attempt_id):
        return LEGACY, None
    a = store.read(attempt_id, stage, 0)
    if a is None:
        return STAGE_NOT_STARTED, None
    if a["actor"] != sa.PERSON:
        return STAGE_NOT_PERSON, a
    if a["stage_state"] == sa.FAILED:
        return STAGE_FAILED, a                     # E-15a / E-15b / E-40: the explicit command's new attempt only
    row = legacy.node_run(run_id, key)
    if row is None:
        return STAGE_CLOSED, None
    if stage == sa.INSTRUCTION and not (row["status"] == "open" and row.get("stage") == sa.INSTRUCTION):
        return STAGE_CLOSED, None
    if stage == sa.RESULT and not (row["status"] == "review" and doc and row.get("result_doc_id") == doc["doc_id"]):
        return STAGE_CLOSED, None
    if legacy.open_questions(flow_id, run_id, key):
        return QUESTIONS, None
    verdict, cur = duplicate(stage, attempt_id, run_id, key, doc)
    if verdict is not None:
        return verdict, cur
    return YES, None


def _invalidated(flow_id: str, key: str) -> set:
    """The documents of the node's invalidated attempts (L-2 §2.7.2 C2). A failed read raises: not knowing which
    documents are an old attempt's judges nothing (never an empty set, which would take one as this attempt's)."""
    return legacy.invalidated_documents(flow_id, key)


def required_instruction(flow_id: str, run_row: dict, key: str, row: dict) -> Optional[str]:
    """L-2 §2.7.3: the current attempt's written instruction (TEST: TS) — the recorded one, the line's, then the
    latest one the node's tokens registered — or None (still not written)."""
    attempt_id = legacy.active_attempt_id(run_row["run_id"], key)
    plan = sa.stage_plan(attempt_id) or {}
    wanted = str(plan.get("instruction_type") or "").upper()
    if not wanted:
        return None
    node = legacy.run_node_row(flow_id, run_row, key) or {"node_key": key}
    line = card_lines.line_document(flow_id, key, card_slots.INSTRUCTION, node.get("node_type"),
                                    node.get("doc_type")) or {}
    candidates = [row.get("instruction_doc_id"), line.get("doc_id")]
    candidates += [d["doc_id"] for d in legacy.registered_instructions(flow_id, run_row["run_id"], key, wanted)]
    gone = _invalidated(flow_id, key)
    for doc_id in dict.fromkeys(c for c in candidates if c):
        if doc_id in gone:
            continue
        doc = legacy.document_row(doc_id)
        if not doc or str(doc.get("type_code") or "").upper() != wanted or doc.get("status") == "cancelled":
            continue
        if card_lines.has_body(doc):
            return doc_id
    return None


def required_result(flow_id: str, attempt_id: str, node: dict, row: dict, doc: Optional[dict]) -> str:
    plan = sa.stage_plan(attempt_id) or {}
    if not sa.result_has_body(sa.kind_of(node), doc):
        return MISSING_REPORT
    if plan.get("instruction_type") and report_baseline.is_baseline(attempt_id, doc):
        # 0038-T T#3 A-2: the report of before the instruction is no result (a baseline that cannot be read raises)
        return MISSING_REPORT
    if plan.get("instruction_type"):
        ins = legacy.document_row(row.get("instruction_doc_id"))
        if not ins or not card_lines.has_body(ins):
            return MISSING_INSTRUCTION
        if ins.get("doc_review_status") not in APPROVED:
            return INSTRUCTION_NOT_APPROVED
    return OK


# ── the instruction stage (L-2 §2.7.4) ────────────────────────────────────────

def _handoff(flow_id: str, key: str, doc_id: str):
    from flowgate.card_settings import review_stage  # noqa: PLC0415

    review_stage._person_job(flow_id, "", key, doc_id, None)            # marks the node as worked on (0061 T#2)
    return review_stage.PERSON_HANDOFF + doc_id


def _quietly(step) -> None:
    try:
        step()
    except Exception:  # noqa: BLE001 — a record of what was not done never fails its caller
        _log.warning("card stage record not written", exc_info=True)


def _release(flow_id: str, owner: str) -> None:
    from flowgate.card_settings import review_stage  # noqa: PLC0415

    if review_stage._clear_handoffs(owner):
        legacy.settle_graph_run(flow_id)


def unreadable(flow_id: str, run_id: str, key: str, attempt_id: Optional[str], doc_id: Optional[str],
               stage: str) -> None:
    """T §4.6 / L-2 §2.14: a post-commit judgement whose assignment could not be read approved and started nothing; its
    B-1 ``assignment_unreadable`` (the submission's mark, the attempt and the stage) is recorded once per submission."""
    doc = legacy.document_row(doc_id) if doc_id else None
    if doc is None:
        return
    ar.record_once(flow_id, run_id, key, attempt_id, doc["doc_id"], ar.ASSIGNMENT_UNREADABLE, sa.mark(doc),
                   ar.b1_stage_of(stage))


def follow_person_instruction(flow_id: str, run_id: str, key: str, doc_hint: Optional[str], attempt_id: Optional[str],
                              trigger: str) -> None:
    """A person's saved instruction judged (REGISTERED / LINE_WRITE / CONTENT_SAVED / RECONCILE: the same answer).
    An unreadable assignment judges nothing: a contact records its B-1 once, the reconciliation only skips the node."""
    owner = _handoff(flow_id, key, doc_hint or key)
    jobs: list = []
    try:
        with legacy.person_hold(flow_id, run_id, key, attempt_id, "manual_instruction") as owned:
            if not owned:
                return
            run_row = legacy.active_run(flow_id)
            row = legacy.node_run(run_id, key)
            if run_row is None or row is None:
                return
            doc_id = required_instruction(flow_id, run_row, key, row)
            verdict, cur = manual_judgeable(flow_id, run_id, key, attempt_id, sa.INSTRUCTION,
                                            legacy.document_row(doc_id))
            if verdict == EXISTING:
                ar.drive_review_assignment(flow_id, cur, ar.HELD, jobs)
                return
            if verdict != YES or doc_id is None:
                _log.debug("person instruction not judged flow=%s node=%s trigger=%s: %s", flow_id, key, trigger,
                           verdict)
                return
            proceed_written_instruction_held(flow_id, run_id, key, attempt_id, doc_id,
                                             {"author": "person", "provider_id": None}, jobs, ar.HELD)
    except sa.AssignmentUnreadable:
        _log.warning("person instruction not judged flow=%s node=%s: assignment unreadable", flow_id, key, exc_info=True)
        jobs.clear()
        if trigger != RECONCILE:
            _quietly(lambda: unreadable(flow_id, run_id, key, attempt_id, doc_hint, sa.INSTRUCTION))
    except Exception:  # noqa: BLE001 — a post-commit step; the node stays as it is for the next read
        _log.warning("person instruction stage failed flow=%s node=%s", flow_id, key, exc_info=True)
    finally:
        ar._run_jobs(jobs)
        _release(flow_id, owner)


def proceed_written_instruction(flow_id: str, run_id: str, key: str, attempt_id: Optional[str], doc_id: str,
                                author: Optional[dict], mode: str, trigger: Optional[str] = None) -> None:
    """A written instruction (reused at the opening, found written at the AI start, the AI stage's end, the
    reconciliation: ``trigger`` RECONCILE) goes to its review assignment, under ``hold_of(mode)``. An unreadable
    assignment approves and starts nothing (B-1 once, none for the reconciliation, which skips the node)."""
    jobs: list = []
    try:
        with ar.hold_of(mode, flow_id, run_id, key, attempt_id, "instruction_proceed") as owned:
            if owned:
                proceed_written_instruction_held(flow_id, run_id, key, attempt_id, doc_id, author, jobs, mode)
    except sa.AssignmentUnreadable:
        jobs.clear()
        if trigger == RECONCILE:
            raise
        _log.warning("written instruction not taken flow=%s node=%s: assignment unreadable", flow_id, key,
                     exc_info=True)
        _quietly(lambda: unreadable(flow_id, run_id, key, attempt_id, doc_id, sa.INSTRUCTION))
        return
    ar._run_jobs(jobs)


def proceed_written_instruction_held(flow_id: str, run_id: str, key: str, attempt_id: Optional[str], doc_id: str,
                                     author: Optional[dict], jobs: list, mode: str = ar.HELD) -> None:
    author = author or legacy.written_by(doc_id)
    after: list = []

    def body():
        row = legacy.open_row(run_id, key)
        if row is None or row.get("stage") != sa.INSTRUCTION:
            return ar.STAGE_MOVED
        run_row = legacy.run_by_id(run_id)
        node = legacy.run_node_row(flow_id, run_row, key)
        if node is None:
            return ar.STAGE_MOVED
        ia = store.read(attempt_id, sa.INSTRUCTION, 0)
        if ia is not None and ia["stage_state"] == sa.FAILED:
            return ar.STAGE_MOVED                  # E-40: a failed instruction stage waits for a new attempt
        ctx = legacy.load_context(run_row)
        if row.get("instruction_doc_id") != doc_id:
            legacy.set_stage(run_id, key, sa.INSTRUCTION, doc_id)              # L-2 §2.4.2: linked before review
            legacy.fill_line(ctx, key, doc_id)
        if ia is not None and ia["stage_state"] in (sa.ACTIVE, sa.WAITING):
            sa.move(ia, sa.DONE)
        if ia is not None and not ia.get("reused_doc_id"):
            # 0045-TR rework (0030-M §3.1 ⑥): the attempt's instruction is written now — the report baseline moves to
            # this moment, so a report saved while the instruction waited never passes as one of after it (raises
            # ``BaselineUnavailable`` when it cannot be kept: the guard rolls back, the instruction is judged later)
            plan = sa.stage_plan(attempt_id) or {}
            report_baseline.advance_at_instruction(attempt_id, run_id, flow_id, key,
                                                   sa.report_line(flow_id, node, plan)[1])
        return ar.open_review_stage_locked(flow_id, run_row, node, attempt_id, sa.INSTRUCTION_REVIEW,
                                           legacy.document_row(doc_id), author.get("author") or "unknown",
                                           author.get("provider_id"), mode, after, ctx)

    a = legacy.with_flow_guard(flow_id, body)
    ar._run_jobs(after)
    if isinstance(a, dict):
        ar.drive_review_assignment(flow_id, a, mode, jobs)


def instruction_saved(flow_id: str, doc_id: Optional[str], node_key: Optional[str] = None,
                      token_id: Optional[str] = None) -> None:
    """``card_settings_ports.instruction_saved`` (after a commit): ``doc_id`` was saved — registered, its line written,
    its body or a new revision saved. A node of the active run whose person instruction stage holds it as its
    current instruction is judged (CONTENT_SAVED / REGISTERED). A node whose assignment cannot be read judges nothing
    and records its B-1 ``assignment_unreadable`` once (T §4.6). Never raises."""
    try:
        if not doc_id or not sa.enabled():
            return
        run_row = legacy.active_run(flow_id)
        if run_row is None:
            return
        for row in legacy.node_runs(run_row["run_id"]):
            key = row["node_key"]
            if node_key and key != node_key:
                continue
            if row["status"] != "open" or row.get("stage") != sa.INSTRUCTION:
                continue
            node = legacy.run_node_row(flow_id, run_row, key) or {"node_key": key}
            line = card_lines.line_document(flow_id, key, card_slots.INSTRUCTION, node.get("node_type"),
                                            node.get("doc_type")) or {}
            if doc_id not in (row.get("instruction_doc_id"), line.get("doc_id")) and node_key is None:
                continue
            if doc_id in _invalidated(flow_id, key):
                continue
            attempt_id = legacy.active_attempt_id(run_row["run_id"], key)
            try:
                if sa.is_legacy_attempt(attempt_id):
                    continue
                ia = store.read(attempt_id, sa.INSTRUCTION, 0)
            except sa.AssignmentUnreadable:
                _log.warning("instruction save not judged flow=%s node=%s: assignment unreadable", flow_id, key,
                             exc_info=True)
                _quietly(lambda: unreadable(flow_id, run_row["run_id"], key, attempt_id, doc_id, sa.INSTRUCTION))
                continue
            if ia is None or ia["actor"] != sa.PERSON or ia["stage_state"] == sa.FAILED:
                continue                           # E-40: a failed person stage is recovered by a new attempt only
            follow_person_instruction(flow_id, run_row["run_id"], key, doc_id, attempt_id,
                                      "registered" if token_id else "content_saved")
    except Exception:  # noqa: BLE001 — the save itself stands; the next run read reconciles (RC-6)
        _log.warning("instruction save not judged flow=%s doc=%s", flow_id, doc_id, exc_info=True)


# ── the result stage (L-2 §2.7.5) ─────────────────────────────────────────────

def person_result(flow_id: str, node: dict, doc_id: str):
    """``card_settings_ports.person_result`` of an assigned attempt, inside the binder's guard: the job that judges the
    bound result after the commit, or None. ``LEGACY``: the caller's current handler."""
    key = node.get("node_key")
    run = legacy.active_run(flow_id)
    if run is None or not legacy.person_stage_open(flow_id, run):
        return None
    attempt_id = legacy.active_attempt_id(run["run_id"], key)
    try:
        if sa.is_legacy_attempt(attempt_id):
            return sa.LEGACY
        a = store.read(attempt_id, sa.RESULT, 0)
    except Exception:  # noqa: BLE001 — E-03: nothing judged, the binder's own record stands
        _log.warning("result assignment not read flow=%s node=%s", flow_id, key, exc_info=True)
        return lambda: ar.record_once(flow_id, run["run_id"], key, attempt_id, doc_id, ar.ASSIGNMENT_UNREADABLE,
                                      sa.mark(legacy.document_row(doc_id)))
    if a is not None and a["actor"] != sa.PERSON and a["stage_state"] == sa.DONE:
        return None                                 # 0038-T T#3 A-4: the stage took its written report; it judges it
    if a is None or a["actor"] != sa.PERSON or a["stage_state"] == sa.FAILED:
        if a is not None and (a["actor"] in (sa.WAIT_SETUP, sa.WAIT_UNAVAILABLE) or a["stage_state"] == sa.FAILED):
            # E-27: a person's result bound on a stage that is no person's — the card's [승인] takes it (B-1 once,
            # after the binder's commit: its run events are numbered by the binder's own step)
            run_id = run["run_id"]
            return lambda: ar.record_once(flow_id, run_id, key, attempt_id, doc_id, ar.STAGE_NOT_PERSON,
                                          sa.mark(legacy.document_row(doc_id)))
        return None                                 # an AI's result: its run's end follows it
    owner = _handoff(flow_id, key, doc_id)
    run_id = run["run_id"]
    return lambda: follow_person_result(flow_id, run_id, key, doc_id, attempt_id, owner)


def follow_person_result(flow_id: str, run_id: str, key: str, doc_id: str, attempt_id: Optional[str],
                         owner: Optional[str] = None, trigger: Optional[str] = None) -> None:
    """A person's bound result judged (REGISTERED / RECONCILE). An unreadable assignment judges nothing: a contact
    records its B-1 once, the reconciliation (``trigger`` RECONCILE) only skips the node."""
    jobs: list = []
    try:
        with legacy.person_hold(flow_id, run_id, key, attempt_id, "manual_result") as owned:
            if not owned:
                return
            doc = legacy.document_row(doc_id)
            verdict, cur = manual_judgeable(flow_id, run_id, key, attempt_id, sa.RESULT, doc)
            if verdict == EXISTING:
                ar.drive_review_assignment(flow_id, cur, ar.HELD, jobs)
                return
            if verdict != YES:
                return
            run_row = legacy.run_by_id(run_id)
            node = legacy.run_node_row(flow_id, run_row, key)
            row = legacy.node_run(run_id, key)
            if node is None or row is None:
                return
            checked = required_result(flow_id, attempt_id, node, row, doc)
            if checked == INSTRUCTION_NOT_APPROVED:
                ar.record_once(flow_id, run_id, key, attempt_id, doc_id, INSTRUCTION_NOT_APPROVED, sa.mark(doc))
                return
            if checked != OK:
                return
            after: list = []

            def body():
                ra = store.read(attempt_id, sa.RESULT, 0)
                if ra is not None and ra["stage_state"] in (sa.ACTIVE, sa.WAITING):
                    sa.move(ra, sa.DONE)
                return ar.open_review_stage_locked(flow_id, run_row, node, attempt_id, sa.RESULT_REVIEW,
                                                   legacy.document_row(doc_id), "person", None, ar.HELD, after)

            a = legacy.with_flow_guard(flow_id, body)
            ar._run_jobs(after)
            if isinstance(a, dict):
                ar.drive_review_assignment(flow_id, a, ar.HELD, jobs)
    except sa.AssignmentUnreadable:
        _log.warning("person result not judged flow=%s node=%s doc=%s: assignment unreadable", flow_id, key, doc_id,
                     exc_info=True)
        jobs.clear()
        if trigger != RECONCILE:
            _quietly(lambda: unreadable(flow_id, run_id, key, attempt_id, doc_id, sa.RESULT))
    except Exception:  # noqa: BLE001 — the result stays in review for a person's [승인]
        _log.warning("person result stage failed flow=%s node=%s doc=%s", flow_id, key, doc_id, exc_info=True)
    finally:
        ar._run_jobs(jobs)
        if owner:
            _release(flow_id, owner)
        else:
            legacy.settle_graph_run(flow_id)


def reconcile_saved_report(flow_id: str, run_row: dict, row: dict, attempt_id: str) -> None:
    """RC-8: a person's result stage whose report was saved in the card's line (no registration): registered with the
    live node token once (the token is spent, so the next read finds nothing). 0038-T T#3 A: the report the stage takes
    is ``written_result``'s — a saved body, submitted or approved alike, never the attempt's report baseline (one that
    cannot be read raises: nothing is taken); an approved one is recorded as taken judged and goes to this attempt's
    review as any other (``person_result`` after the binder binds it)."""
    key = row["node_key"]
    found: dict = {}

    def body():
        live = legacy.open_row(run_row["run_id"], key)
        if live is None or live.get("stage") != sa.RESULT or live.get("result_doc_id"):
            return
        node = legacy.run_node_row(flow_id, run_row, key)
        plan = sa.stage_plan(attempt_id) or {}
        token_id = legacy.live_dispatch_token(run_row["run_id"], key)
        if node is None or not token_id or not plan:
            return
        doc = sa.written_result(flow_id, node, plan, attempt_id)
        if doc is None:
            return
        issuer = sa.issued_to_of(node, run_row)
        if legacy.register_result(doc["doc_id"], token_id, issuer):
            if doc.get("doc_review_status") in APPROVED:
                legacy.record_taken_judged(run_row["run_id"], key, attempt_id, doc, sa.mark(doc))
            found.update(doc_id=doc["doc_id"], doc_type=plan["result_type"], token_id=token_id)

    with legacy.person_hold(flow_id, run_row["run_id"], key, attempt_id, "reconcile_saved_report") as owned:
        if owned:
            legacy.with_flow_guard(flow_id, body)
    if found:
        legacy.tell_graph(flow_id, found["doc_id"], found["doc_type"], found["token_id"])
