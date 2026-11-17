"""Search answered Q&A in a project already authorized by the route."""
import json
from modules.flow_gate.db.connection import get_store


def search(project_id, query, limit=10, before_id=None):
    term = query.strip()
    if not term or len(term) > 200 or not 1 <= limit <= 50:
        raise ValueError("invalid_question_search")
    # Match every whitespace-separated keyword across question/title/answer.
    # Keep error codes and SQL wildcard characters literal; never interpolate input.
    terms = list(dict.fromkeys(term.split()))
    condition = "(LOWER(qi.title) LIKE LOWER(?) ESCAPE '!' OR LOWER(qi.body) LIKE LOWER(?) ESCAPE '!' OR LOWER(a.body) LIKE LOWER(?) ESCAPE '!')"
    store = get_store()
    sql = store._sql("question_items.search_answered").replace("{term_conditions}", " AND ".join([condition] * len(terms)))
    params = [project_id, before_id, before_id]
    for keyword in terms:
        escaped = "%" + keyword.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        params.extend([escaped] * 3)
    rows = store._fetch_all(sql, [*params, limit + 1])
    more = len(rows) > limit
    rows = rows[:limit]
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
    return {"ok": True, "items": rows, "next_before_id": rows[-1]["answer_id"] if more else None}
