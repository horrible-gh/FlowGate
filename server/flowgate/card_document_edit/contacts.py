"""Only the designated transport executes a one-shot committed notification plan."""
import logging
from modules.flow_gate.db.connection import in_transaction
from modules.flow_gate.graph_workflow.services.flow_guard import held_guard
from modules.flow_gate.graph_workflow.services import canvas_events
from modules.flow_gate.graph_workflow.api import v01_contact
from modules.flow_gate.api.v1.events.publisher import broadcast_event_nowait, FlowEvent
from modules.flow_gate.api.v1.events.event_types import EventType

_log = logging.getLogger(__name__)

# Contacts run after commit and only send refresh signals, so the save response must not wait
# for the SSE main loop (up to 2s in the shared publisher.broadcast_event_threadsafe). The module
# attribute keeps its name because tests and callers patch contacts.broadcast_event_threadsafe.
broadcast_event_threadsafe = broadcast_event_nowait


def _best_effort(name, call):
    try:
        call()
    except Exception:
        _log.warning("card_document_edit notification failed: %s", name, exc_info=True)


def run(plan):
    assert not in_transaction() and held_guard() is None, "contacts require commit and guard release"
    assert plan.card_slot is not None, "notification scope must be resolved"
    if plan.consumed:
        _log.error("card_document_edit contact plan already consumed")
        return False
    plan.consumed = True
    doc = plan.doc
    _best_effort("document_explorer_refresh", lambda: broadcast_event_threadsafe(FlowEvent(
        event_type=EventType.DOCUMENT_EXPLORER_REFRESH,
        payload={"operation": "updated", "doc_id": doc["doc_id"], "type": doc["type_code"],
                 "title": doc.get("title"), "status": doc.get("status"), "revision_no": doc["revision_no"]},
        project=plan.project_id, group_id=plan.flow_id, doc_id=doc["doc_id"], audience="*")))
    if plan.card_slot and not plan.canvas_already_queued:
        _best_effort("graph_canvas_changed", lambda: canvas_events.publish_canvas_changed(
            project_id=plan.project_id, flow_id=plan.flow_id, reason="documents",
            actor_user_id=plan.actor_user_id, origin_client_id=None))
    if plan.card_slot or plan.legacy_non_card:
        _best_effort("after_inbox_edited", lambda: v01_contact.after_inbox_edited(
            plan.flow_id, doc["doc_id"], doc["revision_no"], plan.token_id))
    for task in plan.type_tasks:
        _best_effort("type_task", task)
    return True


def run_replay(plan):
    return run(plan)


def work_plan_refresh(doc, actor_user_id):
    broadcast_event_threadsafe(FlowEvent(event_type=EventType.GROUP_VIEW_REFRESH,
        payload={"group_id": doc.get("group_id"), "reason": "document_updated"},
        project=doc["project_id"], group_id=doc.get("group_id"), doc_id=doc["doc_id"], audience=actor_user_id))
