"""Storage of the chat card AI role API (flowgate.v02gui.0028, migration 190).

- ``chat_placed_cards``: one row per work card a chat placed (``service.place``, after its draft save committed): the
  chat conversation document (the CH ``doc_ref`` of the chat token) that placed it, the token and AI run of that call,
  the user, when. A card's row names the one chat that may read / change its AI roles through the chat API. Rows are
  only inserted (a node key is never reused in a Flow); a group delete removes them (FK cascade, SQLite trigger).
- ``chat_card_role_audit``: one row per requested role of every successful role change through the chat API (the
  role's state before and after as JSON, whether it changed, the card's settings version after the change, the token,
  AI run, CH and acting user). Written inside the change's Flow guard transaction, so a refused or rolled-back change
  leaves no row. Rows are only inserted.
- ``chat_card_setting_audit`` (flowgate.v02gui.0032, migration 191): the same for the run settings a chat may change
  (``run_settings``: review count, restarts, time limit) — one row per requested setting (the shown value and whether it
  was stored, before and after). Optional: without migration 191 a settings change is refused, roles still work.

Nothing here reads or writes the card settings, the card role slots or any account default.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Iterable, Optional

from modules.flow_gate.db.connection import get_store

PLACED_TABLE = "chat_placed_cards"
AUDIT_TABLE = "chat_card_role_audit"
SETTING_AUDIT_TABLE = "chat_card_setting_audit"

_PLACED_COLUMNS = "group_id, node_key, chat_doc_id, token_id, ai_run_id, placed_by, placed_at"
_AUDIT_COLUMNS = ("audit_id, group_id, node_key, role, before_json, after_json, changed, settings_version, token_id, "
                  "ai_run_id, chat_doc_id, actor_user_id, created_at")
_SETTING_AUDIT_COLUMNS = _AUDIT_COLUMNS.replace(" role,", " field,")


def _now() -> str:
    """ISO 8601 UTC with milliseconds, like the other Graph rows."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def available() -> bool:
    """False until migration 190 is applied (both tables)."""
    store = get_store()
    return all(store.table_exists(name) for name in (PLACED_TABLE, AUDIT_TABLE))


# ── placement records ────────────────────────────────────────────────────────

def record_placed(flow_id: str, node_keys: Iterable[str], chat_doc_id: str, token_id: Optional[str],
                  ai_run_id: Optional[str], user_id: Optional[str], at: Optional[str] = None) -> int:
    """Insert one record per placed card in one transaction; returns the number of rows written."""
    keys = [k for k in node_keys if k]
    if not keys or not chat_doc_id:
        return 0
    at = at or _now()
    store = get_store()
    with store.transaction():
        for key in keys:
            store._execute(f"INSERT INTO chat_placed_cards ({_PLACED_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           [flow_id, key, str(chat_doc_id), token_id, ai_run_id, user_id, at])
    return len(keys)


def placed(flow_id: str, node_key: str) -> Optional[dict]:
    """The card's placement record, or None (a card no chat placed through the chat API)."""
    return get_store()._fetch_one(
        f"SELECT {_PLACED_COLUMNS} FROM chat_placed_cards WHERE group_id = ? AND node_key = ?", [flow_id, node_key])


def placed_keys(flow_id: str, chat_doc_id: Optional[str]) -> list[str]:
    """Node keys of the cards ``chat_doc_id`` placed on the Flow (oldest first)."""
    if not chat_doc_id:
        return []
    rows = get_store()._fetch_all(
        "SELECT node_key FROM chat_placed_cards WHERE group_id = ? AND chat_doc_id = ? ORDER BY placed_at, node_key",
        [flow_id, str(chat_doc_id)]) or []
    return [r["node_key"] for r in rows]


# ── audit ────────────────────────────────────────────────────────────────────

def write_audit(flow_id: str, node_key: str, role: str, before: Optional[dict], after: Optional[dict], changed: bool,
                settings_version: Optional[int], token_id: Optional[str], ai_run_id: Optional[str], chat_doc_id: str,
                actor_user_id: str, at: Optional[str] = None) -> str:
    """One audit row (inside the caller's transaction when there is one); returns its id."""
    audit_id = "ccra_" + uuid.uuid4().hex
    get_store()._execute(
        f"INSERT INTO chat_card_role_audit ({_AUDIT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [audit_id, flow_id, node_key, role,
         None if before is None else json.dumps(before, ensure_ascii=False, sort_keys=True),
         None if after is None else json.dumps(after, ensure_ascii=False, sort_keys=True),
         1 if changed else 0, settings_version, token_id, ai_run_id, str(chat_doc_id), actor_user_id, at or _now()])
    return audit_id


def audit_rows(flow_id: str, node_key: Optional[str] = None) -> list[dict]:
    """Audit rows of a Flow (one card's when ``node_key``), oldest first, JSON columns decoded."""
    sql = f"SELECT {_AUDIT_COLUMNS} FROM chat_card_role_audit WHERE group_id = ?"
    params: list = [flow_id]
    if node_key is not None:
        sql += " AND node_key = ?"
        params.append(node_key)
    rows = get_store()._fetch_all(sql + " ORDER BY created_at, audit_id", params) or []
    return [_decoded(row) for row in rows]


def _decoded(row) -> dict:
    row = dict(row)
    for column, name in (("before_json", "before"), ("after_json", "after")):
        raw = row.pop(column, None)
        row[name] = json.loads(raw) if raw else None
    row["changed"] = bool(row.get("changed"))
    return row


# ── run settings audit (flowgate.v02gui.0032, migration 191) ─────────────────

def setting_audit_available() -> bool:
    """False until migration 191 is applied."""
    return get_store().table_exists(SETTING_AUDIT_TABLE)


def write_setting_audit(flow_id: str, node_key: str, field: str, before: Optional[dict], after: Optional[dict],
                        changed: bool, settings_version: Optional[int], token_id: Optional[str],
                        ai_run_id: Optional[str], chat_doc_id: str, actor_user_id: str, at: Optional[str] = None) -> str:
    """One run settings audit row (inside the caller's transaction when there is one); returns its id."""
    audit_id = "ccsa_" + uuid.uuid4().hex
    get_store()._execute(
        f"INSERT INTO chat_card_setting_audit ({_SETTING_AUDIT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [audit_id, flow_id, node_key, field,
         None if before is None else json.dumps(before, ensure_ascii=False, sort_keys=True),
         None if after is None else json.dumps(after, ensure_ascii=False, sort_keys=True),
         1 if changed else 0, settings_version, token_id, ai_run_id, str(chat_doc_id), actor_user_id, at or _now()])
    return audit_id


def setting_audit_rows(flow_id: str, node_key: Optional[str] = None) -> list[dict]:
    """Run settings audit rows of a Flow (one card's when ``node_key``), oldest first, JSON columns decoded."""
    sql = f"SELECT {_SETTING_AUDIT_COLUMNS} FROM chat_card_setting_audit WHERE group_id = ?"
    params: list = [flow_id]
    if node_key is not None:
        sql += " AND node_key = ?"
        params.append(node_key)
    rows = get_store()._fetch_all(sql + " ORDER BY created_at, audit_id", params) or []
    return [_decoded(row) for row in rows]
