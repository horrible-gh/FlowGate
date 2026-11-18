"""Project scoped literal OR search; all matches are ranked before pagination."""
import base64
import hashlib
import json
import re
import unicodedata
from modules.flow_gate.db.connection import get_store

ALGORITHM_VERSION = "or-fields-dice-v1"
MAX_TERMS = 32


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def dice(left, right):
    def grams(s):
        s = normalized(s)
        return {s[i:i+3] for i in range(max(0, len(s)-2))}
    a, b = grams(left), grams(right)
    return 2 * len(a & b) / (len(a) + len(b)) if a and b else 0.0


def search(project_id, query, limit=10, before_id=None, cursor=None):
    term = query.strip()
    if not term or len(term) > 200 or not 1 <= limit <= 50:
        raise ValueError("invalid_question_search")
    # Canonical term order makes reordered queries share a pagination contract.
    terms = sorted(set(term.lower().split()))
    if len(terms) > MAX_TERMS:
        raise ValueError("too_many_search_terms")
    signature = hashlib.sha256(json.dumps([project_id, terms, ALGORITHM_VERSION]).encode()).hexdigest()
    condition = "(LOWER(qi.title) LIKE LOWER(?) ESCAPE '!' OR LOWER(qi.body) LIKE LOWER(?) ESCAPE '!' OR LOWER(a.body) LIKE LOWER(?) ESCAPE '!')"
    store = get_store()
    sql = store._sql("question_items.search_answered").replace("{term_conditions}", " OR ".join([condition] * len(terms)))
    # Parenthesize the OR expression so it cannot escape the project boundary.
    sql = sql.replace("AND " + " OR ".join([condition] * len(terms)), "AND (" + " OR ".join([condition] * len(terms)) + ")")
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
        row["algorithm_version"] = ALGORITHM_VERSION
        row["author_provider"] = {"ai_run_id": row.get("author_ai_run_id"), "ai_provider_id": row.get("author_actual_provider_id"), "ai_provider_name": row.get("author_actual_provider_name")}
        row["author_label"] = row.get("author_actual_provider_name") or row.get("author_name") or "미확인"
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
        for field in ("options", "selected_options"):
            try:
                row[field] = json.loads(row.get(field) or "[]")
            except (ValueError, TypeError):
                row[field] = []
        for field in ("body", "answer_body"):
            raw = row.get(field) or ""
            row[field + "_truncated"] = len(raw) > 4000
            row[field] = raw[:4000]
    return {"ok": True, "items": rows, "total_count": total, "returned_count": len(rows), "next_cursor": next_cursor, "next_before_id": rows[-1]["answer_id"] if more else None, "algorithm_version": ALGORITHM_VERSION}
