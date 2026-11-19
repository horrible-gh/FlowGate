"""Conservative identity gate, separate from broad OR candidate retrieval."""
import hashlib
import json
import re
from fastapi import HTTPException
from modules.flow_gate.db import question_search_audits as audits
from modules.flow_gate.services import question_search as search


def identity_tokens(value):
    # Only a small closed list of grammatical particles; retain dates, error codes,
    # permissions, negations and target names in the identity itself.
    words = re.findall(r"[\w%!]+", search.normalized(value))
    return sorted(re.sub(r"(?<=[가-힣])(?:에서|으로|에게|은|는|을|를|이|가|의|에)$", "", w) if len(w) > 2 else w for w in words)


def identical(doc_id, title, body, options, row):
    # Cross-document decisions may conflict with the current R: show them as evidence,
    # but do not automatically apply a different document's authority.
    if row["doc_id"] != doc_id or row.get("body_truncated"):
        return False
    left = identity_tokens((title or "") + " " + body)
    right = identity_tokens((row.get("title") or "") + " " + row.get("body", ""))
    source = (title or "") + " " + body
    candidate = (row.get("title") or "") + " " + row.get("body", "")
    # Bag-of-words cannot bind a permission/negation/date to its target. Require
    # exact normalized text when these signals occur, and for compound questions.
    risky = r"[0-9,;\n]|권한|허용|금지|불허|철회|않|아니|기간|부터|까지|대신|반면|\b(?:not|no|allow|deny|permission|revoke)\b"
    if re.search(risky, source + " " + candidate, re.IGNORECASE):
        return search.normalized(source) == search.normalized(candidate) and len(left) >= 3 and options == row.get("options", [])
    return 3 <= len(left) <= 12 and left == right and options == row.get("options", [])


def prepare(doc_id, project_id, questions, normalized, provenance, actor):
    results = []
    for index, ((title, body, options), raw) in enumerate(zip(normalized, questions)):
        raw = raw if isinstance(raw, dict) else {}
        reason = raw.get("reuse_rejection_reason")
        rejected = raw.get("rejected_answer_id")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise HTTPException(422, "reuse_rejection_reason must not be blank")
        if reason and not rejected:
            raise HTTPException(422, "rejected_answer_id required with reuse_rejection_reason")
        reason = reason.strip() if reason else None
        payload = [doc_id, provenance, actor, index, title, body, options, reason, rejected]
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        precheck_id = "qpc_" + fingerprint
        existing = audits.get(precheck_id)
        if existing and existing.get("status") == "registered":
            from modules.flow_gate.db import answers
            answer_ids = [a["id"] for item_id in existing.get("added_item_ids", []) for a in answers.list_by_question_item(item_id)]
            if answer_ids:
                # A settled question is a new reuse decision, not a retry of the
                # earlier unanswered insert. Keep both audit records.
                fingerprint = hashlib.sha256((fingerprint + json.dumps(answer_ids)).encode()).hexdigest()
                precheck_id = "qpc_" + fingerprint
                existing = audits.get(precheck_id)
        if existing and existing.get("status") not in ("registration_rolled_back", "batch_blocked", "search_failed"):
            results.append(existing)
            continue
        query = ((title or "") + " " + body).strip()[:200]
        # At most 32 terms in a bounded input; preserve the full input fingerprint above.
        query = " ".join(query.split()[:search.MAX_TERMS])
        record = {"index": index, "precheck_id": precheck_id, "searched": True,
                  "algorithm_version": search.ALGORITHM_VERSION + "/identity-precision-v1", "added_item_ids": [],
                  "reuse_rejection_reason": reason, "query": query, "fingerprint": fingerprint}
        try:
            found = search.search(project_id, query)
        except Exception:
            record.update(status="search_failed", candidates=[], found_count=None, returned_count=0,
                          decision_reason="search_failed", next_action="retry_search")
            audits.save(record, doc_id, project_id, provenance, fingerprint, query, reason)
            audits.preserve_after_rollback(record, doc_id, project_id, provenance, fingerprint, query, reason)
            raise HTTPException(503, {"code": "question_precheck_search_failed", **record})
        candidates = found["items"]
        identical_rows = [r for r in candidates if identical(doc_id, title, body, options, r)]
        answer_texts = {search.normalized(r.get("answer_body")) for r in identical_rows}
        same = identical_rows[0] if identical_rows and len(answer_texts) == 1 else None
        if rejected and not any(r["answer_id"] == rejected for r in candidates):
            raise HTTPException(422, "rejected_answer_id must identify a returned candidate")
        status = "pending_registration"
        if rejected and not reason:
            status = "existing_answer_requires_reason"
        elif same and not reason:
            status = "reused_answer"
        record.update(status=status, candidates=candidates, found_count=found["total_count"],
                      returned_count=len(candidates), reused_answer=same if status == "reused_answer" else None,
                      rejected_answer_id=rejected,
                      decision_reason="same_document_identity_tokens_and_options" if same else "candidates_only_identity_unconfirmed",
                      next_action="continue_with_existing_answer" if status == "reused_answer" else
                                  "resubmit_with_reuse_rejection_reason" if status == "existing_answer_requires_reason" else "register_question")
        results.append(record)
    return results


def persist(record, doc_id, project_id, provenance):
    args = (doc_id, project_id, provenance, record["fingerprint"], record["query"], record.get("reuse_rejection_reason"))
    audits.save(record, *args)
    audits.preserve_after_rollback(record, *args)


def response(doc_id, results):
    ids = [i for r in results for i in r.get("added_item_ids", [])]
    status = results[0]["status"] if len(results) == 1 else "batch_processed"
    result = {"doc_id": doc_id, "added_item_ids": ids, "status": status, "item_results": results}
    if len(results) == 1:
        result.update({k: v for k, v in results[0].items() if k not in ("index", "fingerprint", "query", "added_item_ids")})
    return result
