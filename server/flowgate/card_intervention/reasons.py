"""What a recorded reason means for a card in review (flowgate.v02.0119 T#3, T-2). Pure: no DB, no import of a stage.

The reasons are the B-1 vocabulary the card stage records (``card_settings.legacy_adapter``: ``ISSUER_MISSING`` …,
``card_settings.assigned_review``: ``ASSIGNMENT_UNREADABLE`` …), repeated here as strings so this module reads a record
without importing the stage that wrote it.
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
    (oldest first)? None recorded: yes (the first try). A needs_intervention reason recorded: no — the card waits for a
    memo rework / re-review or the Time Machine. Only system errors: while fewer than ``SYSTEM_ERROR_RETRY_LIMIT``."""
    found = [r for r in reasons if r]
    if not found:
        return True
    if any(not is_system_error(r) for r in found):
        return False
    return len(found) < SYSTEM_ERROR_RETRY_LIMIT


def state(reason: Optional[str]) -> dict:
    """``{"kind", "reason"}`` of a card in review nothing completes, its latest recorded reason ``reason``."""
    return {"kind": kind_of(reason), "reason": reason or COMPLETION_MISSING}
