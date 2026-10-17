"""Node title state store (0064 DB0007 §2.1 / §4 Q1~Q4, migration 155_graph_node_title_states.sql).

One row per card (Flow, node key); no row is the not-judged state ``U`` (0062 L0006 3.1), shown as version 0 and
never written just to be read. Every write runs inside the caller's Flow guard and raises ``version`` in SQL
(``version = version + 1``), so the version follows the commit order. ``human_edited`` only goes 0 -> 1 and no
write here sets ``title_source`` back from ``human``. ``claim`` / ``close`` / ``apply_ai`` are the AI title
opportunity writes of 0062 L0006 2.4 / 2.6 (their caller comes with the AI naming set); each is conditional on the
version the caller read and answers the rows it changed (0: the caller lost a race).
SQL is written in the SQLite/PostgreSQL form; ``db/dialect.py`` rewrites it for MySQL.
"""
from __future__ import annotations

from typing import Optional

from modules.flow_gate.db.connection import get_store

from .layout_store import utc_now_ms

TITLE_TABLE = "graph_node_title_states"
STATE_VERSION_START = 1
STATE_VERSION_NONE = 0
SOURCE_NONE, SOURCE_AI, SOURCE_HUMAN = "none", "ai", "human"
UNUSED, CLAIMED, CLOSED = "unused", "claimed", "closed"
# 0062 L0006 1.2. An open key in the database (DB0007 §2.1.2): the values are checked here. 0064 T-B4d (L0005 2.17,
# question #441 o1): a legacy mapping L6 cannot tell from a re-dispatch counts as evidence, closed as its own reason.
CLOSE_REASONS = (
    "applied", "no_candidate", "invalid_candidate", "human_edited", "prior_evidence", "superseded_start",
    "other_registration", "source_changed", "node_gone", "flow_disposed", "not_target_node", "not_target_title",
    "not_target_display", "prior_evidence_legacy",
)
_COLUMNS = ("group_id, node_key, title, title_source, human_edited, opportunity_state, close_reason, "
            "claimed_token_id, claimed_ai_run_id, claimed_at, closed_at, version, created_at, updated_at, updated_by")


def available() -> bool:
    """False until migration 155 is applied (reads then see no row, writes are refused by the service)."""
    return get_store().table_exists(TITLE_TABLE)


def states(flow_id: str) -> dict[str, dict]:
    """Q1: the Flow's title states, ``node_key -> {title, title_source, version}``. Title, source and version
    of a card come from one row of one statement, never read apart."""
    if not available():
        return {}
    rows = get_store()._fetch_all(
        "SELECT node_key, title, title_source, version FROM graph_node_title_states WHERE group_id = ?", [flow_id])
    return {r["node_key"]: {"title": r["title"], "title_source": r["title_source"], "version": int(r["version"])}
            for r in rows or []}


def state(flow_id: str, node_key: str) -> Optional[dict]:
    """Q2: the card's whole row, or None (``U``)."""
    row = get_store()._fetch_one(
        f"SELECT {_COLUMNS} FROM graph_node_title_states WHERE group_id = ? AND node_key = ?", [flow_id, node_key])
    return dict(row) if row else None


def _insert(flow_id: str, node_key: str, values: dict) -> None:
    """Q3: the first row of a card, version 1 (a second insert of the same card is a primary key violation)."""
    now = utc_now_ms()
    row = {"title": None, "title_source": SOURCE_NONE, "human_edited": 0, "opportunity_state": UNUSED,
           "close_reason": None, "claimed_token_id": None, "claimed_ai_run_id": None, "claimed_at": None,
           "closed_at": None, "updated_by": None, **values}
    get_store()._execute(
        f"INSERT INTO graph_node_title_states ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [flow_id, node_key, row["title"], row["title_source"], row["human_edited"], row["opportunity_state"],
         row["close_reason"], row["claimed_token_id"], row["claimed_ai_run_id"], row["claimed_at"],
         row["closed_at"], STATE_VERSION_START, now, now, row["updated_by"]])


def _update(flow_id: str, node_key: str, values: dict, where: str = "", params: tuple = ()) -> int:
    """Q4: one write of a card, the version raised in SQL. ``where`` narrows it (conditional writes)."""
    sets = ", ".join(f"{name} = ?" for name in values)
    return get_store()._execute_affected(
        f"UPDATE graph_node_title_states SET {sets}, version = version + 1, updated_at = ? "
        f"WHERE group_id = ? AND node_key = ?{where}",
        [*values.values(), utc_now_ms(), flow_id, node_key, *params])


def save_human(flow_id: str, node_key: str, title: str, user_id: Optional[str]) -> None:
    """0062 L0006 2.7 step 5: a person's title. The person mark goes on and stays; an opportunity still unused or
    claimed closes as ``human_edited``; a closed one keeps its reason (``X -> X``). ``current`` is the row the
    caller's guard read: the next values are decided from it, so no assignment depends on the order a database
    evaluates ``SET`` in."""
    current = state(flow_id, node_key)
    values: dict = {"title": title, "title_source": SOURCE_HUMAN, "human_edited": 1, "updated_by": user_id}
    if current is None or current["opportunity_state"] != CLOSED:
        values.update(opportunity_state=CLOSED, close_reason="human_edited", closed_at=utc_now_ms())
    if current is None:
        _insert(flow_id, node_key, values)
    else:
        _update(flow_id, node_key, values)


def _check_reason(reason: str) -> None:
    if reason not in CLOSE_REASONS:
        raise ValueError(f"unknown title opportunity close reason: {reason}")


def claim(flow_id: str, node_key: str, token_id: str, ai_run_id: Optional[str],
          expected_version: Optional[int]) -> int:
    """0062 L0006 2.4 ``claim``: ``U`` -> ``C`` (no row: insert; else only an unused row of the version read)."""
    values = {"opportunity_state": CLAIMED, "claimed_token_id": token_id, "claimed_ai_run_id": ai_run_id,
              "claimed_at": utc_now_ms()}
    if expected_version is None:
        _insert(flow_id, node_key, values)
        return 1
    return _update(flow_id, node_key, values, " AND opportunity_state = ? AND version = ?",
                   (UNUSED, expected_version))


def close(flow_id: str, node_key: str, reason: str, expected_version: Optional[int]) -> int:
    """0062 L0006 2.4 ``close``: ``U`` / ``C`` -> ``X`` with its reason; a closed row is never written again."""
    _check_reason(reason)
    values = {"opportunity_state": CLOSED, "close_reason": reason, "closed_at": utc_now_ms()}
    if expected_version is None:
        _insert(flow_id, node_key, values)
        return 1
    return _update(flow_id, node_key, values, " AND opportunity_state <> ? AND version = ?",
                   (CLOSED, expected_version))


def apply_ai(flow_id: str, node_key: str, title: str, token_id: str, expected_version: int) -> int:
    """0062 L0006 2.6 step 8: the claiming token's title, only while the row is still claimed by it, no person
    saved one and nothing was written since the version read."""
    values = {"title": title, "title_source": SOURCE_AI, "opportunity_state": CLOSED, "close_reason": "applied",
              "closed_at": utc_now_ms()}
    return _update(flow_id, node_key, values,
                   " AND opportunity_state = ? AND claimed_token_id = ? AND human_edited = 0 AND version = ?",
                   (CLAIMED, token_id, expected_version))
