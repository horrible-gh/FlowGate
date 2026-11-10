"""What a recorded reason means for a card in review (flowgate.v02.0119 T#3, T-2). Pure: no DB, no import of a stage.

The reasons are the B-1 vocabulary the card stage records (``card_settings.legacy_adapter``: ``ISSUER_MISSING`` …,
``card_settings.assigned_review``: ``ASSIGNMENT_UNREADABLE`` …), repeated here as strings so this module reads a record
without importing the stage that wrote it.

flowgate.v02.0124 T#1 (0003-NR §5.1.2): ``command_rerequest_due`` — the ``needs_intervention`` reasons an explicit step /
full command may request the card's result review again for (the review loop's budget spent). ``retry_due`` is unchanged:
nothing is retried automatically.
"""
from __future__ import annotations

from typing import Iterable, Optional

NEEDS_INTERVENTION = "needs_intervention"
SYSTEM_ERROR = "system_error"
KINDS = (NEEDS_INTERVENTION, SYSTEM_ERROR)

# A card in review with no AI working on it, no integration waiting and no reason recorded (a lost stage the
# reconciliation did not take, a result nothing follows): a person looks at it — never approves it.
COMPLETION_MISSING = "completion_missing"

# The card's own outcome after its automatic rework: the loop ended without passing, or its AI run ended abnormally.
REVIEW_NOT_PASSED = "review_not_passed:"
RUN_NOT_CLEAN = "run_not_clean:"
INTERVENTION_PREFIXES = (REVIEW_NOT_PASSED, RUN_NOT_CLEAN)

# The automatic completion path failed (0004-CH seq 9 T-2: the auto_approval_skipped family): a system error, retried.
ISSUER_MISSING = "issuer_missing"
APPROVAL_REFUSED = "approval_refused"
REVIEW_NOT_STARTED = "review_not_started"
REVIEW_RESULT_UNKNOWN = "review_result_unknown"
ASSIGNMENT_UNREADABLE = "assignment_unreadable"      # an assigned attempt's stage could not be read (T §4.6)
STAGE_NOT_PERSON = "stage_not_person"                # an assigned attempt's result stage in no state that follows it
SYSTEM_ERROR_REASONS = frozenset({ISSUER_MISSING, APPROVAL_REFUSED, REVIEW_NOT_STARTED, REVIEW_RESULT_UNKNOWN,
                                  ASSIGNMENT_UNREADABLE, STAGE_NOT_PERSON})
# Not a system error: ``issuer_lacks_approve`` (a person's work plan whose run issuer lacks ``document.approve``, 0083
# WP-4). No retry can give the issuer that authority, and the command result already counts it as a person's wait
# (``graph_run.command_result.PERSON_APPROVAL_WAITS``): it is ``needs_intervention``.
ISSUER_LACKS_APPROVE = "issuer_lacks_approve"

# How many system error records of one submission (the node's attempt and document) the reconciliation retries up to.
# Each retry that fails records its reason again, so the count is the records themselves: no new store, nothing
# converted. Past the limit the card stays a system error (shown, never a person's approval).
SYSTEM_ERROR_RETRY_LIMIT = 3


def kind_of(reason: Optional[str]) -> str:
    """``SYSTEM_ERROR`` for the automatic path's failures, else ``NEEDS_INTERVENTION`` — the not-passed and abnormal-end
    reasons, a person's stage wait (``instruction_not_approved``), a reason this version does not know and none at all
    (a person looks at the card; nothing approves it)."""
    return SYSTEM_ERROR if reason in SYSTEM_ERROR_REASONS else NEEDS_INTERVENTION


def is_system_error(reason: Optional[str]) -> bool:
    return kind_of(reason) == SYSTEM_ERROR


def retry_due(reasons: Iterable[str]) -> bool:
    """May the reconciliation try the automatic path again for a submission whose recorded reasons are ``reasons``
    (oldest first)? None recorded: yes (the first try). A needs_intervention reason recorded: no automatic retry.
    A general review card is not a direct Time Machine target (impact requires a done node).
    Only system errors: while fewer than ``SYSTEM_ERROR_RETRY_LIMIT``."""
    found = [r for r in reasons if r]
    if not found:
        return True
    if any(not is_system_error(r) for r in found):
        return False
    return len(found) < SYSTEM_ERROR_RETRY_LIMIT


# flowgate.v02.0124 T#1 (0003-NR §5.1.2 condition 2): the stop reasons of a review loop that spent its budget — its
# rounds, its rework retries or its total time. Only these are requested again by an explicit command; a hold
# (``review_verdict_hold``), a stall, an orphaned restart, an abnormal end (``run_not_clean:*``), ``issuer_lacks_approve``,
# ``instruction_not_approved``, ``completion_missing`` and a reason this version does not know are not.
COMMAND_REREQUEST_STOPS = frozenset({"retry_exhausted", "review_count_exhausted", "total_timeout"})


def command_rerequest_due(reason: Optional[str]) -> bool:
    """May an explicit step / full command request the result review of a card in ``needs_intervention`` again, its
    latest recorded reason ``reason``? Only ``review_not_passed:<a budget spent>`` (``COMMAND_REREQUEST_STOPS``). Never an
    automatic retry: ``retry_due`` still answers no for every needs_intervention reason (0003-NR §5.1.4)."""
    if not isinstance(reason, str) or not reason.startswith(REVIEW_NOT_PASSED):
        return False
    return reason[len(REVIEW_NOT_PASSED):] in COMMAND_REREQUEST_STOPS


def command_reentry_due(reason: Optional[str]) -> bool:
    """0126/0159: explicit intent also releases a verdict hold, restart orphan or failed path; never a card Hold.

    A missing completion is inspected against durable document/assignment evidence by the caller.
    Permission, unknown reasons and instruction approval gates remain blocked.
    """
    return (command_rerequest_due(reason) or reason in (
        REVIEW_NOT_PASSED + "review_verdict_hold", REVIEW_NOT_PASSED + "restart_orphaned", COMPLETION_MISSING,
        REVIEW_NOT_STARTED, REVIEW_RESULT_UNKNOWN, APPROVAL_REFUSED, STAGE_NOT_PERSON)
        or isinstance(reason, str) and reason.startswith(RUN_NOT_CLEAN))


def state(reason: Optional[str]) -> dict:
    """``{"kind", "reason"}`` of a card in review nothing completes, its latest recorded reason ``reason``."""
    return {"kind": kind_of(reason), "reason": reason or COMPLETION_MISSING}
