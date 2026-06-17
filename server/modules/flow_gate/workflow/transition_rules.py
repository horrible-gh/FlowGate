"""Transition rules — state-machine allowed transitions, permissions, and precondition mapping (D017 r1 §4, §9-1).

Each transition is defined as (current_state, action) → (next_state, required_permission_list, validation_hint).
"""
from __future__ import annotations

from typing import NamedTuple


class TransitionRule(NamedTuple):
    next_state: str
    required_permissions: tuple[str, ...]  # ANY match is sufficient (OR condition)
    notes: str = ""


# ── Document state transition matrix ─────────────────────────────────────────
# Key: (current_state, action_code)
# D017 r1 §4-2, §7-3, §9-1

DOC_TRANSITIONS: dict[tuple[str, str], TransitionRule] = {
    # draft → submit → open
    ("draft", "submit"): TransitionRule(
        next_state="open",
        required_permissions=("document.update", "own.draft"),
        notes="Own document or document.update permission",
    ),
    # draft → workflow_decide → open (automatic transition when workflow is decided)
    ("draft", "workflow_decide"): TransitionRule(
        next_state="open",
        required_permissions=("document.update", "own.draft"),
    ),
    # open → approve → approved (direct approval, MVP: no request_review)
    ("open", "approve"): TransitionRule(
        next_state="approved",
        required_permissions=("document.approve",),
    ),
    # open → reject → rejected
    ("open", "reject"): TransitionRule(
        next_state="rejected",
        required_permissions=("document.reject",),
        notes="Rejection reason required",
    ),
    # open → cancel → cancelled
    ("open", "cancel"): TransitionRule(
        next_state="cancelled",
        required_permissions=("document.delete", "document.delete.own.draft"),
    ),
    # draft → cancel → cancelled
    ("draft", "cancel"): TransitionRule(
        next_state="cancelled",
        required_permissions=("document.delete", "document.delete.own.draft"),
    ),
    # rejected → resubmit → open
    ("rejected", "resubmit"): TransitionRule(
        next_state="open",
        required_permissions=("document.update",),
    ),
    # rejected → redraft → draft
    ("rejected", "redraft"): TransitionRule(
        next_state="draft",
        required_permissions=("document.update",),
    ),
    # approved → close → closed
    ("approved", "close"): TransitionRule(
        next_state="closed",
        required_permissions=("document.approve",),
    ),
    # in_review → approve → approved
    ("in_review", "approve"): TransitionRule(
        next_state="approved",
        required_permissions=("document.approve",),
    ),
    # in_review → reject → rejected
    ("in_review", "reject"): TransitionRule(
        next_state="rejected",
        required_permissions=("document.reject",),
        notes="Rejection reason required",
    ),
    # open → child_created → closed (automatic transition when a child document is created, T528)
    ("open", "child_created"): TransitionRule(
        next_state="closed",
        required_permissions=("document.update", "document.approve"),
        notes="Automatic transition when a child document is created",
    ),
}

# ── Group state transition matrix ────────────────────────────────────────────
# D017 r1 §2-2, §4-1, §9-1

GROUP_TRANSITIONS: dict[tuple[str, str], TransitionRule] = {
    # draft → automatic transition on workflow-root submit
    ("draft", "start"): TransitionRule(
        next_state="in_progress",
        required_permissions=("document.create", "document.update"),
        notes="Automatically invoked on R/B workflow-root submit",
    ),
    # in_progress → automatic transition on Q document creation
    ("in_progress", "clarify"): TransitionRule(
        next_state="clarifying",
        required_permissions=("document.create",),
        notes="Automatically invoked on Q document creation",
    ),
    # clarifying → return when A registered + Q closed
    ("clarifying", "resume"): TransitionRule(
        next_state="in_progress",
        required_permissions=("document.create",),
        notes="Automatically invoked when Q is resolved",
    ),
    # in_progress → manager group AC
    ("in_progress", "approve"): TransitionRule(
        next_state="approved",
        required_permissions=("document.approve",),
    ),
    # clarifying → manager group AC (warning only for unresolved Qs)
    ("clarifying", "approve"): TransitionRule(
        next_state="approved",
        required_permissions=("document.approve",),
        notes="Includes a warning if unresolved Qs exist",
    ),
    # approved → admin close
    ("approved", "close"): TransitionRule(
        next_state="closed",
        required_permissions=("document.approve",),
        notes="Requires explicit admin action",
    ),
}

# ── Automatic Q state management (D017 r1 §8-5) ──────────────────────────────
# On A registration → Q.status = 'answered'
# On user Q AC → Q.status = 'closed'
# On user Q RJ → Q.status = 'open' (unchanged)

Q_AUTO_TRANSITIONS = {
    "a_created": "answered",   # Linked Q → answered when A (response) is registered
    "q_approve": "closed",     # Q → closed on AC
    "q_reject": "open",        # Q → open (unchanged) on RJ
}


# ── Document review (doc_review_status) transition matrix ────────────────────
# M026 §8-1 6 transition rules (T427)
# Key: (current doc_review_status, action) → next doc_review_status

DOC_REVIEW_TRANSITIONS: dict[tuple[str, str], str] = {
    ('pending_review', 'approve'):      'approved',
    ('pending_review', 'reject'):       'rejected',
    ('revised',        'approve'):      'approved',
    ('revised',        'reject'):       'rejected',
    ('rejected',       'mark_revised'): 'pending_review',
    ('approved',       'mark_revised'): 'pending_review',  # regression after approval → re-review
    # submit: automatic transition when a worker document is registered (M026 §8-1, DB004 §6.1)
    ('',               'submit'):       'pending_review',  # initial registration
    ('approved',       'submit'):       'pending_review',  # re-registration after approval
    ('rejected',       'submit'):       'revised',         # re-registration after rejection
}


def get_doc_rule(current_status: str, action: str) -> TransitionRule | None:
    """Look up the document transition rule. Returns None if not found."""
    return DOC_TRANSITIONS.get((current_status, action))


def get_doc_review_rule(current_review_status: str, action: str) -> str | None:
    """Look up the review-state transition result. Returns None if not found."""
    return DOC_REVIEW_TRANSITIONS.get((current_review_status, action))


def get_group_rule(current_status: str, action: str) -> TransitionRule | None:
    """Look up the group transition rule. Returns None if not found."""
    return GROUP_TRANSITIONS.get((current_status, action))


def check_permission(user_permissions: set[str], required: tuple[str, ...]) -> bool:
    """Return True if user_permissions contains at least one of required (OR condition)."""
    return bool(user_permissions & set(required))
