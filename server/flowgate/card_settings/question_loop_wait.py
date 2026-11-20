"""A review loop waits for the questions its AI asked inside the loop (flowgate.v02test.0051 T0005 A1).

flowgate.v02test.0032 let a review loop go on while its rework (or review) AI's questions were open: the loop is one AI
run under one group lease, so the card's responder could not start and the next hop never waited. That exception is
gone (R 0051.0001 "when the worker asks, the reviewer answers; do not proceed until the answer arrives"):

- park (``worker._worker``, right after the hop's checkpoint and before any effect of the next hop — the Graph
  ``transition``, the next token, the next provider): a Graph loop whose run still has questions it asked open stops
  there. A durable wait row (migration 200, ``db.question_loop_waits``) keeps the stage the checkpoint reserved, the
  asking hop / invocation, the execution, attempt and assignment, and the wait's start. The worker ends normally: the
  run finalizes, the execution ends and the group lease is released — nothing polls. The loop row stays unstopped at
  the reserved stage, so the card stays locked and its review assignment reads as active (no re-request, repair,
  manual take-over or lost-result recovery); ``owns`` gives the orphan sweeps their owner evidence. "Nothing open"
  is only ever a successful read: a failed question read, or open questions whose wait cannot be stored, hold the
  next hop too (``hold``: the loop stops with a ``question_wait_unconfirmed`` detail, the run carries the diagnosis).
- answer: the run's end (``review_stage.on_run_ended``) does not judge the loop; ``question_wait.follow`` puts the
  questions to the card's responder (``question_wait._waiting`` phase ``loop_wait``, the existing dispatcher).
- resume (``question_wait._answered``, a person's / the responder's / a chat AI's answer, and the run read's
  ``question_wait.reconcile``): once every question is answered, the run woken (never a paused, stopping or stopped
  one) and the answers requiring no new attempt, ``resume`` requests the loop's next execution of the same attempt and
  assignment with the stored loop state (``execution.loop_state`` replays it: rounds, deadline and the earlier runs'
  reviews carry on). The request and ``waiting -> resuming`` commit in one Flow guard: exactly once, whatever repeats.
  An answer that comes while the parked run is still finishing (its execution not ended yet) leaves the wait
  ``waiting``; the run's end takes it again (``question_wait.follow_parked_end``).
- hand-off (``review._insert_document_review_loop``): the successor run's admission marks the wait ``resumed`` and
  closes the parked run's loop row (``stop_for_question_wait_handoff``: ``retry_exhausted`` with the
  ``question_wait_handed_on`` detail, never a verdict — ``handed_on``).

Only Graph loops park: a loop with no execution ledger has no way to be resumed and keeps its earlier behaviour.

flowgate.v02test.0051 T0007 (A2, R A-3 "waiting time and rejections caused by questions do not count toward the review budget"):

- question-caused verdicts: a review verdict that a question wait caused gets an explicit cause marker
  (``db.review_count_exclusions``, migration 201: ``question_wait`` + the wait row) and the loop leaves it out of its
  review_count only (``review._document_loop_counted_rounds``). Two shapes, both judged from durable rows when the
  verdict's hop ends, never guessed later: the reviewer asked and rejected in the hop that parked (``park`` ->
  ``classify_parked_review``), and the first review after a rework that submitted while asking, once its questions
  were answered (``review._checkpoint_document_review_loop`` -> ``classify_answer_check_review``). Either way only an
  ``issues`` verdict whose every finding cites one of the wait's questions (``Q<seq>`` / ``item <id>``) is
  question-caused (``question_only_verdict``); a verdict with any finding of its own -- a real defect -- is counted
  as before, and so is every verdict with no marker (legacy rows, pre-feature loops). The review row is never
  changed: it stays its round's verdict (rejection, rework, stall check, history).
- question wait time: ``excluded_wait_seconds`` sums ``waited_at .. resumed_at`` (``now`` while the resumed request
  is not admitted yet) of the waits the loop's runs parked with; the loop's total deadline and every stage timeout
  are moved by it (``review.loop_question_wait_sec``). Time outside a wait counts as before, so a loop that keeps
  failing still reaches its deadline; a cancelled wait is never excluded (its loop did not go on).
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Iterable, Optional

_log = logging.getLogger(__name__)

PHASE = "loop_wait"
HOP_KINDS = ("review", "rework")
HANDED_ON_DETAIL = "question_wait_handed_on"
HELD = "held"                                   # ``park``: the questions could not be confirmed; the next hop is held
HOLD_DETAIL = "question_wait_unconfirmed"       # the stopped loop row's ``stop_detail`` prefix (``hold``)
HOLD_END_REASON = "question_wait_unconfirmed"
_RETRY_DELAYS = (0.1, 0.3)                      # ``_retrying``: a transient store failure is tried twice more


def _db():
    from modules.flow_gate.db import question_loop_waits  # noqa: PLC0415

    return question_loop_waits


def available() -> bool:
    try:
        return _db().available()
    except Exception:  # noqa: BLE001 — no store, no table: nothing parks
        return False


def handed_on(loop: Optional[dict]) -> bool:
    """The loop row of a parked run whose successor took the loop over: never a verdict to consume."""
    return bool(loop) and str(loop.get("stop_detail") or "").startswith(HANDED_ON_DETAIL)


# ── park ───────────────────────────────────────────────────────────────────────

def _asker_inv_id(run: dict, items: list[dict]) -> Optional[str]:
    """The invocation of this run whose actual AI asked the (earliest) question — None when unknown."""
    asker = next((i.get("asker_actual_provider_id") for i in items if i.get("asker_actual_provider_id")), None)
    try:
        from flowgate.ai_roles import record_store  # noqa: PLC0415

        records = run.get("invocations") or (record_store.by_run(run["run_id"]) if record_store.available() else [])
    except Exception:  # noqa: BLE001 — provenance is a record, never a reason not to wait
        _log.warning("question loop wait: invocation read failed run=%s", run.get("run_id"), exc_info=True)
        return None
    records = [r for r in records or [] if r.get("inv_id")]
    if asker:
        same = [r for r in records if r.get("actual_ai_id") == asker]
        records = same or []
    if not records:
        return None
    return max(records, key=lambda r: r.get("launch_no") or 0).get("inv_id")


def _retrying(action):
    """``action()`` with a short retry for a transient store failure (a locked row, a dropped connection); the last
    failure is raised."""
    for delay in (*_RETRY_DELAYS, None):
        try:
            return action()
        except Exception:  # noqa: BLE001
            if delay is None:
                raise
            time.sleep(delay)
    return None


def park(run: dict, loop: Optional[dict]) -> Optional[dict]:
    """At a Graph review loop's hop boundary: what stops ``run`` here for its questions, else None (the next hop starts).

    Called after the hop's checkpoint and before any effect of the next hop. ``loop`` is the checkpoint's row (its
    ``current_stage`` the reserved successor). Returns
    - None: ``run`` asked nothing that is still open — confirmed by a successful read (the next hop starts);
    - the stored wait row (``status`` ``waiting``): it asked, the wait is durable; the run ends and is resumed later;
    - a hold (``{"status": HELD, "reason": ...}``, ``hold``): it could not be confirmed that nothing is open (the
      question read failed) or open questions were found but their wait could not be stored. The next hop never starts
      on an unconfirmed read: the loop is stopped with a diagnosable ``question_wait_unconfirmed`` detail and the run
      ends (its open questions, if any, still hold the loop's verdict — ``assigned_review.consume_loop_verdict`` — and
      go to the card's responder as any stopped loop's do)."""
    if not loop or loop.get("current_stage") not in HOP_KINDS or loop.get("stop_reason"):
        return None
    exec_id, group_id, run_id = run.get("graph_exec_id"), run.get("group_id"), run.get("run_id")
    if not exec_id or not group_id or not run_id:
        return None
    if run.get("cancel_event") is not None and run["cancel_event"].is_set():
        return None
    try:
        from modules.flow_gate.services.ai_invoke.finalize import open_questions_raised_by_run  # noqa: PLC0415

        items = _retrying(lambda: open_questions_raised_by_run(group_id, run_id))
    except Exception:  # noqa: BLE001 — unknown is not "none open": the next hop does not start
        return hold(run, loop, "question_read_failed", [])
    if not items:
        return None
    if not available():
        return hold(run, loop, "wait_store_unavailable", items)
    try:
        wait = _retrying(lambda: _store_wait(run, loop, items))
    except Exception:  # noqa: BLE001 — open questions with no durable wait: the next hop does not start either
        return hold(run, loop, "wait_store_failed", items)
    run["question_loop_wait_id"] = wait["wait_id"]
    _log.info("review loop parked for its questions run=%s exec=%s stage=%s round=%s items=%s", run_id, exec_id,
              wait["resume_stage"], wait["resume_round_no"], wait["question_item_ids"])
    classify_parked_review(run, wait, items)     # T0007 (A2): never raises; no marker = counted as before
    return wait


def _store_wait(run: dict, loop: dict, items: list[dict]) -> dict:
    """Insert ``run``'s wait row — or take the one an earlier try of this park already stored."""
    from flowgate.graph_run import execution_store as ledger, fencing  # noqa: PLC0415

    run_id = run["run_id"]
    stored = _db().get_by_loop_run(run_id)
    if stored is not None and stored["status"] in _db().OPEN:
        return stored
    row = ledger.get(run["graph_exec_id"]) or {}
    attempt = fencing.attempt_row(row.get("attempt_id")) if row.get("attempt_id") else None
    asked_hop = loop.get("last_hop_kind") if loop.get("last_hop_kind") in HOP_KINDS else (
        run.get("hop_kind") if run.get("hop_kind") in HOP_KINDS else loop["current_stage"])
    asked_round = int(loop.get("round_no") or 1)
    if asked_hop == "review" and loop["current_stage"] == "rework" and loop.get("last_hop_outcome") == "succeeded":
        asked_round = max(1, asked_round - 1)       # a rejecting review opened the next round; it asked in its own
    return _db().insert({
        "group_id": run["group_id"], "doc_ref": run.get("doc_ref") or loop.get("doc_ref"), "loop_run_id": run_id,
        "graph_exec_id": run["graph_exec_id"], "graph_attempt_id": row.get("attempt_id"),
        "graph_run_id": row.get("run_id"), "node_key": (attempt or {}).get("node_key"),
        "assignment_stage": row.get("assignment_stage"), "assignment_seq": row.get("assignment_seq"),
        "asked_hop_kind": asked_hop,
        "asker_inv_id": _asker_inv_id(run, items),
        "asked_round_no": asked_round,
        "hop_outcome": loop.get("last_hop_outcome") if loop.get("last_hop_outcome") in ("succeeded", "failed")
        else None,
        "resume_stage": loop["current_stage"], "resume_round_no": int(loop.get("round_no") or 1),
        "resume_attempts_used": int(loop.get("attempts_used") or 0),
        "question_item_ids": [int(i["id"]) for i in items if i.get("id") is not None],
    })


def hold(run: dict, loop: dict, reason: str, items: list[dict]) -> dict:
    """The next hop is held because the questions could not be confirmed (``park``): ``run`` ends here with the
    diagnosis on it (``question_loop_wait_hold``, ``end_reason`` ``question_wait_unconfirmed``, ``last_message``) and
    its loop row is stopped with the existing ``retry_exhausted`` reason and a ``question_wait_unconfirmed: <reason>``
    detail (no new stop_reason, no CHECK migration). Never raises: a loop that cannot even be stopped stays unstopped
    with no owner and the existing orphan sweep closes it; the run still ends without its next hop."""
    run_id = run.get("run_id")
    ids = [int(i["id"]) for i in items or [] if i.get("id") is not None]
    detail = f"{HOLD_DETAIL}: {reason}"
    _log.warning("review loop held at its hop boundary run=%s exec=%s stage=%s: %s items=%s", run_id,
                 run.get("graph_exec_id"), loop.get("current_stage"), reason, ids, exc_info=True)
    stopped = False
    try:
        from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops  # noqa: PLC0415

        stopped = bool(db_loops.stop_for_question_wait_unconfirmed(run_id, detail=detail))
        latest = db_loops.get(run_id) if stopped else None
        if latest is not None:
            run["document_review_loop"] = latest
    except Exception:  # noqa: BLE001
        _log.warning("question loop wait hold: loop stop failed run=%s", run_id, exc_info=True)
    found = {"status": HELD, "reason": reason, "stage": loop.get("current_stage"),
             "round_no": int(loop.get("round_no") or 1), "question_item_ids": ids, "loop_stopped": stopped}
    run["question_loop_wait_hold"] = found
    run["outcome"] = "none"
    run["end_reason"] = HOLD_END_REASON
    run["last_message"] = (f"The review loop was held before its next {loop.get('current_stage')} hop: {reason} "
                           f"(questions {ids or 'unknown'}). Nothing was skipped; answer the questions and request "
                           "the review again.")
    return found


# ── reads ──────────────────────────────────────────────────────────────────────

def wait_of_run(run_id: Optional[str]) -> Optional[dict]:
    """The wait row ``run_id`` parked with (any status), or None (no table / none / unreadable)."""
    if not run_id or not available():
        return None
    try:
        return _db().get_by_loop_run(run_id)
    except Exception:  # noqa: BLE001
        _log.warning("question loop wait read failed run=%s", run_id, exc_info=True)
        return None


def parked(run: dict) -> bool:
    """``run`` ended parked for its questions (its wait still open)."""
    if run.get("question_loop_wait_id"):
        return True
    wait = wait_of_run(run.get("run_id"))
    return wait is not None and wait["status"] in _db().OPEN


def _resume_request_taken(wait: dict) -> Optional[bool]:
    """For a ``resuming`` wait: True while its request may still be taken (requested / running, or a run took it),
    False when it ended without any run taking it (cancelled / lost / missing) — None when unreadable."""
    try:
        from flowgate.graph_run import execution_store as ledger  # noqa: PLC0415

        row = ledger.get(wait.get("resume_exec_id")) if wait.get("resume_exec_id") else None
    except Exception:  # noqa: BLE001
        return None
    if row is None:
        return False
    if row.get("ai_run_id") or row["status"] in ("requested", "running"):
        return True
    return False


def owns(run_id: Optional[str]) -> bool:
    """Owner evidence for the loop row of ``run_id`` (``admission.review_loop_owner_evidence``): its wait is open and
    its attempt is still active. Unreadable evidence keeps the protection (fail closed)."""
    if not run_id or not available():
        return False
    try:
        wait = _db().get_by_loop_run(run_id)
        if wait is None or wait["status"] not in _db().OPEN:
            return False
        if wait.get("graph_attempt_id"):
            from flowgate.graph_run import fencing  # noqa: PLC0415

            attempt = fencing.attempt_row(wait["graph_attempt_id"])
            if attempt is None or attempt.get("attempt_status") != "active":
                return False
        return True                             # waiting, or resuming (taken or resumable again): its loop
    except Exception:  # noqa: BLE001
        _log.warning("question loop wait owner read failed run=%s", run_id, exc_info=True)
        return True


def waiting_phase(flow_id: str, run: dict, node: Optional[dict]) -> tuple[bool, Optional[tuple]]:
    """For ``question_wait._waiting``: (found, waiting). ``found`` — ``run`` parked (whatever its wait's state), so the
    loop-end ``review`` phase never applies to it; ``waiting`` — ``(PHASE, node, wait)`` while it may be resumed."""
    wait = wait_of_run(run.get("run_id"))
    if wait is None or wait.get("group_id") != flow_id:
        return False, None
    if node is None or not resume_due(wait):
        return True, None
    return True, (PHASE, node, wait)


def resume_due(wait: dict) -> bool:
    """The wait may (still) be resumed: ``waiting``, or ``resuming`` whose request no run took and that ended."""
    if wait["status"] == _db().WAITING:
        return True
    return wait["status"] == _db().RESUMING and not wait.get("resumed_run_id") and _resume_request_taken(wait) is False


def item_wait_phases(flow_id: str) -> dict:
    """``{question item id: wait}`` of the Flow's open waits (the card's Q badge, ``run_service``); {} on a failure."""
    if not available():
        return {}
    try:
        return {int(i): w for w in _db().list_open_by_group(flow_id) for i in w["question_item_ids"]}
    except Exception:  # noqa: BLE001
        _log.warning("question loop wait list failed flow=%s", flow_id, exc_info=True)
        return {}


# ── resume ─────────────────────────────────────────────────────────────────────

def resume(flow_id: str, wait: dict) -> Optional[str]:
    """Request the parked loop's next execution once (the Flow guard; the caller woke the run and settles after).

    Returns the new execution id, or None (resumed / cancelled elsewhere, the attempt or assignment moved on, a
    refusal, or the parked run still finishing). An attempt that is no longer active cancels the wait — its loop row is
    then the orphan sweeps' to close. A parked run whose execution is still ``requested`` / ``running`` (an answer that
    came between the park and the run's finalize) is never a cancellation: the wait stays ``waiting`` and the run's end
    takes it again (``review_stage.on_run_ended`` -> ``question_wait.follow_parked_end``)."""
    from flowgate.graph_run import execution, execution_store as ledger, fencing  # noqa: PLC0415
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops  # noqa: PLC0415

    db = _db()
    cancelled: list[str] = []
    deferred: list[str] = []

    def body() -> Optional[str]:
        cur = db.get(wait["wait_id"])
        if cur is None or not resume_due(cur):
            return None
        ended = ledger.get(cur.get("graph_exec_id")) if cur.get("graph_exec_id") else None
        if ended is not None and ended.get("status") in ("requested", "running"):
            # the parked run is still finishing (its finalize has not ended the execution yet): the wait stays
            # ``waiting`` and its run end takes it again (``question_wait.follow_parked_end``)
            deferred.append("execution_" + str(ended["status"]))
            return None
        if ended is None or ended.get("status") != "ended":
            cancelled.append("execution_" + str((ended or {}).get("status") or "missing"))
            return None
        attempt = fencing.attempt_row(ended["attempt_id"])
        if attempt is None or attempt.get("attempt_status") != "active":
            cancelled.append("attempt_" + str((attempt or {}).get("attempt_status") or "missing"))
            return None
        fencing.check(ended["attempt_id"], "review_transition", statuses=fencing.REPORTING)
        if ended.get("assignment_stage") and not _assignment_current(ended):
            cancelled.append("assignment_moved")
            return None
        loop = db_loops.get(cur["loop_run_id"])
        if loop is None or loop.get("current_stage") != cur["resume_stage"] or loop.get("stop_reason"):
            cancelled.append("loop_" + str((loop or {}).get("current_stage") or "missing"))
            return None
        payload = json.loads(ended["request_json"])
        payload["loop"] = dict(loop)
        payload["review_run_ids"] = sorted(set(payload.get("review_run_ids") or []) | {cur["loop_run_id"]})
        hop_origin = execution._hop_origin(payload, loop)
        if hop_origin is not None:
            payload["invocation_origin"] = hop_origin
        payload["question_wait_id"] = cur["wait_id"]
        ref = (ended["assignment_stage"], ended["assignment_seq"]) if ended.get("assignment_stage") else None
        successor = ledger.request(ended["attempt_id"], ended["run_id"], cur["resume_stage"], payload,
                                   assignment=ref)
        if cur["status"] == db.WAITING:
            claimed = db.claim_resume(cur["wait_id"], successor)
        else:
            claimed = db.reclaim_resume(cur["wait_id"], cur["resume_exec_id"], successor)
        if not claimed:
            raise ledger.LeaseLost("question loop wait resumed elsewhere")
        return successor

    try:
        successor = fencing.guarded(flow_id, body)
    except ledger.LeaseLost:
        _log.info("question loop wait not resumed flow=%s wait=%s: taken elsewhere", flow_id, wait["wait_id"])
        return None
    except Exception:  # noqa: BLE001 — a refusal is recorded by the fence; the wait stays for the next answer / read
        _log.warning("question loop wait resume refused flow=%s wait=%s", flow_id, wait["wait_id"], exc_info=True)
        return None
    if deferred:
        _log.info("question loop wait resume deferred flow=%s wait=%s: %s", flow_id, wait["wait_id"], deferred[0])
        return None
    if cancelled:
        reason = cancelled[0]
        if reason.startswith(("attempt_", "assignment_", "execution_")) and db.cancel(wait["wait_id"], reason):
            _log.info("question loop wait cancelled flow=%s wait=%s: %s", flow_id, wait["wait_id"], reason)
        return None
    if successor:
        _log.info("review loop resumed after answers flow=%s wait=%s run=%s exec=%s stage=%s", flow_id,
                  wait["wait_id"], wait["loop_run_id"], successor, wait["resume_stage"])
    return successor                            # the caller settles: the Graph replays the request


def _assignment_current(ended: dict) -> bool:
    """The execution's review assignment is still the attempt's current one, an AI's, active and without verdict."""
    from flowgate.card_settings import stage_assignment as sa  # noqa: PLC0415

    try:
        latest = sa.current_review_assignment(ended["attempt_id"], ended["assignment_stage"])
    except sa.AssignmentUnreadable:
        return False
    return (latest is not None and int(latest["seq"]) == int(ended["assignment_seq"]) and latest["actor"] == sa.AI
            and latest["stage_state"] == sa.ACTIVE and not latest.get("verdict"))


def on_successor_admitted(run: dict) -> bool:
    """The admission of a resumed execution's run (its loop row just inserted): the wait is ``resumed`` and the
    parked run's loop row is closed as handed on. Never raises: admission is not failed by it."""
    exec_id = run.get("graph_exec_id")
    if not exec_id or not available():
        return False
    try:
        from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops  # noqa: PLC0415

        db = _db()
        wait = db.get_by_resume_exec(exec_id)
        if wait is None or wait["status"] != db.RESUMING:
            return False
        if not db.mark_resumed(wait["wait_id"], exec_id, run["run_id"]):
            return False
        db_loops.stop_for_question_wait_handoff(wait["loop_run_id"], successor_run_id=run["run_id"])
        _log.info("question loop wait handed on wait=%s from=%s to=%s", wait["wait_id"], wait["loop_run_id"],
                  run["run_id"])
        return True
    except Exception:  # noqa: BLE001
        _log.warning("question loop wait hand-off failed exec=%s run=%s", exec_id, run.get("run_id"), exc_info=True)
        return False


def resume_answered(flow_id: str) -> None:
    """The run read / Resume: every open wait of the Flow whose questions are all answered goes on (once), as an
    answer event would have taken it (``question_wait._answered``) — a restart or a lost event loses nothing."""
    if not available():
        return
    from flowgate.card_settings import question_wait  # noqa: PLC0415

    try:
        waits = _db().list_open_by_group(flow_id)
    except Exception:  # noqa: BLE001
        _log.warning("question loop wait list failed flow=%s", flow_id, exc_info=True)
        return
    for wait in waits:
        if resume_due(wait) and question_wait.open_questions(flow_id, wait["loop_run_id"]) == []:
            question_wait._answered(flow_id, wait["loop_run_id"])


# ── A2: question-caused verdicts and question wait time (flowgate.v02test.0051 T0007) ─────────────────────────────

# A finding cites a question by its item id ("item 901", the form the engine's answered-question clauses ask for) or by
# its Q number ("Q12"). Only the wait's own questions count as cited.
_ITEM_REF = re.compile(r"\bitem\s*#?\s*(\d+)\b", re.IGNORECASE)
_SEQ_REF = re.compile(r"\bQ\s*#?\s*(\d+)\b")
_FINDING_TEXT_KEYS = ("locus", "note", "title", "detail", "message")
# Citing a question is only a reference: a real defect may name a question too. A finding rests on its question ONLY
# when the reviewer says so explicitly with ``"cause": "question"`` (the review mention's Verdict guide and the
# answered-question clause tell it when to). Without that field a finding is a defect of its own, whatever it cites.
FINDING_CAUSE_KEY = "cause"
FINDING_CAUSE_QUESTION = "question"


def _exclusions():
    from modules.flow_gate.db import review_count_exclusions  # noqa: PLC0415

    return review_count_exclusions


def _findings_of(review: dict) -> Optional[list]:
    raw = review.get("findings")
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, list) else None


def _finding_text(finding) -> str:
    if isinstance(finding, dict):
        return " ".join(str(finding.get(key) or "") for key in _FINDING_TEXT_KEYS)
    return str(finding or "")


def declares_question_only(finding) -> bool:
    """The finding itself says it is about its question and nothing else (``"cause": "question"``)."""
    return (isinstance(finding, dict)
            and str(finding.get(FINDING_CAUSE_KEY) or "").strip().lower() == FINDING_CAUSE_QUESTION)


def question_only_verdict(review: Optional[dict], items: Iterable[dict]) -> Optional[list]:
    """The ids of ``items`` an ``issues`` verdict rests on when it rests on nothing else -- else None (it counts).

    Two things are required of EVERY finding: it declares itself question-only (``declares_question_only``, the
    explicit basis) AND it cites one of ``items`` (``item <id>`` or ``Q<seq>``), so the declaration is tied to this
    wait's questions. A citation alone proves nothing -- a real defect may name a question in the same finding -- so a
    finding without the declaration is a defect of its own and the verdict is counted whatever questions it names. A
    verdict with no findings, an unreadable findings list, another verdict or no questions is never question-caused
    (nothing explicit to rest on)."""
    if not review or (review.get("verdict") or "").lower() != "issues":
        return None
    findings = _findings_of(review)
    if not findings:
        return None
    items = [i for i in items or () if i and i.get("id") is not None]
    by_id = {int(i["id"]) for i in items}
    by_seq = {int(i["seq"]): int(i["id"]) for i in items if i.get("seq") is not None}
    if not by_id:
        return None
    cited: set = set()
    for finding in findings:
        if not declares_question_only(finding):
            return None
        text = _finding_text(finding)
        hit = {int(m) for m in _ITEM_REF.findall(text) if int(m) in by_id}
        hit |= {by_seq[int(m)] for m in _SEQ_REF.findall(text) if int(m) in by_seq}
        if not hit:
            return None
        cited |= hit
    return sorted(cited)


def _owned_reviews(doc_ref: str, run_id: str) -> list[dict]:
    from modules.flow_gate.db import document_reviews as db_reviews  # noqa: PLC0415

    return sorted((r for r in db_reviews.list_by_doc(doc_ref) or [] if r.get("review_run_id") == run_id),
                  key=lambda r: int(r.get("id") or 0))


def _mark(wait: dict, review: dict, cited: list, basis: str) -> dict:
    row = _exclusions().insert(review_id=int(review["id"]), doc_ref=wait["doc_ref"], wait_id=wait["wait_id"],
                               basis=basis, question_item_ids=cited, review_run_id=review.get("review_run_id"),
                               revision_no=review.get("revision_no"))
    _log.info("review %s not counted against the loop's review_count: %s (wait=%s, questions %s)", review["id"],
              basis, wait["wait_id"], cited)
    return row


def classify_parked_review(run: dict, wait: dict, items: list[dict]) -> Optional[dict]:
    """``park``: the reviewer asked and returned its verdict in the hop that just parked. When that verdict is an
    ``issues`` resting on those questions only (``question_only_verdict``: every finding declared and cited), it is marked
    question-caused. Returns the exclusion row, or None (nothing to mark, or it could not be marked -- then it counts,
    as before). Never raises."""
    if wait.get("asked_hop_kind") != "review" or wait.get("hop_outcome") != "succeeded":
        return None
    try:
        if not _exclusions().available():
            return None
        owned = _owned_reviews(wait["doc_ref"], run["run_id"])
        if not owned:
            return None
        review = owned[-1]                       # the verdict of the review hop that parked (its round's newest)
        cited = question_only_verdict(review, items)
        if cited is None:
            return None
        return _mark(wait, review, cited, _exclusions().BASIS_ASKED_WITH_VERDICT)
    except Exception:  # noqa: BLE001 — an unmarked verdict is counted, never a reason to fail the park
        _log.warning("question-caused review could not be marked run=%s wait=%s", run.get("run_id"),
                     wait.get("wait_id"), exc_info=True)
        return None


def classify_answer_check_review(run: dict, loop: Optional[dict]) -> Optional[dict]:
    """After a review hop's checkpoint (``review._checkpoint_document_review_loop``): the run was resumed from a wait
    whose rework hop submitted the document while asking (``asked_hop_kind`` rework, ``hop_outcome`` succeeded,
    ``resume_stage`` review), and this is the run's FIRST verdict -- the review of that very submission, made after
    the answers. When it is an ``issues`` whose every finding declares itself question-only and cites one of those
    questions (``question_only_verdict``), the
    rejection comes from the assumption the question stated and is marked question-caused. Never raises."""
    run_id = run.get("run_id")
    predecessors = run.get("graph_review_run_ids") or []
    if (not run_id or not predecessors or not loop or loop.get("last_hop_kind") != "review"
            or loop.get("last_hop_outcome") != "succeeded"):
        return None
    try:
        if not available() or not _exclusions().available():
            return None
        waits = [w for w in _db().list_by_loop_runs(predecessors)
                 if w.get("resumed_run_id") == run_id and w["status"] == _db().RESUMED
                 and w.get("asked_hop_kind") == "rework" and w.get("hop_outcome") == "succeeded"
                 and w.get("resume_stage") == "review"]
        if not waits:
            return None
        wait = waits[-1]
        owned = _owned_reviews(wait["doc_ref"], run_id)
        if len(owned) != 1:
            return None                          # none yet, or not the first review after the resume
        from modules.flow_gate.db import question_items as db_question_items  # noqa: PLC0415

        items = [row for row in (db_question_items.get_by_pk(int(i)) for i in wait["question_item_ids"]) if row]
        cited = question_only_verdict(owned[0], items)
        if cited is None:
            return None
        return _mark(wait, owned[0], cited, _exclusions().BASIS_ANSWER_CONTRADICTED)
    except Exception:  # noqa: BLE001
        _log.warning("answer-check review could not be classified run=%s", run_id, exc_info=True)
        return None


def _instant(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def excluded_wait_seconds(loop_run_ids: Iterable[str], now: datetime) -> int:
    """Seconds the loop carried by ``loop_run_ids`` spent parked for its questions: each wait's ``waited_at`` up to
    ``resumed_at`` (its successor admitted), or up to ``now`` while it is resuming / waiting. A cancelled wait (its
    loop did not go on) is not excluded. 0 when there is nothing to read; a failed read is logged and also answers 0,
    which keeps the time counted as before (never a longer budget on records nobody could read)."""
    ids = [i for i in loop_run_ids or () if i]
    if not ids or not available():
        return 0
    try:
        waits = _db().list_by_loop_runs(ids)
    except Exception:  # noqa: BLE001
        _log.warning("question wait time could not be read runs=%s", ids, exc_info=True)
        return 0
    until = _instant(now) or datetime.now(timezone.utc)
    total = 0
    for wait in waits:
        if wait.get("status") == _db().CANCELLED:
            continue
        start = _instant(wait.get("waited_at"))
        end = _instant(wait.get("resumed_at")) or until
        if start is not None and end > start:
            total += int((end - start).total_seconds())
    return total

