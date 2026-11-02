"""Card completion (flowgate.v02.0119 T#2, T-1; 0004-CH seq 9 §1 · seq 11 교체 2 · seq 13 정정 2).

The completion record is the only source of "this node is done" in a Graph run. A node's ``review -> done`` has three
causes, never a person's approval of a document:

    ai_review_passed     the card's AI review passed the result (actor: the provider the review actually ran on)
    no_review_submitted  the card has no review: the AI result is complete as submitted (actor: system)
    human_submitted      a person's result with no review, complete as submitted (actor: the person who wrote it)

``requested_by`` is the Graph run's issuer (``started_by``) and only that (it is never the actor, nor the card's
assignee). ``verdict_id`` is the passing review row.

Order (T-1 정합성):

    ① ``record`` — the row is written and committed, idempotently: the key ``(run_id, node_key, attempt_id,
       result_doc_id)`` holds one live row, so a repeated review pass, a retried job or two run commands racing write
       one row; the second asker gets the first one's row back. A row superseded by a newer revision of the same result
       in the same attempt (a person's resubmission) is followed by the new submission's own row (the key's next
       ``generation``); a revoked row (Time Machine) never is.
    ② ``apply`` — in the Flow guard (one transaction): the node's missing ``review_approved`` is made from the record by
       the binder's reconciliation (``result_binder.next_missing_event`` asks ``completion_of`` here), and ``resolve`` ->
       ``apply_decision`` makes the node done, ends its attempt and opens the next cards in the same transaction; the
       record turns ``applied`` there too (``applied``, called by ``apply_decision`` before the attempt ends), and the
       node event carries its ``completion_id``. A code node's done waits for its attempt's merge (``defer_done``,
       ``integration_waiting``): the record stays ``recorded`` and the merge's end applies it the same way.
    ③ the guard commits.

A failure or a restart between ① and ② leaves a ``recorded`` row the node has not followed: the next reconciliation
(a run read, a command, the binder's next contact) reads it and makes the missing event; the frontier ignores an event
that is no longer valid, so the done and the openings happen once. The same reconciliation settles the Flow's
``recorded`` rows that no longer answer their node (``settle``, in the guard): a moved result, attempt or revision, a
node out of review or another run makes them ``superseded`` — with no explicit ``apply`` again — and a node already done
with the record's own attempt and result makes it ``applied``.

A Time Machine rewind marks the target nodes' records ``revoked`` (kept, never applied again). A new attempt (an
automatic rework, a rework asked with a memo, a retry) has a new attempt id and so its own record: only a repeated event
of the same submission is refused, never a new one.

Existing runs: a node of a run started before the service was installed (``repository.installed_at``) whose result is
approved reads as complete (``completion_of`` -> ``legacy``) only when no completion row of the node exists in the run
and its current attempt (if any) also predates the installation — a superseded, revoked or stale row and a rework's new
attempt are never read past. Their rows are not migrated.
"""
from __future__ import annotations

import logging
from typing import Optional

from flowgate.card_completion import legacy_adapter as legacy
from flowgate.card_completion import repository
from flowgate.card_completion.repository import APPLIED, LIVE, RECORDED, REVOKED, SUPERSEDED, TERMINAL

_log = logging.getLogger(__name__)

AI_REVIEW_PASSED, NO_REVIEW_SUBMITTED, HUMAN_SUBMITTED = "ai_review_passed", "no_review_submitted", "human_submitted"
CAUSES = (AI_REVIEW_PASSED, NO_REVIEW_SUBMITTED, HUMAN_SUBMITTED)
SYSTEM, AI, USER = "system", "ai", "user"
ACTOR_KINDS = (SYSTEM, AI, USER)
SYSTEM_ACTOR_ID = "system"
REVIEW_STATUS = "review"
DONE_STATUS = "done"
PASS_VERDICT = "pass"
TIME_MACHINE_REASON = "time_machine"
ALREADY_DONE = "already_done"


class CompletionRefused(ValueError):
    """A completion that cannot be recorded: an unknown cause / actor kind, or no node in review takes the result."""

    def __init__(self, code: str, **params):
        super().__init__(code)
        self.code = code
        self.params = params


# ── the node a result belongs to ─────────────────────────────────────────────

def target_of(flow_id: str, doc_id: str, node_key: Optional[str] = None) -> Optional[dict]:
    """``{run, node_key, attempt_id, node_run}`` of the active run's node whose result in review is ``doc_id`` (and whose
    key is ``node_key`` when given), or None."""
    run = legacy.active_run(flow_id)
    if run is None or not doc_id:
        return None
    row = legacy.node_run_by_result(run["run_id"], doc_id)
    if row is None or (node_key and row["node_key"] != node_key) or row["status"] != REVIEW_STATUS:
        return None
    return {"run": run, "node_key": row["node_key"], "node_run": row,
            "attempt_id": legacy.active_attempt_id(run["run_id"], row["node_key"])}


def requested_by_of(run: Optional[dict]) -> Optional[str]:
    """The Graph run's issuer: its ``started_by`` (never a token's recipient nor the card's assignee)."""
    issuer = (run or {}).get("started_by")
    return str(issuer) if issuer else None


def passing_verdict(doc_id: str, revision_no: Optional[int]) -> Optional[dict]:
    """The latest review of the document's ``revision_no`` when it passed it, else None."""
    reviews = [r for r in legacy.reviews_of(doc_id) if revision_no is None or int(r.get("revision_no") or 0) == revision_no]
    if not reviews:
        return None
    latest = max(reviews, key=lambda r: int(r.get("id") or 0))
    return latest if str(latest.get("verdict") or "").lower() == PASS_VERDICT else None


def review_provider(verdict: Optional[dict]) -> Optional[str]:
    """The provider the passing review actually ran on: its row's ``actual_provider_id``, else the provider of the
    review run it names (``review_run_id``). Never the provider that was only asked for (``requested_provider_id``): a
    fallback run or an incomplete record does not make it the actor. None when nothing proves one."""
    if not verdict:
        return None
    actual = verdict.get("actual_provider_id")
    if actual:
        return str(actual)
    return legacy.review_run_provider(verdict.get("review_run_id"))


def actor_of(cause: str, verdict: Optional[dict] = None, author: Optional[str] = None) -> tuple[str, Optional[str]]:
    """``(actor_kind, actor_id)`` of a completion: the AI a pass was actually made by (``review_provider``; unknown: no
    id); the system for an AI result complete as submitted with no review; for ``human_submitted`` the person who wrote
    the submission (``author``, from the submission's own records; unknown: no id). Never the run's issuer."""
    if cause == AI_REVIEW_PASSED:
        return AI, review_provider(verdict)
    if cause == NO_REVIEW_SUBMITTED:
        return SYSTEM, SYSTEM_ACTOR_ID
    return USER, (str(author) if author else None)


# ── ① the record ─────────────────────────────────────────────────────────────

def _renews(latest: dict, revision_no: Optional[int]) -> bool:
    """The key's latest row was superseded and ``revision_no`` is a newer revision of its result: a new submission."""
    return latest["status"] == SUPERSEDED and revision_no is not None and latest.get("revision_no") is not None \
        and int(revision_no) > int(latest["revision_no"])


def record(flow_id: str, run_id: str, node_key: str, attempt_id: Optional[str], result_doc_id: str, *, cause: str,
           actor_kind: str, actor_id: Optional[str], requested_by: Optional[str], verdict_id=None,
           revision_no: Optional[int] = None) -> tuple[dict, bool]:
    """Write the completion (or read back the one its key has): ``(row, created)``. Joins the caller's transaction when
    there is one (the automatic completion writes it with the document's status display, revision pinned).

    The key's live row is returned as it is (a repeated pass). A ``recorded`` row of an older revision is superseded
    first (``revision_changed``); a superseded row followed by a newer revision of the result takes the new submission's
    row as the key's next generation. A revoked row, or a superseded one with no newer revision, is returned as it is —
    the caller must not report it as a completion (its status is not live)."""
    if cause not in CAUSES:
        raise CompletionRefused("unknown_cause", cause=cause)
    if actor_kind not in ACTOR_KINDS:
        raise CompletionRefused("unknown_actor_kind", actor_kind=actor_kind)
    if not repository.available():
        raise CompletionRefused("completion_store_missing")
    row = {"run_id": run_id, "group_id": flow_id, "node_key": node_key, "attempt_id": attempt_id,
           "result_doc_id": result_doc_id, "revision_no": revision_no, "cause": cause, "actor_kind": actor_kind,
           "actor_id": actor_id, "requested_by": requested_by,
           "verdict_id": None if verdict_id is None else str(verdict_id)}
    with legacy.transaction():
        latest = repository.by_key(run_id, node_key, attempt_id, result_doc_id)
        if latest is None:
            return repository.insert(row, 0)
        if latest["status"] == RECORDED and revision_no is not None and latest.get("revision_no") is not None \
                and int(latest["revision_no"]) != int(revision_no):
            repository.set_status(latest["completion_id"], (RECORDED,), SUPERSEDED, "revision_changed")
            latest = repository.by_id(latest["completion_id"]) or latest
        if _renews(latest, revision_no):
            return repository.insert(row, int(latest.get("generation") or 0) + 1)
        return latest, False


# ── whether a record still answers the node ──────────────────────────────────

def stale_reason(rec: dict, row: Optional[dict], active_attempt: Optional[str], doc: Optional[dict]) -> Optional[str]:
    """Why the record ``rec`` no longer answers the node run ``row`` in review (None: it does)."""
    if row is None:
        return "node_not_in_run"
    if row.get("result_doc_id") != rec["result_doc_id"]:
        return "result_changed"
    if row.get("status") != REVIEW_STATUS:
        return "node_not_in_review"
    if repository.attempt_key(active_attempt) != rec["attempt_id"]:
        return "attempt_changed"
    if doc is not None and rec.get("revision_no") is not None \
            and int(doc.get("revision_no") or 0) != int(rec["revision_no"]):
        return "revision_changed"
    return None


def _done_with(rec: dict, row: Optional[dict], run_id: str) -> Optional[bool]:
    """For a node already done: True when it is done with this record's own attempt and result, False when with another
    (an older verdict); None when the node is not done."""
    if row is None or row.get("status") != DONE_STATUS:
        return None
    return row.get("result_doc_id") == rec["result_doc_id"] \
        and repository.attempt_key(legacy.current_attempt_id(run_id, rec["node_key"])) == rec["attempt_id"]


def settle_reason(run: Optional[dict], rec: dict) -> Optional[str]:
    """What the Flow's ``recorded`` row ``rec`` is now: ``ALREADY_DONE`` (its node is done with its own attempt and
    result), a supersede reason, or None (it still answers its node in review — or the run is stopping)."""
    if run is None or run["run_id"] != rec["run_id"]:
        return "run_not_active"
    if run.get("status") == legacy.STOPPING:
        return None
    row = legacy.node_run(run["run_id"], rec["node_key"])
    done = _done_with(rec, row, run["run_id"])
    if done is True:
        return ALREADY_DONE
    if done is False:
        return "result_changed" if row.get("result_doc_id") != rec["result_doc_id"] else "attempt_changed"
    return stale_reason(rec, row, legacy.active_attempt_id(run["run_id"], rec["node_key"]),
                        legacy.document(rec["result_doc_id"]))


def legacy_run(run: dict) -> bool:
    """A run started before the service was installed (``repository.installed_at``): its rows predate the records."""
    stamp = repository.installed_at()
    started = str(run.get("started_at") or "")
    return bool(stamp) and bool(started) and started < stamp


def legacy_attempt(attempt_id: Optional[str]) -> bool:
    """The node's current attempt predates the installation too (or the node has none: opened before migration 151)."""
    if not attempt_id:
        return True
    stamp = repository.installed_at()
    created = legacy.attempt_created_at(attempt_id) or ""
    return bool(stamp) and bool(created) and created < stamp


# ── ② applying it ────────────────────────────────────────────────────────────

def apply(flow_id: str, completion_id: str) -> str:
    """Make the node follow its completion, in the Flow guard (② / ③); the record's status afterwards (or ``missing``).

    The node is done, its attempt ended and the next cards opened in the guard's one transaction — or, for a code node,
    its merge is requested and the merge's end does it. A record that no longer answers the node is ``superseded``; a
    run stopping keeps it ``recorded`` (the reconciliation of the run's next command reads it again)."""
    if legacy.in_transaction():
        raise AssertionError("card completion apply must not run inside an open transaction")
    jobs: list = []

    def body() -> str:
        rec = repository.by_id(completion_id)
        if rec is None:
            return "missing"
        if rec["status"] != RECORDED:
            return rec["status"]
        if not legacy.graph_owned(flow_id):
            return rec["status"]
        run = legacy.active_run(flow_id)
        reason = settle_reason(run, rec)
        if reason == ALREADY_DONE:                             # done already (a repeated apply): it is this one's
            repository.set_status(completion_id, (RECORDED,), APPLIED, ALREADY_DONE)
            return APPLIED
        if reason is not None:
            repository.set_status(completion_id, (RECORDED,), SUPERSEDED, reason)
            _log.info("card completion superseded flow=%s node=%s completion=%s: %s", flow_id, rec["node_key"],
                      completion_id, reason)
            return SUPERSEDED
        if run.get("status") == legacy.STOPPING:
            return rec["status"]
        legacy.reconcile_node(run, rec["node_key"], jobs)
        again = repository.by_id(completion_id) or rec
        return again["status"]

    outcome = legacy.guarded(flow_id, body)
    legacy.run_jobs(jobs)
    return outcome


def complete(flow_id: str, doc_id: str, *, cause: str, requested_by: Optional[str] = None,
             node_key: Optional[str] = None, attempt_id: Optional[str] = None, verdict: Optional[dict] = None,
             actor: Optional[tuple[str, Optional[str]]] = None, author: Optional[str] = None) -> dict:
    """① then ②: the completion of the node whose result in review is ``doc_id``. A repeated call returns the existing
    record (applied again: a no-op). Raises ``CompletionRefused`` when no node in review takes the result. The returned
    ``status`` is the record's: only ``applied`` / ``recorded`` are a completion (``superseded`` / ``revoked`` are not).
    ``requested_by`` is the run's ``started_by`` whenever the run names one; ``author`` (a person's submission) is read
    from the submission's records when not given."""
    found = target_of(flow_id, doc_id, node_key)
    if found is None:
        existing = [r for r in repository.of_document(flow_id, doc_id) if r["status"] in LIVE]
        if existing:
            return {**existing[-1], "created": False}
        raise CompletionRefused("node_not_in_review", doc_id=doc_id)
    active = found["attempt_id"]
    if attempt_id and active and attempt_id != active:
        raise CompletionRefused("attempt_changed", attempt_id=attempt_id, active_attempt_id=active)
    doc = legacy.document(doc_id) or {}
    revision = int(doc.get("revision_no") or 0)
    if cause == AI_REVIEW_PASSED and verdict is None:
        verdict = passing_verdict(doc_id, revision)
    if cause == HUMAN_SUBMITTED and author is None and actor is None:
        author = legacy.submission_author(doc)
    kind, actor_id = actor or actor_of(cause, verdict, author)
    rec, created = record(flow_id, found["run"]["run_id"], found["node_key"], active, doc_id, cause=cause,
                          actor_kind=kind, actor_id=actor_id,
                          requested_by=requested_by_of(found["run"]) or requested_by,
                          verdict_id=(verdict or {}).get("id"), revision_no=revision)
    if rec["status"] in TERMINAL:
        return {**rec, "created": created}
    status = apply(flow_id, rec["completion_id"])
    return {**rec, "status": status, "created": created}


# ── the Graph's ports (``card_completion_ports``) ────────────────────────────

def completion_of(flow_id: str, run: dict, node_key: str, doc: dict) -> Optional[dict]:
    """``card_completion_ports.completion_of``: is the node's result ``doc`` (the node in review) complete?

    ``{"completion_id": <id>, "legacy": False}`` for the live record of the node's active attempt and result that still
    answers it (revision included); ``{"completion_id": None, "legacy": True}`` for an existing run's approved result
    with no completion row of the node in the run and a current attempt that also predates the service; else None — a
    person's approval of the document on the v0.1 review screen is no completion, and a superseded, revoked or stale
    record is never read past to the document's approval."""
    run_id = run["run_id"]
    attempt = legacy.active_attempt_id(run_id, node_key)
    rec = repository.by_key(run_id, node_key, attempt, doc["doc_id"])
    if rec is not None:
        if rec["status"] == APPLIED:
            return {"completion_id": rec["completion_id"], "legacy": False}
        if rec["status"] == RECORDED and stale_reason(rec, legacy.node_run(run_id, node_key), attempt, doc) is None:
            return {"completion_id": rec["completion_id"], "legacy": False}
        return None
    if repository.of_node(run_id, node_key):
        return None                                            # the node has records: they alone decide
    if legacy_run(run) and legacy_attempt(attempt) and doc.get("doc_review_status") in legacy.APPROVED:
        return {"completion_id": None, "legacy": True}
    return None


def applied(flow_id: str, run_id: str, node_key: str, result_doc_id: Optional[str],
            event_seq: Optional[int] = None) -> Optional[str]:
    """``card_completion_ports.applied``, inside ``apply_decision`` (the node's done, before its attempt ends): the live
    record of the node's active attempt and result turns ``applied`` in the same transaction; its id (the node event's
    ``completion_id``), or None (an existing run's approved result)."""
    if not result_doc_id or not repository.available():
        return None
    rec = repository.live(run_id, node_key, legacy.active_attempt_id(run_id, node_key), result_doc_id)
    if rec is None:
        return None
    if rec["status"] == RECORDED:
        repository.set_status(rec["completion_id"], (RECORDED,), APPLIED, None, event_seq)
    return rec["completion_id"]


def unsettled(flow_id: str, run: Optional[dict]) -> bool:
    """``card_completion_ports.unsettled`` (read only, before a run read): a ``recorded`` row of the Flow that
    ``settle`` would change."""
    return any(settle_reason(run, rec) is not None for rec in repository.recorded_of_group(flow_id))


def settle(flow_id: str, run: Optional[dict]) -> int:
    """``card_completion_ports.settle``, inside the Flow guard (the reconciliation of a run read or a command): every
    ``recorded`` row of the Flow is compared with its node's current attempt, result and revision — ``superseded`` when
    it no longer answers it, ``applied`` when the node is done with its own attempt and result. The rows changed."""
    changed = 0
    for rec in repository.recorded_of_group(flow_id):
        reason = settle_reason(run, rec)
        if reason is None:
            continue
        if reason == ALREADY_DONE:
            changed += repository.set_status(rec["completion_id"], (RECORDED,), APPLIED, ALREADY_DONE)
        else:
            changed += repository.set_status(rec["completion_id"], (RECORDED,), SUPERSEDED, reason)
            _log.info("card completion settled flow=%s node=%s completion=%s: superseded (%s)", flow_id,
                      rec["node_key"], rec["completion_id"], reason)
    return changed


def revoke_rewound(run_id: str, node_keys) -> list[str]:
    """A Time Machine rewind took ``node_keys`` back (inside its guard): their live records -> ``revoked``."""
    return repository.revoke(run_id, node_keys, TIME_MACHINE_REASON)


def node_completions(run_id: str, node_key: str) -> list[dict]:
    """Every completion of the node in the run, oldest first (the run read's evidence: cause, actor_kind, requested_by)."""
    return repository.of_node(run_id, node_key)


__all__ = ["AI_REVIEW_PASSED", "NO_REVIEW_SUBMITTED", "HUMAN_SUBMITTED", "CAUSES", "SYSTEM", "AI", "USER",
           "ACTOR_KINDS", "CompletionRefused", "target_of", "requested_by_of", "passing_verdict", "review_provider",
           "actor_of", "record", "stale_reason", "settle_reason", "legacy_run", "legacy_attempt", "apply", "complete",
           "completion_of", "applied", "unsettled", "settle", "revoke_rewound", "node_completions",
           "RECORDED", "APPLIED", "SUPERSEDED", "REVOKED", "LIVE", "TERMINAL"]
