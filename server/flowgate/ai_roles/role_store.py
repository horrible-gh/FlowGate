"""Account role defaults, card role slots and card init state (flowgate.v02.0113 T#1, DB-1 §2.1-§2.3, §4).

Migration 163: ``user_ai_role_defaults``, ``graph_node_role_slots``, ``graph_node_role_init``.

- An account row means setup completed (L-1 ``AccountRoles``). Its three values are written by one statement
  (W-1) and ``setup_completed_at`` only by the INSERT, so the first completion time stays when two first saves
  race; the last commit decides the values. A transaction-level failure (lock, deadlock) runs the save again,
  three attempts in all (DB-1 §4.3).
- A slot row is the role's explicit marker (L-1 ``RoleSlot``). There are exactly two DELETE statements: W-2's
  replace (the same key is inserted again at once) and W-3, the WP re-deploy's removal of a ``wp_plan`` slot.
- An init row is the card's init state (L-1 ``CardInit``); no row = ``LEGACY``. It only moves
  ``pending_init -> initialized`` (W-5).
- Slot and init writes run inside the caller's Flow guard; when one changed something the caller moves the card
  settings version once (W-6, ``card_settings.repository.bump_version``) in the same transaction.
- No AI id column references ``ai_providers`` (DB-D4): a deleted AI keeps its original id and name snapshot.
- Nothing here reads or writes ``graph_node_settings`` (DB-1 I-19).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Optional

from modules.flow_gate.db.connection import get_store, in_transaction, is_transient_failure
from modules.flow_gate.db.dialect import MYSQL

ACCOUNT_TABLE = "user_ai_role_defaults"
SLOT_TABLE = "graph_node_role_slots"
INIT_TABLE = "graph_node_role_init"

ROLES = ("instruction", "execution", "review")
ASSIGNED, NOT_USED = "assigned", "not_used"
SLOT_SOURCES = ("creation_copy", "wp_plan", "pending_fill", "card_explicit")
PENDING_INIT, INITIALIZED = "pending_init", "initialized"
NAME_SNAPSHOT_MAX = 100          # DB-D10: the AI name limit (ai_settings_service.NAME_MAX)
ACCOUNT_SAVE_ATTEMPTS = 3        # DB-1 §4.3 W-1: first attempt included

_ACCOUNT_VALUE_COLUMNS = tuple(f"{role}_{part}" for role in ROLES for part in ("kind", "provider_id", "provider_name"))
_ACCOUNT_UPDATE = (
    "UPDATE user_ai_role_defaults SET instruction_kind = ?, instruction_provider_id = ?, "
    "instruction_provider_name = ?, execution_kind = ?, execution_provider_id = ?, execution_provider_name = ?, "
    "review_kind = ?, review_provider_id = ?, review_provider_name = ?, updated_at = ? WHERE user_id = ?"
)
_ACCOUNT_INSERT = (
    "INSERT INTO user_ai_role_defaults (user_id, instruction_kind, instruction_provider_id, "
    "instruction_provider_name, execution_kind, execution_provider_id, execution_provider_name, review_kind, "
    "review_provider_id, review_provider_name, setup_completed_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_SLOT_COLUMNS = "role, kind, provider_id, provider_name, source, set_by, set_at"


class AccountRolesWriteError(RuntimeError):
    """The account's three values were not stored (nothing was written, L-1 S-19)."""


@dataclass(frozen=True)
class RoleValue:
    """One role's value: L-1 ``AccountRoleValue`` and the value part of a ``RoleSlot``."""
    kind: str                                  # ASSIGNED | NOT_USED
    provider_id: Optional[str] = None          # ASSIGNED only; kept as it is when the AI is deleted
    provider_name: Optional[str] = None        # name snapshot when the value was set (None = none)


@dataclass(frozen=True)
class AccountRoles:
    """L-1 ``AccountRoles``: the row exists = setup completed."""
    values: Mapping[str, RoleValue]            # instruction / execution / review
    setup_completed_at: str
    updated_at: str


@dataclass(frozen=True)
class RoleSlot:
    """L-1 ``RoleSlot`` of one role of one card."""
    role: str
    value: RoleValue
    source: str                                # SLOT_SOURCES
    set_by: Optional[str]                      # None = SYSTEM (pending_fill only)
    set_at: str


@dataclass(frozen=True)
class CardInit:
    """L-1 ``CardInit`` (no row = LEGACY)."""
    state: str                                 # PENDING_INIT | INITIALIZED
    creator_id: Optional[str]
    created_at: str
    initialized_at: Optional[str]


def _now() -> str:
    """ISO 8601 UTC with milliseconds, like the other Graph rows (``card_settings.repository._now``)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def name_snapshot(name: Optional[str]) -> Optional[str]:
    """DB-D10: the name is a display aid (the id identifies the AI), so a longer one keeps its first 100 chars."""
    if name is None:
        return None
    return str(name)[:NAME_SNAPSHOT_MAX]


def available() -> bool:
    """False until migration 163 is applied (all three tables): the role feature is off."""
    store = get_store()
    return all(store.table_exists(name) for name in (ACCOUNT_TABLE, SLOT_TABLE, INIT_TABLE))


# ── account role defaults (R-1, W-1) ─────────────────────────────────────────

def _account_from_row(row: Optional[dict]) -> Optional[AccountRoles]:
    if not row:
        return None
    values = {role: RoleValue(row[f"{role}_kind"], row[f"{role}_provider_id"], row[f"{role}_provider_name"])
              for role in ROLES}
    return AccountRoles(values, row["setup_completed_at"], row["updated_at"])


def account_roles(user_id: str) -> Optional[AccountRoles]:
    """R-1. None = no row = the account has not completed setup (every role UNSET)."""
    return _account_from_row(get_store()._fetch_one(
        "SELECT * FROM user_ai_role_defaults WHERE user_id = ?", [user_id]))


def _account_params(values: Mapping[str, RoleValue]) -> list:
    missing = [role for role in ROLES if role not in values]
    if missing:
        raise ValueError(f"account roles need all three values, missing {missing}")
    params: list = []
    for role in ROLES:
        value = values[role]
        params += [value.kind, value.provider_id, name_snapshot(value.provider_name)]
    return params


def _locked_account_row(store, user_id: str) -> Optional[dict]:
    """The committed row after a lost first insert (MySQL REPEATABLE READ needs a locking read, DB-1 §4.3)."""
    sql = "SELECT * FROM user_ai_role_defaults WHERE user_id = ?"
    if store.dialect == MYSQL:
        sql += " FOR UPDATE"
    return store._fetch_one(sql, [user_id])


def _save_account_once(store, user_id: str, params: list, at: str) -> None:
    if store._execute_affected(_ACCOUNT_UPDATE, [*params, at, user_id]) == 1:          # ① a row was there
        return
    if store.insert_isolated(_ACCOUNT_INSERT, [user_id, *params, at, at], "fg_uard_ins"):  # ② first save
        return
    if store._execute_affected(_ACCOUNT_UPDATE, [*params, at, user_id]) == 1:          # ③ lost the first save
        return
    # MySQL counts changed rows only: a row that already holds exactly these values reports 0. Anything else
    # (no row after a unique violation, other values) is a failed save.
    row = _locked_account_row(store, user_id)
    if row is None or [row[column] for column in _ACCOUNT_VALUE_COLUMNS] != params:
        raise AccountRolesWriteError("account role values were not stored")


def save_account_roles(user_id: str, values: Mapping[str, RoleValue], now: Optional[str] = None) -> AccountRoles:
    """W-1: store the account's three role values at once and return the stored row.

    The service validated the values (L-1 §2.3.2); the CHECKs are the last line. Its own transaction, run again
    on a lock / deadlock failure up to ``ACCOUNT_SAVE_ATTEMPTS`` times. Inside a caller's transaction there is
    one attempt and its failure is the caller's.
    """
    params = _account_params(values)
    store = get_store()
    if in_transaction():
        _save_account_once(store, user_id, params, now or _now())
        stored = account_roles(user_id)
        if stored is None:
            raise AccountRolesWriteError("account role values were not stored")
        return stored
    failure: Optional[BaseException] = None
    for _attempt in range(ACCOUNT_SAVE_ATTEMPTS):
        try:
            with store.transaction():
                _save_account_once(store, user_id, params, now or _now())
                stored = account_roles(user_id)
            if stored is None:
                raise AccountRolesWriteError("account role values were not stored")
            return stored
        except Exception as exc:
            if not is_transient_failure(exc):
                raise
            failure = exc
    raise AccountRolesWriteError("account role values were not stored after retries") from failure


# ── card role slots (R-2, W-2, W-3) ──────────────────────────────────────────

def _slot_from_row(row: dict) -> RoleSlot:
    return RoleSlot(row["role"], RoleValue(row["kind"], row["provider_id"], row["provider_name"]),
                    row["source"], row["set_by"], row["set_at"])


def slots(flow_id: str, node_key: str) -> dict[str, RoleSlot]:
    """R-2: ``role -> RoleSlot`` of one card (a missing role = no marker)."""
    found = get_store()._fetch_all(
        "SELECT role, kind, provider_id, provider_name, source, set_by, set_at FROM graph_node_role_slots "
        "WHERE group_id = ? AND node_key = ?", [flow_id, node_key])
    return {row["role"]: _slot_from_row(row) for row in found or []}


def write_slot(flow_id: str, node_key: str, slot: RoleSlot) -> bool:
    """W-2: replace one role's slot (delete + insert, the same statements on every dialect), inside the caller's
    Flow guard. True when the stored value, source or setter changed (the caller then moves the version)."""
    store = get_store()
    before = slots(flow_id, node_key).get(slot.role)
    value = slot.value
    store._execute("DELETE FROM graph_node_role_slots WHERE group_id = ? AND node_key = ? AND role = ?",
                   [flow_id, node_key, slot.role])
    store._execute(
        "INSERT INTO graph_node_role_slots (group_id, node_key, role, kind, provider_id, provider_name, source, "
        "set_by, set_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [flow_id, node_key, slot.role, value.kind, value.provider_id, name_snapshot(value.provider_name),
         slot.source, slot.set_by, slot.set_at])
    stored = RoleValue(value.kind, value.provider_id, name_snapshot(value.provider_name))
    return before is None or (before.value, before.source, before.set_by) != (stored, slot.source, slot.set_by)


def remove_wp_plan_slot(flow_id: str, node_key: str, role: str) -> bool:
    """W-3: the WP re-deploy's plan no longer sets this role. Only a ``wp_plan`` slot goes; a slot of any other
    source stays (DB-1 I-4). True when a row was removed."""
    return get_store()._execute_affected(
        "DELETE FROM graph_node_role_slots WHERE group_id = ? AND node_key = ? AND role = ? AND source = 'wp_plan'",
        [flow_id, node_key, role]) > 0


# ── card init state (R-3, W-4, W-5) ──────────────────────────────────────────

def card_init(flow_id: str, node_key: str) -> Optional[CardInit]:
    """R-3. None = no row = LEGACY."""
    row = get_store()._fetch_one(
        "SELECT state, creator_id, created_at, initialized_at FROM graph_node_role_init "
        "WHERE group_id = ? AND node_key = ?", [flow_id, node_key])
    if not row:
        return None
    return CardInit(row["state"], row["creator_id"], row["created_at"], row["initialized_at"])


def create_card_init(flow_id: str, node_key: str, state: str, creator_id: Optional[str],
                     now: Optional[str] = None) -> CardInit:
    """W-4: the card's first init row (new card, WP deploy, or a LEGACY card becoming ``initialized``). The
    primary key refuses a second row."""
    at = now or _now()
    initialized_at = at if state == INITIALIZED else None
    get_store()._execute(
        "INSERT INTO graph_node_role_init (group_id, node_key, state, creator_id, created_at, initialized_at) "
        "VALUES (?, ?, ?, ?, ?, ?)", [flow_id, node_key, state, creator_id, at, initialized_at])
    return CardInit(state, creator_id, at, initialized_at)


def mark_initialized(flow_id: str, node_key: str, now: Optional[str] = None) -> bool:
    """W-5: ``pending_init -> initialized``, the only state change (``creator_id`` stays). True when it moved."""
    return get_store()._execute_affected(
        "UPDATE graph_node_role_init SET state = 'initialized', initialized_at = ? "
        "WHERE group_id = ? AND node_key = ? AND state = 'pending_init'", [now or _now(), flow_id, node_key]) > 0
