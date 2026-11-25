"""User-owned AI preferences. Partial upserts preserve the other saved field."""
from .connection import get_store, now_iso


def get(user_id):
    if not user_id or not get_store().table_exists("user_ai_preferences"):
        return {"title_provider": "auto", "repeat_count_max": None}
    return get_store()._fetch_one(
        "SELECT title_provider, repeat_count_max FROM user_ai_preferences WHERE user_id = ?", [user_id]
    ) or {"title_provider": "auto", "repeat_count_max": None}


def patch(user_id, values):
    columns = [key for key in ("title_provider", "repeat_count_max") if key in values]
    if not columns:
        return
    names = ", ".join(columns)
    assignments = ", ".join(key + " = excluded." + key for key in columns)
    placeholders = ", ".join("?" for _ in columns)
    get_store()._execute(
        f"INSERT INTO user_ai_preferences (user_id, {names}, updated_at) VALUES (?, {placeholders}, ?) "
        f"ON CONFLICT(user_id) DO UPDATE SET {assignments}, updated_at = excluded.updated_at",
        [user_id, *(values[key] for key in columns), now_iso()],
    )
