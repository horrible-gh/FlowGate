"""The four server-owned register axes, compared once and recorded once.

flowgate.default.0492 T0018 — implements L0010 §2.2-§2.4 (R5/R6) and DB0011 §2.1.

Two boundaries ask the same question and must answer it identically:

* ``register_dispatch`` — the API provider proxy, before it builds an /inbox body at all
  (:func:`bind_registration_context`);
* ``inbox`` — the four /inbox handlers, which re-derive the axes from the request they
  actually received (:func:`inbox_mismatches`).

Both compare ``action``, ``project``, ``group``, ``doc`` in that fixed order, so a
rejection always names the same axis for the same fault no matter which side caught it,
and a test can pin the axis instead of the message.

Nothing here reads model input. The expected side comes from the run, the actual side
from the verified token, and the group axis from the document's DB row — never from a
group substring guessed out of a document id (L0010 §2.3).

Fail-closed by construction: an axis whose value cannot be resolved gets a per-call unique
sentinel, so an unresolvable value never compares equal to anything, including another
unresolvable value.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import uuid
from typing import Any, Optional

from modules.flow_gate.db import documents as db_docs

logger = logging.getLogger(__name__)

# L0010 §1 binding_axis_order — the single source of truth for "which axis is the
# representative one" and for the order of the axes array.
AXIS_ORDER = ("action", "project", "group", "doc")

BOUNDARY_DISPATCH = "register_dispatch"
BOUNDARY_INBOX = "inbox"
# Not a live boundary: the label a backfilled pre-T0018 register_errors element carries,
# because it never recorded an axis and inventing one would fabricate evidence.
BOUNDARY_LEGACY = "legacy_unclassified"
BOUNDARIES = (BOUNDARY_DISPATCH, BOUNDARY_INBOX, BOUNDARY_LEGACY)

CODE_FORBIDDEN = "forbidden"
REASON_BINDING = "context_binding_mismatch"
REASON_PERMISSION = "permission_denied"
BINDING_MESSAGE = "Context binding mismatch. Use the correct token."

# The only token kind whose empty doc_ref may relax the doc axis (L0010 §2.3). An
# API-provider run token is never this kind.
TOKEN_KIND_HUMAN_LEGACY = "human_legacy"
TOKEN_KIND_API_RUN = "api_run_bound"

_LOG_EVENT = "inbox_context_binding_rejected"


class BindingError(Exception):
    """A context-binding rejection carrying its axes and correlation id."""

    def __init__(self, record: dict):
        self.record = record
        super().__init__(record.get("axis") or REASON_BINDING)


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _unresolved() -> str:
    """A value that equals nothing, not even another unresolved value."""
    return f"\x00unresolved:{uuid.uuid4().hex}"


def canonical_context(action: Any, project: Any, group: Any, doc: Any) -> dict[str, str]:
    """Canonical four-axis context. Aliases are not accepted; only ``action`` is case-folded
    (it is a server enum), because project/group/document ids are stored case-sensitively."""
    action_text = _text(action)
    return {
        "action": action_text.lower() if action_text else _unresolved(),
        "project": _text(project) or _unresolved(),
        "group": _text(group) or _unresolved(),
        "doc": _text(doc) or _unresolved(),
    }


def compare_axes(expected: dict, actual: dict) -> list[str]:
    return [axis for axis in AXIS_ORDER if expected.get(axis) != actual.get(axis)]


def group_of_doc(doc_id: Any) -> Optional[str]:
    """The document's group as the DB records it, or None. Never parsed out of the id."""
    doc_key = _text(doc_id)
    if not doc_key:
        return None
    try:
        row = db_docs.get_by_id(doc_key)
    except Exception:
        logger.warning("register binding: group lookup failed", exc_info=True)
        return None
    return _text((row or {}).get("group_id"))


def token_axes(token_rec: dict) -> tuple[dict[str, str], Optional[str], Optional[str]]:
    """``(context, group_token_db, group_token_resolved)`` for a verified token record.

    A token row minted before the group column existed carries no group; it is completed
    from the DB group of its own doc_ref, and a failed lookup leaves the axis unresolved
    (which is a mismatch) rather than passing.
    """
    group_db = _text(token_rec.get("group_id"))
    group_resolved = group_db or group_of_doc(token_rec.get("doc_ref"))
    context = canonical_context(
        token_rec.get("action_scope"),
        token_rec.get("project"),
        group_resolved,
        token_rec.get("doc_ref"),
    )
    return context, group_db, group_resolved


def token_kind(token_rec: dict) -> str:
    """``api_run_bound`` when the token row itself names an AI run or a provider.

    The tokens table has no kind column (migration 075a), so the two columns the AI-invoke
    issuer fills — ``ai_run_id`` and ``provider_id`` — ARE the durable record of what kind
    of token this is. Deriving the kind from them keeps the legacy review relaxation off
    every provider-run token without adding a field an issuer could forget to set.
    """
    if _text(token_rec.get("ai_run_id")) or _text(token_rec.get("provider_id")):
        return TOKEN_KIND_API_RUN
    return TOKEN_KIND_HUMAN_LEGACY


def log_relaxation(
    *,
    correlation_id: str,
    token_id: Any,
    action_handler: str,
    unjudged_axes: Optional[list] = None,
) -> None:
    """Audit line for a review that passed only because a human legacy token's doc_ref was
    empty (L0010 §2.3). Not a failure row: nothing was rejected, so nothing is stored in
    register_context_failures — but the waiver itself must be findable.

    `relaxed_axis` stays `doc`, the axis whose KNOWN mismatch was waived. `unjudged_axes`
    names anything the same missing doc_ref left with no token-side value to compare at
    all, so a waiver and an absence are never read as the same thing.
    """
    logger.info(
        "inbox_context_binding_relaxed %s",
        json.dumps({
            "event": "inbox_context_binding_relaxed",
            "boundary": BOUNDARY_INBOX,
            "action_handler": action_handler,
            "binding_relaxed": True,
            "relaxed_axis": "doc",
            "unjudged_axes": unjudged_axes or [],
            "correlation_id": correlation_id,
            "token_id_hash": token_id_hash(token_id),
        }, ensure_ascii=False),
    )


def _secret() -> bytes:
    return (os.environ.get("SECRET_KEY") or "flowgate-register-binding").encode("utf-8")


def token_id_hash(token_id: Any) -> Optional[str]:
    """Irreversible stand-in for a token id — the raw id never leaves this module."""
    value = _text(token_id)
    if not value:
        return None
    return hmac.new(_secret(), value.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def fingerprint(context: dict) -> str:
    """HMAC of the four axes. Two failures with the same fingerprint pair had the same
    mismatch; neither side's values can be read back out of it."""
    joined = "\x1f".join(str(context.get(axis)) for axis in AXIS_ORDER)
    return hmac.new(_secret(), joined.encode("utf-8"), hashlib.sha256).hexdigest()[:64]


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def failure_record(
    *,
    boundary: str,
    axes: list[str],
    run_context: dict,
    token_context: dict,
    correlation_id: str,
    run_id: Optional[str] = None,
    ai_run_id: Optional[str] = None,
    token_id: Optional[str] = None,
    group_token_db: Optional[str] = None,
    group_token_resolved: Optional[str] = None,
    action_scope_request: Optional[str] = None,
    prev_doc_id_request: Optional[str] = None,
    target_doc_id_request: Optional[str] = None,
    binding_relaxed: bool = False,
    status: int = 403,
    turn: Optional[int] = None,
    notes: Optional[str] = None,
) -> dict:
    """One rejection, in the exact shape the memory diagnostics and the DB row share.

    The eight keys L0010 §2.5 names (``status``/``code``/``reason``/``turn``/``boundary``/
    ``axis``/``axes``/``correlation_id``) sit at the top level so ``register_errors``
    readers see them without unwrapping; ``telemetry`` carries the column payload that only
    :mod:`modules.flow_gate.db.register_context_failures` needs.
    """
    axis = axes[0] if axes else None
    telemetry = {
        "run_id": run_id,
        "correlation_id": correlation_id,
        "boundary": boundary,
        "action_scope_run": _resolved(run_context.get("action")),
        "action_scope_token": _resolved(token_context.get("action")),
        "action_scope_request": action_scope_request,
        "project_run": _resolved(run_context.get("project")),
        "project_token": _resolved(token_context.get("project")),
        "group_run": _resolved(run_context.get("group")),
        "group_token_db": group_token_db,
        "group_token_resolved": group_token_resolved or _resolved(token_context.get("group")),
        "doc_ref_run": _resolved(run_context.get("doc")),
        "doc_ref_token": _resolved(token_context.get("doc")),
        "prev_doc_id_request": prev_doc_id_request,
        "target_doc_id_request": target_doc_id_request,
        "ai_run_id": ai_run_id,
        "axis_first_mismatch": axis,
        "axes_all_mismatches": list(axes),
        "token_id_hash": token_id_hash(token_id),
        "expected_fingerprint": fingerprint(run_context),
        "actual_fingerprint": fingerprint(token_context),
        "binding_relaxed": bool(binding_relaxed),
        "relaxed_axis": "doc" if binding_relaxed else None,
        "status": status,
        "code": CODE_FORBIDDEN,
        "reason": REASON_BINDING,
        "turn": turn,
        "notes": notes,
    }
    return {
        "status": status,
        "code": CODE_FORBIDDEN,
        "reason": REASON_BINDING,
        "turn": turn,
        "boundary": boundary,
        "axis": axis,
        "axes": list(axes),
        "correlation_id": correlation_id,
        "telemetry": telemetry,
    }


def _resolved(value: Any) -> Optional[str]:
    """Unwrap a canonical axis value; an unresolved sentinel reads back as None."""
    if isinstance(value, str) and value.startswith("\x00unresolved:"):
        return None
    return value


def log_failure(record: dict) -> None:
    """Structured log carrying only what L0010 §2.4 allows: never an expected/actual value,
    a raw token, a token id or a filesystem path."""
    telemetry = record.get("telemetry") or {}
    logger.warning(
        "%s %s",
        _LOG_EVENT,
        json.dumps(
            {
                "event": _LOG_EVENT,
                "boundary": record.get("boundary"),
                "axis": record.get("axis"),
                "axes": record.get("axes"),
                "run_id": telemetry.get("run_id"),
                "correlation_id": record.get("correlation_id"),
                "token_id_hash": telemetry.get("token_id_hash"),
                "expected_fingerprint": telemetry.get("expected_fingerprint"),
                "actual_fingerprint": telemetry.get("actual_fingerprint"),
                "binding_relaxed": telemetry.get("binding_relaxed"),
            },
            ensure_ascii=False,
        ),
    )


def forbidden_details(record: dict) -> dict:
    """The 403 ``error`` object. Deliberately omits expected/actual, raw token, token id and
    any absolute path — a correlation id is what ties a report back to the server row."""
    return {
        "code": CODE_FORBIDDEN,
        "message": BINDING_MESSAGE,
        "details": {
            "reason": REASON_BINDING,
            "axis": record.get("axis"),
            "axes": record.get("axes"),
            "correlation_id": record.get("correlation_id"),
        },
    }


def permission_denied_error(message: str) -> dict:
    """A permission 403 shares the top-level code and says so in ``reason`` — it must never
    carry axes, so a reader cannot mistake an RBAC refusal for a binding fault."""
    return {
        "code": CODE_FORBIDDEN,
        "message": message,
        "details": {"reason": REASON_PERMISSION},
    }


def inbox_mismatches(
    *,
    action_handler: str,
    project: Any,
    group: Any,
    doc: Any,
    token_rec: dict,
    token_kind: Optional[str] = None,
    api_run_bound: bool = False,
) -> tuple[list[str], dict, dict, bool, Optional[str], Optional[str]]:
    """L0010 §2.3 — the inbox's own four-axis re-check.

    Returns ``(axes, expected, actual, binding_relaxed, group_token_db, group_token_resolved)``.
    ``group`` is the caller's DB-resolved group for the request, never a string guess.
    """
    expected = canonical_context(action_handler, project, group, doc)
    actual, group_db, group_resolved = token_axes(token_rec)
    axes = compare_axes(expected, actual)

    relaxed = False
    if (
        action_handler == "review"
        and token_kind == TOKEN_KIND_HUMAN_LEGACY
        and not api_run_bound
        and not _text(token_rec.get("doc_ref"))
    ):
        # Only what the EMPTY doc_ref caused is removed. Nothing is written into `actual`,
        # so the audit still records what the token really carried.
        #
        # `group` goes with it when the token has no group column either: on such a token the
        # group could ONLY have come from the doc_ref that is missing, so there is nothing on
        # the token side to compare — leaving it in would make the doc waiver dead letter,
        # since every one of these tokens would then be refused on `group` instead.
        dropped = {"doc"} if group_db else {"doc", "group"}
        relaxed = bool(dropped & set(axes))
        axes = [axis for axis in axes if axis not in dropped]

    return axes, expected, actual, relaxed, group_db, group_resolved


def record_for_run(run_id: Optional[str], record: dict) -> None:
    """Accumulate a failure on the live run — the SSOT while a run is in memory.

    The inbox boundary runs in the same process as the API provider loop but has no run
    object, so it reaches the run through the token's ai_run_id. A run that is not live
    here (an external worker's token, a restarted process) simply gets the structured log.
    """
    log_failure(record)
    if not run_id:
        return
    try:
        from modules.flow_gate.services import ai_invoke_service

        run = ai_invoke_service.get_run_record(run_id)
    except Exception:
        logger.warning("register binding: live run lookup failed", exc_info=True)
        return
    if run is None:
        return
    run.setdefault("register_errors", []).append(record)
