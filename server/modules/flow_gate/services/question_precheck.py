"""Reuse gate over the broad OR candidates: a settled project answer is returned instead of a new question."""
import hashlib
import json
import re
from fastapi import HTTPException
from modules.flow_gate.db import question_search_audits as audits
from modules.flow_gate.services import ai_text_guard
from modules.flow_gate.services import question_search as search

# Bag-of-words cannot bind a permission/negation/date to its target, and a compound
# question has more than one target: with these signals only the same words in the
# same order count as the same question.
_RISKY = r"[0-9,;\n]|권한|허용|금지|불허|철회|않|아니|기간|부터|까지|대신|반면|\b(?:not|no|allow|deny|permission|revoke)\b"
SIMILAR_JACCARD = 0.7
# Closed list of words that only say "I am asking". An added word outside this list may
# name another target or action ("delete the production DB", "including rollback") and is
# never the same question.
_FILLER = frozenset(
    "요청 문의 부탁 여부 질문 드립니다 부탁드립니다 요청드립니다 문의드립니다 바랍니다 "
    "주세요 해주세요 알려주세요 궁금합니다 혹시 please question request".split())


def _tokens(value):
    # Only a small closed list of grammatical particles; retain dates, error codes,
    # permissions, negations and target names in the identity itself.
    # "!" stays inside a word but an exclamation mark around it is punctuation.
    words = [w for w in (m.strip("!") for m in re.findall(r"[\w%!]+", search.normalized(value))) if w]
    return [re.sub(r"(?<=[가-힣])(?:에서|으로|에게|은|는|을|를|이|가|의|에)$", "", w) if len(w) > 2 else w for w in words]


def identity_tokens(value):
    return sorted(_tokens(value))


def _question_text(title, body):
    """The text that identifies a question: the body alone when the title only shortens it.

    An AI usually adds a title that repeats the body in fewer words ("nightly staging deploy:
    go ahead?" for "confirm the agent may go ahead with the nightly staging server deploy").
    Such a title names nothing new: every word of it is an asking-word or the start of a body
    word. A title with a word of its own ("staging deploy permission") may name another target
    and stays part of the identity.
    """
    body = body or ""
    words = _tokens(title or "")
    if not words:
        return body
    body_words = _tokens(body)
    for word in words:
        if word in _FILLER or word in body_words:
            continue
        if len(word) < 2 or not any(other.startswith(word) for other in body_words):
            return (title or "") + " " + body
    return body


def stored_text_damaged(row):
    """A stored question or answer that reads as lost in transit (``?? ????``, U+FFFD, mojibake).

    Rows saved before the registration gate existed stay in the database and in the board list,
    but one is never handed out as the settled answer.
    """
    return any(ai_text_guard.corruption_signal(row.get(field)) for field in ("title", "body", "answer_body"))


def match_kind(title, body, options, row):
    """'identical', 'similar' or None for one answered candidate from any document of the project."""
    if row.get("body_truncated") or options != row.get("options", []):
        return None
    if stored_text_damaged(row):
        return None
    source = _question_text(title, body)
    candidate = _question_text(row.get("title"), row.get("body", ""))
    left, right = _tokens(source), _tokens(candidate)
    if len(left) < 3:
        return None
    # Same words in the same order; spacing, punctuation and particles do not matter.
    if left == right or "".join(left) == "".join(right):
        return "identical"
    if re.search(_RISKY, source + " " + candidate, re.IGNORECASE):
        return None
    a, b = set(left), set(right)
    if sorted(left) == sorted(right):
        return "identical"
    # One question only adds a few asking-words to the other. A swapped word (another
    # target) leaves words on both sides, and an added topic word may change the target.
    if (a <= b or b <= a) and (a ^ b) <= _FILLER and len(a & b) >= 3 and len(a & b) / len(a | b) >= SIMILAR_JACCARD:
        return "similar"
    return None


def identical(doc_id, title, body, options, row):
    return match_kind(title, body, options, row) == "identical"


def choose(doc_id, title, body, options, candidates):
    """(row, decision_reason). The asking document's own answers outrank other documents'."""
    matches = [(row, kind) for row in candidates for kind in [match_kind(title, body, options, row)] if kind]
    if not matches:
        return None, "candidates_only_identity_unconfirmed"
    own = [m for m in matches if m[0]["doc_id"] == doc_id]
    pool = own or matches
    # Answers that disagree are evidence, not a decision.
    if len({search.normalized(row.get("answer_body")) for row, _ in pool}) != 1:
        return None, "candidates_only_conflicting_answers"
    row, kind = pool[0]
    if own:
        return row, "same_document_identity_tokens_and_options" if kind == "identical" else "same_document_similar_question"
    return row, "project_identical_question" if kind == "identical" else "project_similar_question"


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
        # Topic words only, bounded; the full input stays in the fingerprint above.
        query = search.query_from_text((title or "") + " " + body)
        record = {"index": index, "precheck_id": precheck_id, "searched": True,
                  "algorithm_version": search.ALGORITHM_VERSION + "/project-reuse-v1", "added_item_ids": [],
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
        same, decision = choose(doc_id, title, body, options, candidates)
        if rejected and not any(r["answer_id"] == rejected for r in candidates):
            raise HTTPException(422, "rejected_answer_id must identify a returned candidate")
        status = "pending_registration"
        if rejected and not reason:
            status = "existing_answer_requires_reason"
        elif same and not reason:
            status = "reused_answer"
        # Candidates go out as a board list; only the one reused answer is carried in full.
        record.update(status=status, candidates=[search.summary(r) for r in candidates], found_count=found["total_count"],
                      returned_count=len(candidates), reused_answer=same if status == "reused_answer" else None,
                      rejected_answer_id=rejected, decision_reason=decision,
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
        # The single item is spelled out at the top level; do not carry its list twice.
        result["item_results"] = [{k: v for k, v in results[0].items() if k not in ("candidates", "reused_answer")}]
        result.update({k: v for k, v in results[0].items() if k not in ("index", "fingerprint", "query", "added_item_ids")})
    return result
