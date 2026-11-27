"""Append-only approval continuity for the F81 TS/TSR edit matrix."""
import json
import uuid
from modules.flow_gate.db.connection import get_store, now_iso
from . import decision_repository as repo
from flowgate.card_settings.report_baseline import fingerprint


def capture(doc):
    if doc.get("type_code") not in {"TS", "TSR"} or doc.get("doc_review_status") != "approved" or not repo.available():
        return []
    rows = get_store()._fetch_all("SELECT * FROM graph_document_decisions WHERE doc_id = ? AND action = 'approve'", [doc["doc_id"]])
    mark = fingerprint(doc)
    return [r for r in rows if repo.latest(r["group_id"], r["node_key"], r["slot"])["decision_id"] == r["decision_id"]
            and json.loads(r["document_mark_json"]) == mark]


def preserve(rows, doc):
    if doc.get("doc_review_status") != "approved":
        return
    for row in rows:
        version = repo.version(row["group_id"], row["node_key"]) + 1
        request_id = "gdr_" + uuid.uuid4().hex
        key = "f81-carry:" + uuid.uuid4().hex
        now = now_iso()
        repo.insert_request({"request_id": request_id, "group_id": row["group_id"], "node_key": row["node_key"],
            "request_key": key, "request_hash": key, "decision_version": version, "actor_id": row["actor_id"],
            "context_json": json.dumps({"preserved_from": row["decision_id"]}), "result_json": "{}", "created_at": now})
        repo.append({**row, "decision_id": "gdd_" + uuid.uuid4().hex, "request_id": request_id,
            "revision_no": doc["revision_no"], "document_mark_json": json.dumps(fingerprint(doc)),
            "decision_version": version, "request_key": key, "created_at": now,
            "cause": "approval_preserved", "reason": "f81_edit_matrix"})
