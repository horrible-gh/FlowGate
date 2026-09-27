"""Deferred final-approval intent carried by a conflict merge session.

flowgate.default.0555 T0008 (T#2), contract in D0005 §3.5/§3.6/§3.8/§3.9.

A final approval whose Git finalize ends in a conflict cannot be decided inside
that one request: a person still has to resolve the conflict and a reviewer still
has to approve the resolution.  The approval therefore is not "failed", it is
*parked* — and what has to survive until the merge really terminates is "when this
merge finishes, WHICH approval does it finish".

That record is an **approval intent**.  It lives in the `context` JSON of the merge
session the conflict opened, written by the same INSERT that creates the session
(``create_session(context=...)``), so the "session without an intent" orphan D0005
§3.5 calls out cannot exist: either both rows land or neither does.  No new table,
no in-memory registry — the session row is already durable, already survives a
process restart, and is already what the merge-review completion path reads.

The merge session id is deliberately NOT reused as the intent id.  A session says
what is being merged; an intent says which AC document that merge will approve, and
those are different facts with different lifetimes (§3.8).

``db_git`` is reached through the ``git_service`` facade inside each function rather
than imported at module scope: a top-level binding would shadow the name the test
corpus patches on ``git_service`` and silently exempt this module from it (0550
NR0025 권고2, enforced by ``test_git_facade_seam_scope_0550``).
"""
from __future__ import annotations

import logging
from typing import Optional

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db.connection import now_iso

_log = logging.getLogger(__name__)

INTENT_KEY = "final_approval_intent"
CONSUMED_KEY = "final_approval_intent_consumed"
DISCARDED_KEY = "final_approval_intent_discarded"
CLEAN_RETRY_SOURCE = "clean_final_approval"
CLEAN_TERMINAL_STATUSES = frozenset({"merged", "pushed", "discarded", "archived", "stashed"})
CLEAN_RETRY_ACTIONS = frozenset({"merge", "merge_only", "push", "commit_push", "stash"})

# The authority for this approval was checked when the intent was recorded, by the
# orchestrator's own precheck, against the live permissions of `requested_by`.  The
# deferred half re-checks the facts D0005 §3.8 lists (document, group, type, state,
# intent ownership) — not the permission grant, which is what the stored intent IS.
_DEFERRED_PERMISSIONS = frozenset({"document.approve"})


def build_intent(
    *,
    approval_intent_id: str,
    group_id: str,
    ac_doc_id: str,
    requested_by: str,
    git_action: str,
) -> dict:
    """The §3.8 payload, in the shape the session context stores it."""
    return {
        "approval_intent_id": approval_intent_id,
        "group_id": group_id,
        "ac_doc_id": ac_doc_id,
        "requested_by": requested_by,
        "git_action": git_action,
        "created_at": now_iso(),
    }


def intent_of_context(context: Optional[dict]) -> Optional[dict]:
    intent = (context or {}).get(INTENT_KEY)
    return intent if isinstance(intent, dict) and intent.get("approval_intent_id") else None


def intent_of_session(session: Optional[dict]) -> Optional[dict]:
    from modules.flow_gate.services import git_service as _gs

    return intent_of_context(_gs.db_git.session_context(session))


def find_intent_session(group_id: str) -> tuple[Optional[dict], Optional[dict]]:
    """The newest session of this group that still carries an unconsumed intent.

    The open session is the answer while the conflict is being worked on.  After
    ``_complete_merge_review`` closes the session, a D0005 §3.9 re-approval still
    has to find the very same intent on the now-closed row — so closed sessions are
    searched too, newest first, and the first one still holding an intent wins.
    """
    from modules.flow_gate.services import git_service as _gs

    for session in _gs.db_git.sessions_by_group(group_id):
        if _gs.db_git.session_kind(session) != _gs.db_git.SESSION_KIND_MERGE:
            continue
        intent = intent_of_session(session)
        if intent is not None:
            return session, intent
    return None, None


def build_clean_retry(intent: dict, *, terminal_status: str, merge_commit: Optional[str]) -> dict:
    """Snapshot the clean A11 approval capability beside terminal Git state."""
    if terminal_status not in CLEAN_TERMINAL_STATUSES:
        raise ValueError(f"invalid clean approval terminal status: {terminal_status!r}")
    return {
        "source": CLEAN_RETRY_SOURCE,
        "intent": dict(intent),
        "terminal_status": terminal_status,
        "terminal_git_action": intent.get("git_action"),
        "merge_commit": merge_commit,
        "approval_completed": False,
        "recorded_at": now_iso(),
    }


def clean_retry_of_state(state: Optional[dict]) -> Optional[dict]:
    from modules.flow_gate.services import git_service as _gs

    return _gs.db_git.final_approval_retry_context(state)


def find_clean_retry(group_id: str) -> tuple[Optional[dict], Optional[dict]]:
    """Load A11 retry evidence from group_git_state, never process memory."""
    from modules.flow_gate.services import git_service as _gs

    state = _gs.db_git.get_state(group_id)
    return state, clean_retry_of_state(state)


def record_clean_retry(
    *,
    group_id: str,
    intent: dict,
    terminal_status: str,
    merge_commit: Optional[str],
) -> dict:
    """Persist terminal Git and its clean-approval retry snapshot under the Git lock."""
    from modules.flow_gate.services import git_service as _gs

    retry = build_clean_retry(
        intent, terminal_status=terminal_status, merge_commit=merge_commit,
    )
    if terminal_status in {"merged", "pushed"}:
        _gs.db_git.set_status_with_final_approval_retry(
            group_id, terminal_status, retry, merge_commit=merge_commit,
        )
    elif terminal_status in {"archived", "stashed"}:
        # Archive terminal labels are response-level facts, not legacy state values.
        # Neutralize the live finalize status while atomically parking the retry;
        # the archived refs already own the bytes and approval completion performs
        # the delayed slot cleanup.
        _gs.db_git.set_status_with_final_approval_retry(
            group_id, "none", retry, merge_commit=None,
        )
    else:
        # discarded is likewise a response-level terminal fact, but its no-work
        # path has already retained the neutral state.
        _gs.db_git.set_final_approval_retry(group_id, retry)
    return retry


def validate_clean_retry(
    retry: dict,
    *,
    group_id: str,
    expected_ac_doc_id: Optional[str] = None,
) -> tuple[Optional[dict], Optional[str]]:
    """Fail-closed admission for a clean terminal approval-only retry."""
    from modules.flow_gate.services import git_service as _gs

    if not isinstance(retry, dict):
        return None, "retry_evidence_invalid"
    intent = retry.get("intent")
    if retry.get("source") != CLEAN_RETRY_SOURCE or not isinstance(intent, dict):
        return None, "retry_evidence_invalid"
    if retry.get("approval_completed") is not False:
        return None, "retry_already_completed"
    if retry.get("terminal_status") not in CLEAN_TERMINAL_STATUSES:
        return None, "retry_git_not_terminal"
    if intent.get("group_id") != group_id:
        return None, "intent_group_mismatch"
    if expected_ac_doc_id is not None and intent.get("ac_doc_id") != expected_ac_doc_id:
        return None, "intent_document_mismatch"
    if (
        not intent.get("approval_intent_id")
        or intent.get("git_action") not in CLEAN_RETRY_ACTIONS
        or retry.get("terminal_git_action") != intent.get("git_action")
    ):
        return None, "retry_request_mismatch"

    state = _gs.db_git.get_state(group_id)
    stored = clean_retry_of_state(state)
    stored_intent = (stored or {}).get("intent") or {}
    if stored_intent.get("approval_intent_id") != intent.get("approval_intent_id"):
        return None, "intent_already_consumed"
    if stored != retry:
        return None, "retry_request_mismatch"
    terminal_status = retry.get("terminal_status")
    state_status = (state or {}).get("status") or "none"
    if (
        (terminal_status in {"merged", "pushed"} and state_status != terminal_status)
        or (terminal_status in {"discarded", "archived", "stashed"} and state_status != "none")
    ):
        return None, "retry_git_state_mismatch"

    doc = db_documents.get_by_id(intent.get("ac_doc_id") or "")
    if doc is None:
        return None, "intent_document_missing"
    if doc.get("group_id") != group_id:
        return None, "intent_group_mismatch"
    if str(doc.get("type_code") or "").upper() != "AC":
        return None, "intent_document_not_ac"
    if doc.get("doc_review_status") != "pending_review":
        return None, "intent_document_not_pending"
    root = db_documents.get_by_id(doc.get("target_id") or "")
    if (
        root is None
        or root.get("group_id") != group_id
        or str(root.get("type_code") or "").upper() not in {"R", "B"}
        or root.get("doc_review_status") != "wf_in_progress"
    ):
        return None, "intent_root_not_in_progress"
    return doc, None


def consume_clean_retry(group_id: str, approval_intent_id: str) -> bool:
    from modules.flow_gate.services import git_service as _gs

    return _gs.db_git.consume_final_approval_retry(group_id, approval_intent_id)


def group_is_final_approval_bound(group_id: str) -> bool:
    """D0005 §3.11 — is this group's Git work coupled to a waiting final approval?

    Judged at read time from the one fact that defines it (an open merge session
    carrying an intent), never stored as its own flag: a second copy of this would
    be a second thing to keep in step with the session.
    """
    from modules.flow_gate.services import git_service as _gs

    session = _gs.db_git.get_open_session_by_group(group_id)
    if session is None or _gs.db_git.session_kind(session) != _gs.db_git.SESSION_KIND_MERGE:
        return False
    return intent_of_session(session) is not None


def validate_intent(
    intent: dict, *, group_id: str, merge_id: int,
) -> tuple[Optional[dict], Optional[str]]:
    """Re-check the §3.8 facts just before consuming.  ``(document, reason)``.

    A mismatch is never repaired by guessing another AC: the group's Git is already
    terminal, so the honest outcome is "Git finished, approval did not" and a person
    decides what to approve next.
    """
    from modules.flow_gate.services import git_service as _gs

    if intent.get("group_id") != group_id:
        return None, "intent_group_mismatch"
    doc = db_documents.get_by_id(intent.get("ac_doc_id") or "")
    if doc is None:
        return None, "intent_document_missing"
    if doc.get("group_id") != group_id:
        return None, "intent_group_mismatch"
    if str(doc.get("type_code") or "").upper() != "AC":
        return None, "intent_document_not_ac"
    if doc.get("doc_review_status") != "pending_review":
        return None, "intent_document_not_pending"
    stored = intent_of_session(_gs.db_git.get_session(merge_id))
    if stored is None or stored.get("approval_intent_id") != intent.get("approval_intent_id"):
        return None, "intent_already_consumed"
    return doc, None


def consume_intent(merge_id: int, approval_intent_id: str) -> bool:
    """Conditionally clear one intent; True only when THIS call cleared it.

    Called from inside the approval transaction (``commit_final_approval``'s
    consume hook), so a False here rolls the AC approval and the root completion
    back with it — D0005 §3.8's "the conditional update is the last safety net"
    under the project Git lock that already serialised the callers.
    """
    from modules.flow_gate.services import git_service as _gs

    session = _gs.db_git.get_session(merge_id)
    if session is None:
        return False
    context = _gs.db_git.session_context(session)
    intent = intent_of_context(context)
    if intent is None or intent.get("approval_intent_id") != approval_intent_id:
        return False
    next_context = dict(context)
    next_context.pop(INTENT_KEY, None)
    next_context[CONSUMED_KEY] = {
        "approval_intent_id": approval_intent_id,
        "ac_doc_id": intent.get("ac_doc_id"),
        "consumed_at": now_iso(),
    }
    return _gs.db_git.cas_session_context(merge_id, session.get("context"), next_context)


def discard_intent(merge_id: int) -> Optional[dict]:
    """Drop the intent a merge abort invalidates (D0005 §3.6 B10).

    The abort ends the Git attempt the approval was riding on, so the approval has
    to start over from a fresh id — the discarded one is kept only as a trace and
    is never reusable, because nothing reads it back.
    """
    from modules.flow_gate.services import git_service as _gs

    session = _gs.db_git.get_session(merge_id)
    intent = intent_of_session(session)
    if intent is None:
        return None
    context = dict(_gs.db_git.session_context(session))
    context.pop(INTENT_KEY, None)
    context[DISCARDED_KEY] = {
        "approval_intent_id": intent.get("approval_intent_id"),
        "ac_doc_id": intent.get("ac_doc_id"),
        "discarded_at": now_iso(),
        "reason": "merge_abort",
    }
    _gs.db_git.set_session_context(merge_id, context)
    return intent


def _verdict(
    *,
    approved: bool,
    stage: str,
    intent: dict,
    merge_id: Optional[int],
    error: Optional[dict] = None,
) -> dict:
    verdict = {
        "approved": approved,
        "document_status": "approved" if approved else "pending_review",
        "root_status": "wf_done" if approved else "wf_in_progress",
        "stage": stage,
        "deferred": False,
        "approval_intent_id": intent.get("approval_intent_id"),
        "ac_doc_id": intent.get("ac_doc_id"),
        "merge_id": merge_id,
    }
    if error is not None:
        verdict["error"] = error
    return verdict


def commit_clean_retry(group_id: str, retry: dict) -> dict:
    """Commit only the approval backed by one clean terminal retry snapshot."""
    from modules.flow_gate.workflow.pipeline_service import commit_final_approval

    intent = retry.get("intent") if isinstance(retry, dict) else {}
    doc, reason = validate_clean_retry(retry, group_id=group_id)
    if doc is None:
        _log.warning(
            "clean final approval for %s not applied (%s); git stays terminal",
            group_id, reason,
        )
        return _verdict(
            approved=False, stage="intent_mismatch", intent=intent or {}, merge_id=None,
            error={"code": reason, "message": "the recorded clean approval retry no longer matches"},
        )

    def _consume(_doc: dict, _root: dict) -> bool:
        return consume_clean_retry(group_id, intent["approval_intent_id"])

    try:
        commit_final_approval(
            doc_id=doc["doc_id"],
            actor_user_id=intent.get("requested_by") or "",
            user_permissions=set(_DEFERRED_PERMISSIONS),
            consume_hook=_consume,
        )
    except Exception as exc:  # noqa: BLE001 — Git remains terminal on every failure
        _log.warning(
            "clean final approval transaction failed for %s", group_id, exc_info=True,
        )
        return _verdict(
            approved=False, stage="approval_commit", intent=intent, merge_id=None,
            error={"code": "approval_commit_failed", "message": str(exc)},
        )
    verdict = _verdict(
        approved=True, stage="complete", intent=intent, merge_id=None,
    )
    verdict["retry_source"] = CLEAN_RETRY_SOURCE
    return verdict


def commit_deferred_approval(group_id: str, merge_id: int, intent: dict) -> dict:
    """Finish the parked approval now that the merge really is terminal.

    Called by the merge-review completion path while it still owns the project Git
    lock (D0005 §3.6 B7/B8).  The AC approval, the root completion and the intent
    consume are one transaction: all three or none.  Git is never undone here — a
    failed transaction leaves merged Git + a pending AC + a live intent, which is
    exactly the §3.9 state a re-approval knows how to finish.
    """
    # Deferred import: the Git service must not take a hard dependency on the
    # workflow package (D0005 §5 — the import cycle is why this is a hook).
    from modules.flow_gate.workflow.pipeline_service import commit_final_approval

    doc, reason = validate_intent(intent, group_id=group_id, merge_id=merge_id)
    if doc is None:
        _log.warning(
            "deferred final approval for %s not applied (%s); git stays terminal",
            group_id, reason,
        )
        return _verdict(
            approved=False, stage="intent_mismatch", intent=intent, merge_id=merge_id,
            error={"code": reason, "message": "the recorded approval intent no longer matches"},
        )

    def _consume(_doc: dict, _root: dict) -> bool:
        return consume_intent(merge_id, intent["approval_intent_id"])

    try:
        commit_final_approval(
            doc_id=doc["doc_id"],
            actor_user_id=intent.get("requested_by") or "",
            user_permissions=set(_DEFERRED_PERMISSIONS),
            consume_hook=_consume,
        )
    except Exception as exc:  # noqa: BLE001 — every failure means "not approved"
        _log.warning(
            "deferred final approval transaction failed for %s", group_id, exc_info=True,
        )
        return _verdict(
            approved=False, stage="approval_commit", intent=intent, merge_id=merge_id,
            error={"code": "approval_commit_failed", "message": str(exc)},
        )
    return _verdict(approved=True, stage="complete", intent=intent, merge_id=merge_id)
