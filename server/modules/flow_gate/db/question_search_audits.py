"""Durable precheck records, including reuse and rejected registration attempts."""
import json
from .connection import get_store, now_iso, after_rollback


def get(precheck_id):
    row = get_store()._fetch_one(get_store()._sql("question_search_audits.get"), [precheck_id])
    return json.loads(row["result_json"]) if row else None


def save(result, doc_id, project_id, provenance, fingerprint, query, reason):
    store = get_store()
    args = [result["precheck_id"], doc_id, project_id, provenance.get("ai_run_id"),
            provenance.get("invocation_id"), fingerprint, query, result["algorithm_version"],
            result["status"], result.get("found_count"), len(result["candidates"]),
            json.dumps([r["answer_id"] for r in result["candidates"]]),
            (result.get("reused_answer") or {}).get("answer_id"), reason,
            json.dumps(result.get("added_item_ids", [])), json.dumps(result, ensure_ascii=False), now_iso()]
    if get(result["precheck_id"]) is not None:
        store._execute(store._sql("question_search_audits.update"),
                       [result["status"], json.dumps(result.get("added_item_ids", [])), json.dumps(result, ensure_ascii=False), result["precheck_id"]])
    else:
        store._execute(store._sql("question_search_audits.insert"), args)


def preserve_after_rollback(result, *args):
    # Execute only after the request's transaction has released its connection/locks.
    # Registration ids do not survive rollback; record the search evidence as rolled back.
    snapshot = dict(result)
    snapshot["added_item_ids"] = []
    if snapshot["status"] == "registered":
        snapshot["status"] = "registration_rolled_back"
    def restore():
        if get(snapshot["precheck_id"]) is None:
            save(snapshot, *args)
    after_rollback(restore)


def for_doc(doc_id):
    rows = get_store()._fetch_all(get_store()._sql("question_search_audits.by_doc"), [doc_id])
    return [json.loads(r["result_json"]) for r in rows]


def summary(record):
    if not record:
        return {"searched": None, "status": "no_record", "label": "기록 없음"}
    return {k: record.get(k) for k in ("precheck_id", "searched", "status", "found_count", "returned_count", "reused_answer", "reuse_rejection_reason", "algorithm_version", "decision_reason")}
