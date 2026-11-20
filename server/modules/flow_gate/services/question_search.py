"""Project scoped literal OR search; all matches are ranked before pagination.

Callers get a board: ``listing`` returns one short row per answer and ``detail`` opens
one answer in full. ``search`` itself keeps full rows for the server's own identity check.
"""
import base64
import hashlib
import json
import re
import unicodedata
from modules.flow_gate.db.connection import get_store

ALGORITHM_VERSION = "or-fields-dice-v2"
MAX_TERMS = 32
MAX_QUERY_CHARS = 200
PREVIEW_CHARS = 120
TITLE_CHARS = 80
DETAIL_CHARS = 20000

_EDGE = " \t\r\n.,?!;:\"'()[]{}<>~“”‘’…·、。？！「」『』"
# Substring matching makes stripping a trailing particle recall-only: the bare noun still
# finds the noun followed by a particle.
_PARTICLE = re.compile(r"(?<=[가-힣]{2})[)\]\"'”’」』>]*(?:에서|으로|에게|까지|부터|은|는|을|를|이|가|의|에|로|와|과|도|만)$")
# Closed list of words that say nothing about the topic. Negations (no, not, 않, 없) stay.
STOPWORDS = frozenset(
    "a an the of to in on at is are was be it this that and or for with as by do does "
    "we i you can should would please "
    "그리고 또는 및 등 것 수 좀 그 이 저 때 더 왜 어떻게 무엇 어떤 "
    "하는 있는 입니다 합니다 할까요 될까요 되나요 인가요 있나요 해도".split())


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def dice(left, right):
    def grams(s):
        s = normalized(s)
        return {s[i:i+3] for i in range(max(0, len(s)-2))}
    a, b = grams(left), grams(right)
    return 2 * len(a & b) / (len(a) + len(b)) if a and b else 0.0


def search_terms(text):
    """Distinct topic words in input order: edge punctuation, particles and stopwords removed."""
    words = (text or "").lower().split()
    terms = []
    for word in words:
        core = _PARTICLE.sub("", word.strip(_EDGE)).strip(_EDGE) or word
        if core not in STOPWORDS and core not in terms:
            terms.append(core)
    # A query made only of stopwords is still a query.
    return terms or list(dict.fromkeys(words))


def query_from_text(text):
    """A bounded search query from a whole question: topic words only, not the raw text."""
    picked = []
    for term in search_terms(text)[:MAX_TERMS]:
        term = term[:MAX_QUERY_CHARS]
        if len(" ".join(picked + [term])) > MAX_QUERY_CHARS:
            break
        picked.append(term)
    return " ".join(picked)


def _decorate(row):
    row["algorithm_version"] = ALGORITHM_VERSION
    row["author_provider"] = {"ai_run_id": row.get("author_ai_run_id"), "ai_provider_id": row.get("author_actual_provider_id"), "ai_provider_name": row.get("author_actual_provider_name")}
    row["author_label"] = row.get("author_actual_provider_name") or row.get("author_name") or "미확인"


def _finish(row, cap):
    for field in ("options", "selected_options"):
        try:
            row[field] = json.loads(row.get(field) or "[]")
        except (ValueError, TypeError):
            row[field] = []
    for field in ("body", "answer_body"):
        raw = row.get(field) or ""
        row[field + "_truncated"] = len(raw) > cap
        row[field] = raw[:cap]
    return row


def search(project_id, query, limit=10, before_id=None, cursor=None):
    term = query.strip()
    if not term or len(term) > MAX_QUERY_CHARS or not 1 <= limit <= 50:
        raise ValueError("invalid_question_search")
    # Canonical term order makes reordered queries share a pagination contract.
    terms = sorted(search_terms(term))
    if len(terms) > MAX_TERMS:
        raise ValueError("too_many_search_terms")
    signature = hashlib.sha256(json.dumps([project_id, terms, ALGORITHM_VERSION]).encode()).hexdigest()
    condition = "(LOWER(qi.title) LIKE LOWER(?) ESCAPE '!' OR LOWER(qi.body) LIKE LOWER(?) ESCAPE '!' OR LOWER(a.body) LIKE LOWER(?) ESCAPE '!')"
    store = get_store()
    # Parenthesize the OR expression so it cannot escape the project boundary.
    sql = store._sql("question_items.search_answered").replace("{term_conditions}", "(" + " OR ".join([condition] * len(terms)) + ")")
    params = [project_id]
    for keyword in terms:
        escaped = "%" + keyword.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        params.extend([escaped] * 3)
    rows = store._fetch_all(sql, params)
    for row in rows:
        fields = [str(row.get(k) or "").lower() for k in ("title", "body", "answer_body")]
        hits = [max((weight for field, weight in zip(fields, (4, 3, 1)) if t in field), default=0) for t in terms]
        row["matched_terms"] = [t for t, hit in zip(terms, hits) if hit]
        row["score"] = round(sum(hits) / (4 * len(terms)) + .1 * dice(" ".join(terms), " ".join(fields[:2])), 8)
        _decorate(row)
    rows.sort(key=lambda r: (r["score"], r["answer_id"]), reverse=True)
    total = len(rows)
    boundary = None
    if cursor:
        try:
            data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if data[0] != signature or len(data) != 3:
                raise ValueError()
            boundary = (float(data[1]), int(data[2]))
        except Exception as exc:
            raise ValueError("invalid_search_cursor") from exc
    elif before_id is not None:
        anchor = next((r for r in rows if r["answer_id"] == before_id), None)
        if anchor is None:
            raise ValueError("invalid_search_cursor")
        boundary = (anchor["score"], anchor["answer_id"])
    if boundary is not None:
        rows = [r for r in rows if (r["score"], r["answer_id"]) < boundary]
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = base64.urlsafe_b64encode(json.dumps([signature, rows[-1]["score"], rows[-1]["answer_id"]]).encode()).decode() if more else None
    for row in rows:
        _finish(row, 4000)
    return {"ok": True, "items": rows, "total_count": total, "returned_count": len(rows), "next_cursor": next_cursor, "next_before_id": rows[-1]["answer_id"] if more else None, "algorithm_version": ALGORITHM_VERSION}


def summary(row):
    """One board row: where the answer is and how it starts, never the bodies."""
    body = " ".join((row.get("body") or "").split())
    answer = " ".join((row.get("answer_body") or "").split())
    title = " ".join((row.get("title") or "").split()) or body
    return {"answer_id": row.get("answer_id"), "item_id": row.get("item_id"), "doc_id": row.get("doc_id"),
            "document_title": row.get("document_title"), "seq": row.get("seq"), "title": title[:TITLE_CHARS],
            "author_label": row.get("author_label"), "author_kind": row.get("author_kind"),
            "answered_at": row.get("answered_at"), "score": row.get("score"),
            "matched_terms": row.get("matched_terms", []), "answer_preview": answer[:PREVIEW_CHARS],
            "answer_preview_truncated": len(answer) > PREVIEW_CHARS or bool(row.get("answer_body_truncated"))}


def listing(result):
    return dict(result, items=[summary(row) for row in result["items"]], view="list")


def detail(project_id, answer_id):
    """The full question and answer of one listed row, inside the same project boundary."""
    store = get_store()
    sql = store._sql("question_items.search_answered").replace("{term_conditions}", "a.id = ?")
    row = store._fetch_one(sql, [project_id, answer_id])
    if row is None:
        return None
    _decorate(row)
    return _finish(row, DETAIL_CHARS)
