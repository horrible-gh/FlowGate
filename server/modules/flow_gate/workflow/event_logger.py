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
# R0001 group 0125 / NR0003: present-tense work-STATE signals. Originally recorded for the dashboard
# state board (작업상태 집계) only. NR0003 권고 4: re-introducing per-transition state changes into the
# past-tense feed would undo the 0118 noise reduction ("상태 변화마다 알림 폭증"). The investigation
# found "시작"(workflow start) and "연속작업 종료"(continuous-run end) had no backend signal at all;
# these constants close that gap (NR0003 §발견 3, 권고 1).
#
# EVT_WORK_STARTED — still state-board ONLY; MUST stay out of _NOTIFICATION_EVENT_TYPES (it is a
#   present-tense per-start signal; promoting it revives the 폭증).
# EVT_CONTINUOUS_WORK_ENDED — R0001 group 0135 / N0008: now ALSO on the notification feed. Unlike a
#   per-transition state change it fires exactly ONCE per unmanned run (at the target-reached stop),
#   so promoting just this terminal event gives a distinct "연속작업 완료" alarm without the 폭증.
#   See dashboard_service._NOTIFICATION_EVENT_TYPES.
EVT_WORK_STARTED = "work_started"
EVT_CONTINUOUS_WORK_ENDED = "continuous_work_ended"
# EVT_CONTINUOUS_WORK_FAILED — R0001 group 0154 / NR0004 Gap A: the failure-path counterpart of
#   CONTINUOUS_WORK_ENDED. When an unmanned chain's server-side test_run finishes RED, no TSR is
#   assembled (tsr_doc_id stays null) and the chain stops with NO persistent signal of any kind — only
#   a transient SSE `test_run_finished` broadcast — so the chain went silent and nobody knew until the
#   run record was opened by hand (NR0004 §2.4, the exact "테스트레포트에서 멈춤" R0001 reported). Like
#   ENDED it fires exactly ONCE per unmanned run (at the failed-run stop), so promoting just this
#   terminal event yields a distinct "연속작업 실패" alarm without reviving the 0118 per-step 폭증.
#   See dashboard_service._NOTIFICATION_EVENT_TYPES.
EVT_CONTINUOUS_WORK_FAILED = "continuous_work_failed"
# EVT_TEST_RUN_REPAIR / EVT_TEST_RUN_REPAIR_EXHAUSTED — flowgate.default.0157 (R0001→…→T0006): the
#   test-run auto-recovery loop (engine_recipe_service.handle_run_failure). When an unmanned chain's
#   server-side test_run dies on an ENVIRONMENT failure (setup/tool/PATH — the test never ran), the
#   system re-fires it up to MAX_REPAIR_ATTEMPTS with a fresh repair token instead of stopping. Each
#   attempt records a REPAIR event carrying the repair token + fix mention; hitting the cap records one
#   EXHAUSTED escalation with the attempt history — the single case where the user must step in. Both
#   are on the notification feed (dashboard_service._NOTIFICATION_EVENT_TYPES): bounded to at most
#   MAX_REPAIR_ATTEMPTS repair rows + one exhausted row per doc, so no 0118 per-step 폭증.
EVT_TEST_RUN_REPAIR = "test_run_repair"
EVT_TEST_RUN_REPAIR_EXHAUSTED = "test_run_repair_exhausted"


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


def log_work_started(
    *,
    project_id: str,
    actor_user_id: str,
    document_id: int,
    doc_id: str | None = None,
    group_id: str | None = None,
) -> dict:
    """Record an explicit workflow-"start" signal (R0001 group 0125, NR0003 권고 1).

    Emitted when a requirement's workflow is decided and the document flips to
    doc_review_status='wf_in_progress'. Used only for the state board aggregation
    (get_work_state_summary); deliberately excluded from the notification feed whitelist.
    """
    meta: dict[str, Any] = {"to_state": "wf_in_progress"}
    if doc_id:
        meta["doc_id"] = doc_id
    return log_event(
        event_type=EVT_WORK_STARTED,
        project_id=project_id,
        actor_user_id=actor_user_id,
        group_id=group_id,
        document_id=document_id,
        to_state="wf_in_progress",
        metadata=meta,
    )


def log_continuous_work_ended(
    *,
    project_id: str,
    actor_user_id: str,
    document_id: int,
    doc_id: str | None = None,
    group_id: str | None = None,
    target_seq: int | None = None,
) -> dict:
    """Record an explicit "continuous-work ended" signal (R0001 group 0125, NR0003 권고 1).

    Emitted when the server-driven continuous (unmanned) self-chain reaches its target and stops.
    Before this, the system had no signal at all for "어떤 문서가 연속작업을 종료했는지" (NR0003 §발견 3).
    Used only for the state board aggregation; never added to the notification feed whitelist.
    """
    meta: dict[str, Any] = {}
    if doc_id:
        meta["doc_id"] = doc_id
    if target_seq is not None:
        meta["target_seq"] = target_seq
    return log_event(
        event_type=EVT_CONTINUOUS_WORK_ENDED,
        project_id=project_id,
        actor_user_id=actor_user_id,
        group_id=group_id,
        document_id=document_id,
        metadata=meta or None,
    )


def log_continuous_work_failed(
    *,
    project_id: str,
    actor_user_id: str,
    document_id: int | None,
    doc_id: str | None = None,
    group_id: str | None = None,
    run_id: str | None = None,
    case_passed: int | None = None,
    case_failed: int | None = None,
    error: str | None = None,
    target_seq: int | None = None,
) -> dict:
    """Record an explicit "continuous-work failed/paused" signal (R0001 group 0154, NR0004 Gap A).

    Emitted when a server-driven continuous (unmanned) run's test_run finishes RED: no TSR is assembled
    and the chain has nothing to hand to the next step, so it stops for a human. Before this, that stop
    produced no persistent signal at all — only a transient SSE `test_run_finished` broadcast — so the
    unmanned chain went silent (NR0004 §2.4). Fires once per failed run; promoted to the notification
    feed as the "연속작업 실패" counterpart of continuous_work_ended.
    """
    meta: dict[str, Any] = {}
    if doc_id:
        meta["doc_id"] = doc_id
    if run_id:
        meta["run_id"] = run_id
    if case_passed is not None:
        meta["case_passed"] = case_passed
    if case_failed is not None:
        meta["case_failed"] = case_failed
    if error:
        meta["error"] = error
    if target_seq is not None:
        meta["target_seq"] = target_seq
    return log_event(
        event_type=EVT_CONTINUOUS_WORK_FAILED,
        project_id=project_id,
        actor_user_id=actor_user_id,
        group_id=group_id,
        document_id=document_id,
        metadata=meta or None,
    )


def log_test_run_repair(
    *,
    project_id: str,
    actor_user_id: str,
    document_id: int | None,
    doc_id: str | None = None,
    group_id: str | None = None,
    run_id: str | None = None,
    attempt: int | None = None,
    max_attempts: int | None = None,
    engine: str | None = None,
    error: str | None = None,
    token: str | None = None,
    mention: str | None = None,
) -> dict:
    """Record one test-run auto-recovery repair delivery (flowgate.default.0157, L §2-6).

    Emitted when an unmanned chain's test_run failed for an ENVIRONMENT reason and the loop re-fires it.
    Carries the fresh repair token + fix mention so a worker (or the user) can re-fire from the feed.
    Fires at most MAX_REPAIR_ATTEMPTS times per doc.
    """
    meta: dict[str, Any] = {}
    if doc_id:
        meta["doc_id"] = doc_id
    if run_id:
        meta["run_id"] = run_id
    if attempt is not None:
        meta["attempt"] = attempt
    if max_attempts is not None:
        meta["max_attempts"] = max_attempts
    if engine:
        meta["engine"] = engine
    if error:
        meta["error"] = error
    if token:
        meta["token"] = token
    if mention:
        meta["mention"] = mention
    return log_event(
        event_type=EVT_TEST_RUN_REPAIR,
        project_id=project_id,
        actor_user_id=actor_user_id,
        group_id=group_id,
        document_id=document_id,
        metadata=meta or None,
    )


def log_test_run_repair_exhausted(
    *,
    project_id: str,
    actor_user_id: str,
    document_id: int | None,
    doc_id: str | None = None,
    group_id: str | None = None,
    attempts: list[dict] | None = None,
) -> dict:
    """Record the test-run repair-exhausted escalation (flowgate.default.0157, L §2-6 / P §상한 도달).

    Emitted once when a doc hits MAX_REPAIR_ATTEMPTS consecutive environment failures — the auto-recovery
    loop stops re-firing and hands the attempt history to the user. The one case a human must intervene.
    """
    meta: dict[str, Any] = {"attempts": attempts or []}
    if doc_id:
        meta["doc_id"] = doc_id
    return log_event(
        event_type=EVT_TEST_RUN_REPAIR_EXHAUSTED,
        project_id=project_id,
        actor_user_id=actor_user_id,
        group_id=group_id,
        document_id=document_id,
        metadata=meta,
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
