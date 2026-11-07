"""0145 T#1: immutable result-slot coordinates, captured before a worker can submit.

The ledger uses existing workflow events (no schema or historical data rewrite).
Legacy new tokens are never reinterpreted as edits: a mapped result writer must
be reissued when its card already has a result document.
"""
import json

from modules.flow_gate.db import documents, tokens, workflow_events
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.store import run_store
from flowgate.card_documents import repository, lines, slots
from flowgate.card_document_edit.errors import GraphApiError

EVENT = "graph_result_slot_bound"


def refused(reason):
    return GraphApiError(409, "result_slot_binding_conflict", {"reason": reason})


def snapshot(flow_id, run_id, node):
    """Called under the dispatch Flow guard; absence of legacy lines keeps legacy creation."""
    if not any(s.slot == slots.REPORT for s in slots.card_slots(node.get("node_type"), node.get("doc_type"))):
        return None
    if not repository.available():
        raise refused("card_documents_unavailable")
    rows = repository.rows(flow_id, [node["node_key"]])
    if not rows:
        return None  # Pre-card-document definitions have no preallocated slot.
    candidates = [r for r in slots.lines_of(node.get("node_type"), node.get("doc_type"), rows)
                  if r["slot"] == slots.REPORT]
    if len(candidates) != 1:
        raise refused("result_slot_missing")
    row = candidates[0]
    doc = documents.get_by_id(row["doc_id"])
    run = run_store.run_by_id(run_id)
    attempt = run_store.active_attempt_id(run_id, node["node_key"])
    if (not run or run["group_id"] != flow_id or not attempt or not doc
            or doc.get("group_id") != flow_id or doc.get("type_code") != row["doc_type"]
            or doc.get("superseded_by") or doc.get("status") in {"cancelled", "archived"}):
        raise refused("result_slot_unavailable")
    return {"flow_id": flow_id, "run_id": run_id, "node_key": node["node_key"],
            "slot": slots.REPORT, "doc_id": doc["doc_id"], "revision_no": int(doc.get("revision_no") or 0),
            "attempt_id": attempt}


def apply_job(job):
    if job.get("stage") != "result":
        return job
    binding = snapshot(job["flow_id"], job["run_id"], job["node"])
    if binding:
        refs = [job.get("doc_ref"), *(job.get("ref_doc_ids") or [])]
        job.update(action_scope="edit", doc_ref=binding["doc_id"], target_doc_id=binding["doc_id"],
                   result_doc_type=documents.get_by_id(binding["doc_id"])["type_code"],
                   result_slot_binding=binding,
                   ref_doc_ids=list(dict.fromkeys(d for d in refs if d and d != binding["doc_id"])))
        job["result_first_write"] = not lines.has_body(documents.get_by_id(binding["doc_id"]))
    return job


def check_job(job):
    binding = job.get("result_slot_binding")
    if binding and snapshot(job["flow_id"], job["run_id"], job["node"]) != binding:
        raise refused("dispatch_binding_changed")


def mapped(token_id):
    return get_store()._fetch_one(
        "SELECT run_id, node_key, stage, attempt_id FROM graph_run_token_map WHERE token_id = ?", [token_id])


def capture_mapped(token_id, run_id, node_key, stage):
    """Inside map_token's transaction, including edit/rework tokens from existing issuers."""
    token = tokens.get_by_id(token_id)
    if not token or token.get("action_scope") != "edit" or stage == "instruction":
        return
    rows = [r for r in repository.rows(token["group_id"], [node_key])
            if r["slot"] == slots.REPORT and r["doc_id"] == token.get("doc_ref")] if repository.available() else []
    if not rows:
        return
    doc = documents.get_by_id(token["doc_ref"])
    mapping = mapped(token_id)
    run = run_store.run_by_id(run_id)
    if (len(rows) != 1 or not doc or not mapping or not mapping.get("attempt_id") or not run
            or run["group_id"] != token["group_id"] or doc.get("group_id") != token["group_id"]
            or doc.get("project_id") != token["project"] or doc.get("superseded_by")):
        raise refused("mapping_inconsistent")
    binding = {"flow_id": token["group_id"], "run_id": run_id, "node_key": node_key, "slot": slots.REPORT,
               "doc_id": doc["doc_id"], "revision_no": int(doc.get("revision_no") or 0),
               "attempt_id": mapping["attempt_id"]}
    workflow_events.create({"event_type": EVENT, "project_id": token["project"], "group_id": token["group_id"],
        "document_id": doc["id"], "actor_user_id": token["issued_to"],
        "metadata": json.dumps({"token_id": token_id, "binding": binding})})


def issued_binding(token):
    # Python JSON parsing is shared by SQLite/PostgreSQL/MariaDB event readers.
    found = []
    for row in get_store()._fetch_all(
            "SELECT metadata FROM workflow_events WHERE event_type = ? AND group_id = ?", [EVENT, token.get("group_id")]):
        value = json.loads(row["metadata"])
        if value.get("token_id") == token["token_id"]:
            found.append(value["binding"])
    if len(found) > 1:
        raise refused("binding_ambiguous")
    return found[0] if found else None


def validate(token, expected_revision):
    binding = issued_binding(token)
    if not binding:
        return  # Existing non-result / pre-feature edit tokens retain the 0127 contract.
    mapping = mapped(token["token_id"])
    if (token.get("group_id") != binding["flow_id"] or token.get("doc_ref") != binding["doc_id"]
            or not mapping or any(mapping.get(k) != binding[k] for k in ("run_id", "node_key", "attempt_id"))
            or run_store.active_attempt_id(binding["run_id"], binding["node_key"]) != binding["attempt_id"]):
        raise refused("attempt_binding_changed")
    rows = repository.rows(binding["flow_id"], [binding["node_key"]])
    current = [r for r in rows if r["slot"] == binding["slot"]]
    doc = documents.get_by_id(binding["doc_id"])
    if len(current) != 1 or current[0]["doc_id"] != binding["doc_id"] or not doc or doc.get("superseded_by"):
        raise refused("slot_document_changed")
    if expected_revision != binding["revision_no"]:
        raise refused("issued_revision_changed")
    # Current revision CAS and idempotent replay are judged by the common revise service.


def reject_legacy_new(token):
    """No allocation/consumption and no implicit action=new -> edit translation."""
    if not run_store.available():
        return
    mapping = mapped(token["token_id"])
    if not mapping or mapping.get("stage") == "instruction":
        return
    node = next((n for n in run_store.node_runs(mapping["run_id"]) if n["node_key"] == mapping["node_key"]), {})
    if mapping.get("stage") is None and node.get("stage") == "instruction":
        return
    if not repository.available():
        raise refused("card_documents_unavailable")
    if any(r["slot"] == slots.REPORT and r.get("doc_id") for r in repository.rows(token["group_id"], [mapping["node_key"]])):
        raise GraphApiError(409, "result_slot_token_upgrade_required", {"reason": "reissue_bound_edit_token"})
