"""Wire names, status/error codes and limits of the Conversation contract (D0006 §3.1, §3.5, §5).

Everything a client or a model can observe is named here once: command states,
decisions, cancel/failure reason codes, HTTP error codes and the time / result-length
limits. Behaviour lives in the other modules.
"""
from __future__ import annotations

from typing import Optional

from flowgate.conversation import legacy_adapter

# ── command request states (D0006 §3.1) ──────────────────────────────────────
PENDING_APPROVAL = "pending_approval"
APPROVED = "approved"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
TIMED_OUT = "timed_out"
CANCELLED = "cancelled"
REJECTED = "rejected"

STATUSES = (PENDING_APPROVAL, APPROVED, RUNNING, SUCCEEDED, FAILED, TIMED_OUT, CANCELLED, REJECTED)
TERMINAL_STATUSES = (SUCCEEDED, FAILED, TIMED_OUT, CANCELLED, REJECTED)
OPEN_STATUSES = (PENDING_APPROVAL, APPROVED, RUNNING)

# ── decisions (D0006 §3.2) ───────────────────────────────────────────────────
DECISION_APPROVE = "approve"
DECISION_REJECT = "reject"
DECISION_CANCEL = "cancel"
DECISIONS = (DECISION_APPROVE, DECISION_REJECT, DECISION_CANCEL)

# Who decided: the user's standing policy or the user on the card.
DECIDED_BY_POLICY = "policy"
DECIDED_BY_USER = "user"

# ── user command policy (stored with the chat settings) ──────────────────────
POLICY_ALWAYS_APPROVE = "always_approve"
POLICY_USER_APPROVAL = "user_approval"
POLICY_REJECT = "reject"
COMMAND_POLICIES = (POLICY_ALWAYS_APPROVE, POLICY_USER_APPROVAL, POLICY_REJECT)

# ── cancelled / failed reason codes (D0006 §5.3) ─────────────────────────────
REASON_CANCELLED_BY_USER = "chat_command_cancelled_by_user"
REASON_APPROVAL_WAIT_EXPIRED = "chat_command_approval_wait_expired"
REASON_RUN_BUDGET_EXHAUSTED = "chat_command_run_budget_exhausted"
REASON_RUN_FINISHED = "chat_command_run_finished"
REASON_RUN_CANCELLED = "chat_command_run_cancelled"
REASON_SERVER_RESTARTED = "chat_command_server_restarted"
REASON_PROCESS_OWNER_UNAVAILABLE = "chat_command_process_owner_unavailable"
REASON_INTERNAL_ERROR = "chat_command_internal_error"

CANCEL_REASONS = (
    REASON_CANCELLED_BY_USER, REASON_APPROVAL_WAIT_EXPIRED, REASON_RUN_BUDGET_EXHAUSTED,
    REASON_RUN_FINISHED, REASON_RUN_CANCELLED, REASON_SERVER_RESTARTED,
)
FAILURE_REASONS = (REASON_PROCESS_OWNER_UNAVAILABLE, REASON_INTERNAL_ERROR)

# ── HTTP error codes (D0006 §5.3, §5.4) ──────────────────────────────────────
ERR_FORBIDDEN = "chat_command_forbidden"
ERR_CAPABILITY_REQUIRED = "chat_command_capability_required"
ERR_RUN_FINISHED = REASON_RUN_FINISHED
ERR_RUN_CANCELLED = REASON_RUN_CANCELLED
ERR_WORKTREE_UNAVAILABLE = "chat_command_worktree_unavailable"
ERR_WORKTREE_MISMATCH = "chat_command_worktree_mismatch"
ERR_NOT_FOUND = "chat_command_not_found"
ERR_INVALID_DECISION = "chat_command_invalid_decision"
ERR_DECISION_FORBIDDEN = "chat_command_decision_forbidden"
ERR_ALREADY_DECIDED = "chat_command_already_decided"
ERR_ALREADY_FINISHED = "chat_command_already_finished"
ERR_INVALID_REQUEST = "chat_command_invalid_request"

ERR_CHAT_DOCUMENT_NOT_FOUND = "chat_document_not_found"
ERR_ACTIVITY_FORBIDDEN = "forbidden"
ERR_RUN_CHANGE_NOT_FOUND = "chat_run_change_not_found"
ERR_RUN_CHANGE_PATH_NOT_FOUND = "chat_run_change_path_not_found"

ERR_CONVERSATION_INVALID_REQUEST = "conversation_invalid_request"
ERR_CONVERSATION_PROJECT_NOT_FOUND = "conversation_project_not_found"
ERR_CONVERSATION_GROUP_NOT_FOUND = "conversation_group_not_found"
ERR_CONVERSATION_GROUP_DISPOSED = "conversation_group_disposed"
ERR_CONVERSATION_NUMBER_RESERVATION_FAILED = "conversation_number_reservation_failed"
# flowgate.v02.0047: the one AI title of a new chat (auto_title.py).
ERR_CONVERSATION_TITLE_CHANGED = "conversation_title_changed"
ERR_CONVERSATION_AUTO_TITLE_NOT_OWED = "conversation_auto_title_not_owed"
ERR_CONVERSATION_AUTO_TITLE_NO_MESSAGE = "conversation_auto_title_no_message"
ERR_CONVERSATION_AUTO_TITLE_UNAVAILABLE = "conversation_auto_title_unavailable"
ERR_CONVERSATION_AUTO_TITLE_FAILED = "conversation_auto_title_failed"

# ── time and result-length limits (D0006 §3.5) ───────────────────────────────
APPROVAL_WAIT_MAX_SEC = 900
RUN_SAFETY_SEC = 5
CLI_WAIT_DEFAULT_SEC = 30
CLI_WAIT_MAX_SEC = 120
# Not a copy: the API worker's own tool-result cap, read from the runtime.
AI_RESULT_MAX_CHARS = legacy_adapter.API_TOOL_RESULT_MAX_CHARS
RESULT_STREAMS_TOTAL_CHARS = 12000
RESULT_META_FIELD_CHARS = 1000
# A running command may outlive its own timeout by this much before the API worker
# stops waiting for it (D0006 §3.5.2 rule 4).
RUNNING_GRACE_SEC = 15
# Upper bound of one wait step; progress is noted at least this often (D0006 §3.6).
WAIT_POLL_SEC = 1.0

# ── activity anchor (D0006 §3.9.1) ───────────────────────────────────────────
ANCHOR_BEFORE = "before"
ANCHOR_AFTER = "after"
ANCHOR_POSITIONS = (ANCHOR_BEFORE, ANCHOR_AFTER)
ANCHOR_REPLY = "reply"
ANCHOR_RUN_START = "run_start"
ANCHOR_AMBIGUOUS = "ambiguous"
ANCHOR_UNRESOLVED = "unresolved"
ANCHOR_STATES = (ANCHOR_REPLY, ANCHOR_RUN_START, ANCHOR_AMBIGUOUS, ANCHOR_UNRESOLVED)

# ── run change capture (D0006 §3.8.1) ────────────────────────────────────────
CAPTURE_READY = "ready"
CAPTURE_UNAVAILABLE = "unavailable"

# ── SSE invalidation events (D0006 §3.9.2; not authoritative payloads) ───────
EVENT_COMMAND_UPDATED = "chat_command_updated"
EVENT_RUN_CHANGES = "chat_run_changes"

CHAT_TYPE_CODE = "CH"


class ConversationError(Exception):
    """One contract error: HTTP status + stable code + human message."""

    def __init__(self, status: int, code: str, message: Optional[str] = None):
        self.status = int(status)
        self.code = code
        self.message = message or code
        super().__init__(self.message)


def error_body(code: str, message: Optional[str] = None) -> dict:
    """The error envelope every Conversation route answers with (D0006 §5.1)."""
    return {"ok": False, "error": {"code": code, "message": message or code}}
