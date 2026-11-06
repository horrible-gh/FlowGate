"""Display-only Flow archive/restore (v02test.0002 T-c). Never operates on Git or documents."""
from __future__ import annotations

from flowgate.gate_decisions import cleanup, repository as decisions
from modules.flow_gate.db import documents, groups
from modules.flow_gate.documents.live_root import is_live_root
from modules.flow_gate.graph_workflow.store import run_store
from . import canvas_events, canvas_service
from .errors import GraphApiError
from .flow_guard import with_flow_guard, with_read_snapshot


def archive_eligibility(flow_id: str) -> dict:
    run = run_store.current_run(flow_id)
    if run is None:
        # current_run may be None even though historical runs exist. Such a Flow is not legacy.
        if run_store.list_runs(flow_id, 1, 0)[1]:
            return {"eligible": False, "reason": "run_not_completed"}
        roots = documents.get_documents_by_group_id(flow_id)
        if not any(is_live_root(doc) and doc.get("doc_review_status") == "wf_done" for doc in roots):
            return {"eligible": False, "reason": "run_not_completed"}
        reason = cleanup.activity_blocker(flow_id, None)
    else:
        if not run_store.completed(run):
            return {"eligible": False, "reason": "run_not_completed"}
        rows = decisions.decisions_of_run(flow_id, run["run_id"])
        if any(row["status"] in decisions.RESERVING for row in rows):
            return {"eligible": False, "reason": "gate_job_unfinished"}
        if not any(row["status"] == decisions.SUCCEEDED for row in rows):
            return {"eligible": False, "reason": "no_gate_decision"}
        reason = cleanup.activity_blocker(flow_id, run["run_id"])
    return {"eligible": reason is None, "reason": reason}


def _view(group: dict) -> dict:
    archived = group.get("archived_at") is not None
    eligibility = ({"eligible": False, "reason": "already_archived"} if archived
                   else archive_eligibility(group["group_id"]))
    return {"archived": archived, "archived_at": group.get("archived_at"),
            "archived_by": group.get("archived_by"), **eligibility}


def read(flow_id: str, actor: str, authorize) -> dict:
    def body():
        group = canvas_service.load_flow(flow_id)
        authorize(actor, group, "read")
        return _view(group)
    return with_read_snapshot(body)


def change(flow_id: str, actor: str, archived: bool, authorize) -> dict:
    # Check access before taking a guard, then again against the row read under it.
    authorize(actor, canvas_service.load_flow(flow_id), "write")

    def body():
        group = canvas_service.load_flow(flow_id)
        authorize(actor, group, "write")
        if (group.get("archived_at") is not None) == archived:
            return _view(group)
        if archived:
            eligibility = archive_eligibility(flow_id)
            if not eligibility["eligible"]:
                raise GraphApiError(409, eligibility["reason"], {"flow_id": flow_id})
        if groups.set_archive(flow_id, archived, actor):
            canvas_events.publish_archive_changed(project_id=group["project_id"], flow_id=flow_id,
                                                  archived=archived, actor_user_id=actor)
        return _view(canvas_service.load_flow(flow_id))

    return with_flow_guard(flow_id, body)
