"""One saved question policy per user. Empty means use the existing defaults."""
from .connection import get_store, now_iso

MAX_CHARS = 4000


def get(user_id):
    if not user_id or not get_store().table_exists("user_question_policies"):
        return {"body": "", "updated_at": None}
    return get_store()._fetch_one(
        "SELECT body, updated_at FROM user_question_policies WHERE user_id = ?", [user_id]
    ) or {"body": "", "updated_at": None}


def save(user_id, body):
    if not isinstance(body, str) or len(body) > MAX_CHARS:
        raise ValueError("question_policy_too_long")
    get_store()._execute(
        "INSERT INTO user_question_policies (user_id, body, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET body = excluded.body, updated_at = excluded.updated_at",
        [user_id, body, now_iso()],
    )
    return get(user_id)
