"""Search answered Q&A in a project already authorized by the route."""
import json
from modules.flow_gate.db.connection import get_store


def search(project_id, query, limit=10, before_id=None):
    term = query.strip()
    if not term or len(term) > 200 or not 1 <= limit <= 50:
        raise ValueError("invalid_question_search")
    term = "%" + term.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
    rows = get_store()._fetch_all(get_store()._sql("question_items.search_answered"),
        [project_id, before_id, before_id, term, term, term, limit + 1])
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
