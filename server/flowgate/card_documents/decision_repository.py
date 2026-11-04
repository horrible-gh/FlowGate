"""0126 T#1: append-only human decisions and durable request receipts (migration 171).

A card version covers both slots. All writes require the caller's Flow guard. A badge is
never human approval evidence: consumers must use ``projection`` / ``valid_approval``.
"""
from __future__ import annotations

import json
from modules.flow_gate.db.connection import get_store
from flowgate.card_settings.report_baseline import fingerprint

REQUESTS = "graph_document_decision_requests"
DECISIONS = "graph_document_decisions"


def available():
    return get_store().table_exists(REQUESTS) and get_store().table_exists(DECISIONS)


def version(flow_id, node_key):
    if not available():
        return 0
    row = get_store()._fetch_one(
        f"SELECT MAX(decision_version) AS v FROM {REQUESTS} WHERE group_id = ? AND node_key = ?",
        [flow_id, node_key])
    return int(row["v"] or 0)


def request(flow_id, node_key, key):
    return get_store()._fetch_one(
        f"SELECT * FROM {REQUESTS} WHERE group_id = ? AND node_key = ? AND request_key = ?",
        [flow_id, node_key, key])


def insert_request(row):
    _insert(REQUESTS, row)


def finish_request(request_id, result):
    get_store()._execute(f"UPDATE {REQUESTS} SET result_json = ? WHERE request_id = ?",
                         [json.dumps(result, ensure_ascii=False), request_id])


def append(row):
    _insert(DECISIONS, row)


def _insert(table, row):
    columns = list(row)
    get_store()._execute(f"INSERT INTO {table} (" + ", ".join(columns) + ") VALUES (" +
                         ", ".join("?" for _ in columns) + ")", [row[c] for c in columns])


def latest(flow_id, node_key, slot):
    if not available():
        return None
    return get_store()._fetch_one(
        f"SELECT * FROM {DECISIONS} WHERE group_id = ? AND node_key = ? AND slot = ? "
        "ORDER BY decision_version DESC LIMIT 1", [flow_id, node_key, slot])


def projection(flow_id, node_key, slot, doc, mark=None):
    """No write on read. An edited/replaced body cannot inherit a human decision.

    body_event_id also distinguishes an in-place save with identical bytes. Failed body reads
    cannot validate an approval. Older decisions stay in the ledger for history.
    """
    if not doc:
        return {"revision_no": None, "human_decision": None}
    row = latest(flow_id, node_key, slot)
    result = {"revision_no": int(doc.get("revision_no") or 0), "human_decision": None}
    if row is None or row["doc_id"] != doc["doc_id"]:
        return result
    mark = fingerprint(doc) if mark is None else mark
    valid = bool(mark.get("body_sha256")) and json.loads(row["document_mark_json"]) == mark
    # A subsequent system decision is not attributed to this human.
    valid = valid and doc.get("doc_review_status") == row["after_state"]
    result["human_decision"] = {k: row[k] for k in (
        "decision_id", "request_id", "action", "actor_kind", "actor_id", "created_at",
        "decision_version", "cause", "reason", "cascade_from")}
    result["human_decision"]["valid"] = valid
    if not valid and doc.get("doc_review_status") == row["after_state"]:
        result["doc_review_status"] = "pending_review"
    return result


def valid_approval(flow_id, node_key, slot, doc):
    """T#2 entry: revision/body-bound evidence, never a fabricated AI verdict/completion."""
    decision = projection(flow_id, node_key, slot, doc).get("human_decision")
    return decision if decision and decision["valid"] and decision["action"] == "approve" else None
