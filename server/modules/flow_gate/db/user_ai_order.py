"""Each user's display order of AI (flowgate.v02test.0025, migration 185).

One list over the system AI and the user's own AI. It orders every AI selection list the user
sees; it is never a fallback order (that stays ai_providers.sort_order of the system rows).
An AI missing from the list (added later) is shown after the listed ones, in its own order.
"""
from __future__ import annotations

from .connection import get_store, now_iso


def list_ids(user_id: str) -> list[str]:
    """The saved order (live AI only), first to last. Empty = never saved."""
    rows = get_store()._fetch_all(
        "SELECT o.provider_id FROM user_ai_order o JOIN ai_providers p ON p.provider_id = o.provider_id "
        "WHERE o.user_id = ? AND p.deleted_at IS NULL ORDER BY o.sort_order, o.provider_id",
        [user_id],
    )
    return [r["provider_id"] for r in rows]


def replace(store, user_id: str, provider_ids: list[str]) -> None:
    """Replace the user's order inside the caller's transaction (``store`` = the transaction)."""
    now = now_iso()
    store._execute("DELETE FROM user_ai_order WHERE user_id = ?", [user_id])
    for index, provider_id in enumerate(provider_ids):
        store._execute(
            "INSERT INTO user_ai_order (user_id, provider_id, sort_order, updated_at) VALUES (?, ?, ?, ?)",
            [user_id, provider_id, index, now],
        )
