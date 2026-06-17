"""Workflow event logging helper (D017 r1 §8-1, §8-2).

Handles INSERT of events into the workflow_events table.
Uses db.workflow_events.create() rather than direct DB manipulation.
"""
from __future__ import annotations

import json
from typing import Any

from modules.flow_gate.db import workflow_events as db_events


# ── Event type constants (D017 r1 §8-2) ──────────────────────────────────────
EVT_STATE_CHANGED = "state_changed"
EVT_ACTION_TAKEN = "action_taken"
EVT_COMMENT_ADDED = "comment_added"
EVT_PROMPT_COPIED = "prompt_copied"
EVT_GROUP_COMPLETION_CANDIDATE = "group_completion_candidate"
EVT_GROUP_APPROVED = "group_approved"


def log_event(
    *,
    event_type: str,
    project_id: str,
    actor_user_id: str,
    group_id: str | None = None,
    document_id: int | None = None,
    from_state: str | None = None,
    to_state: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict:
    """Record an event in the workflow_events table and return the created row.

    Parameters
    ----------
    event_type:
        Event type (use EVT_* constants).
    project_id:
        ID of the project the event belongs to.
    actor_user_id:
        ID of the user who triggered the event.
    group_id:
        Specify for group-level events. May be None for document-level events.
    document_id:
        documents.id (INTEGER PK). None for group-level events.
    from_state / to_state:
        Previous/next state for state-transition events.
    metadata:
        Additional information dict. Serialized as JSON before storage.
    """
    meta_str = json.dumps(metadata, ensure_ascii=False) if metadata else None
    return db_events.create(
        {
            "event_type": event_type,
            "project_id": project_id,
            "group_id": group_id,
            "document_id": document_id,
            "actor_user_id": actor_user_id,
            "from_state": from_state,
            "to_state": to_state,
            "metadata": meta_str,
        }
    )


def log_state_changed(
    *,
    project_id: str,
    actor_user_id: str,
    from_state: str,
    to_state: str,
    group_id: str | None = None,
    document_id: int | None = None,
    action_code: str | None = None,
) -> dict:
    """Record a state-transition event."""
    meta: dict[str, Any] = {"from": from_state, "to": to_state}
    if action_code:
        meta["action"] = action_code
    return log_event(
        event_type=EVT_STATE_CHANGED,
        project_id=project_id,
        actor_user_id=actor_user_id,
        group_id=group_id,
        document_id=document_id,
        from_state=from_state,
        to_state=to_state,
        metadata=meta,
    )


def log_prompt_copied(
    *,
    project_id: str,
    actor_user_id: str,
    doc_id: str,
    document_id: int,
    group_id: str | None,
    template_type: str,
    action_context: str | None = None,
) -> dict:
    """Record a prompt-copied event (PM decision No.5)."""
    meta: dict[str, Any] = {
        "doc_id": doc_id,
        "template": template_type,
    }
    if action_context:
        meta["context"] = action_context
    return log_event(
        event_type=EVT_PROMPT_COPIED,
        project_id=project_id,
        actor_user_id=actor_user_id,
        group_id=group_id,
        document_id=document_id,
        metadata=meta,
    )


def log_group_approved(
    *,
    project_id: str,
    actor_user_id: str,
    group_id: str,
) -> dict:
    """Record a group-approved event."""
    return log_event(
        event_type=EVT_GROUP_APPROVED,
        project_id=project_id,
        actor_user_id=actor_user_id,
        group_id=group_id,
        metadata={"status": "approved"},
    )


def log_group_completion_candidate(
    *,
    project_id: str,
    actor_user_id: str,
    group_id: str,
    incomplete_count: int,
) -> dict:
    """Record an all-child-documents-complete notification event (D017 r1 §6)."""
    return log_event(
        event_type=EVT_GROUP_COMPLETION_CANDIDATE,
        project_id=project_id,
        actor_user_id=actor_user_id,
        group_id=group_id,
        metadata={"incomplete_count": incomplete_count},
    )
