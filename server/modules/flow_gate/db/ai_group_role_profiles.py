"""persisted common/per-document role cells, independent of runtime bindings.

Providers deliberately have no FK: deletion retains the id and last assigned name.
Callers own the transaction; locking the parent serializes edits and row positions.
(migration 191): ``review_count`` is the row's default review count (NULL: a type row inherits
the common row, the common row means the existing card default 0). It is copied into a card only when the card is
created; nothing reads it at run time.
"""
from .connection import get_store, now_iso

ROLES = ("instruction", "execution", "review")
COMMON = "common"


def stored_review_count(typed: dict | None, common: dict | None) -> tuple[int | None, str]:
    """(stored value, its level) before any policy judgment: the type row's non-NULL value ("type"), else the common
    row's ("common"), else (None, "default")."""
    for row, source in ((typed, "type"), (common, "common")):
        value = (row or {}).get("review_count")
        if value is not None:
            return int(value), source
    return None, "default"


def effective_review_count(typed: dict | None, common: dict | None, choices) -> tuple[int, str]:
    """(review count, where it came from) a new card of a type copies, judged against ``choices`` (the card 검수 횟수
    values the policy allows today): the type row's non-NULL value ("type"), else the common row's ("common"), else the
    existing card default 0 ("default"). A stored value outside ``choices`` (e.g. 5 kept after the max was lowered to
    3) is not copied: the card gets 0 ("default"). The API's effective value and the creation copy both use this one
    judgment, so what the API shows is what a new card stores. The stored value itself is never changed here."""
    value, source = stored_review_count(typed, common)
    if value is None or value not in choices:
        return 0, "default"
    return value, source


def lock_group(ai_group_id: str) -> None:
    # An actual write takes the parent row's lock on all supported backends,
    # before any read, including SQLite's deferred transactions.
    get_store()._execute("UPDATE ai_groups SET updated_at = ? WHERE ai_group_id = ?",
                         [now_iso(), ai_group_id])


def list_profiles(ai_group_id: str) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM ai_group_role_profiles WHERE ai_group_id = ? ORDER BY position, type_key",
        [ai_group_id])


def get(ai_group_id: str, type_key: str) -> dict | None:
    return get_store()._fetch_one(
        "SELECT * FROM ai_group_role_profiles WHERE ai_group_id = ? AND type_key = ?",
        [ai_group_id, type_key])


def create(ai_group_id: str, type_key: str) -> None:
    store = get_store()
    common = type_key == COMMON
    position = 0 if common else int(store._fetch_one(
        "SELECT COALESCE(MAX(position), 0) AS p FROM ai_group_role_profiles WHERE ai_group_id = ?",
        [ai_group_id])["p"]) + 1
    kind = "unset" if common else "inherit"
    now = now_iso()
    store._execute(
        "INSERT INTO ai_group_role_profiles (ai_group_id, type_key, position, "
        "instruction_kind, execution_kind, review_kind, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [ai_group_id, type_key, position, kind, kind, kind, now, now])


def set_cell(ai_group_id: str, type_key: str, role: str, kind: str,
             provider_id: str | None, provider_name: str | None) -> None:
    if role not in ROLES:
        raise ValueError("invalid role")
    get_store()._execute(
        f"UPDATE ai_group_role_profiles SET {role}_kind = ?, {role}_provider_id = ?, "
        f"{role}_provider_name = ?, updated_at = ? WHERE ai_group_id = ? AND type_key = ?",
        [kind, provider_id, provider_name, now_iso(), ai_group_id, type_key])


def set_review_count(ai_group_id: str, type_key: str, review_count: int | None) -> None:
    get_store()._execute(
        "UPDATE ai_group_role_profiles SET review_count = ?, updated_at = ? WHERE ai_group_id = ? AND type_key = ?",
        [review_count, now_iso(), ai_group_id, type_key])


def delete(ai_group_id: str, type_key: str) -> None:
    if type_key == COMMON:
        raise ValueError("common profile cannot be deleted")
    get_store()._execute(
        "DELETE FROM ai_group_role_profiles WHERE ai_group_id = ? AND type_key = ?",
        [ai_group_id, type_key])
