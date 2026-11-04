"""Card completion store (flowgate.v02.0119 T#2, T-1).

Migration 169_graph_card_completions.sql. One row per completion of a node attempt's result: the only source of "this
node is done" in a Graph run. The row is never deleted; its ``status`` tells what became of it:

    recorded    written (step ①), not yet the node's done
    applied     the node went done with it (step ②, in the same transaction as the done)
    superseded  an older verdict: the node's result / attempt / revision had moved, or the node was not in review any
                more — nothing was applied
    revoked     a Time Machine rewind took the node back after it; kept as history, never applied again

The idempotency key is ``(run_id, node_key, attempt_id, result_doc_id)``: it holds one live row, and a second writer of
the same key reads that row back (``insert``). ``generation`` numbers the key's rows (unique with the key): the key's
next row is written only by ``completion.record`` when its latest row was superseded by a newer revision of the same
result in the same attempt (a person's resubmission) — the earlier rows stay as history. ``attempt_id`` is '' for a
node run opened before migration 151.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Iterable, Optional

from modules.flow_gate.db.connection import get_store

TABLE = "graph_card_completions"
META_TABLE = "graph_card_completion_meta"
INSTALLED_AT = "installed_at"
ID_PREFIX = "cmp_"

RECORDED, APPLIED, SUPERSEDED, REVOKED = "recorded", "applied", "superseded", "revoked"
LIVE = (RECORDED, APPLIED)
TERMINAL = (SUPERSEDED, REVOKED)
STATUSES = (RECORDED, APPLIED, SUPERSEDED, REVOKED)

_COLUMNS = ("completion_id, run_id, group_id, node_key, attempt_id, result_doc_id, generation, revision_no, cause, "
            "actor_kind, actor_id, requested_by, verdict_id, status, status_reason, created_at, applied_at, "
            "applied_event_seq, status_changed_at")


def now() -> str:
    """ISO 8601 UTC with milliseconds, like the Graph run rows (``layout_store.utc_now_ms``)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def available() -> bool:
    """False until migration 169 is applied."""
    return get_store().table_exists(TABLE)


def attempt_key(attempt_id: Optional[str]) -> str:
    return str(attempt_id or "")


def _one(sql: str, params: list) -> Optional[dict]:
    row = get_store()._fetch_one(sql, params)
    return _with_decision(dict(row)) if row else None


def _all(sql: str, params: list) -> list[dict]:
    return [_with_decision(dict(r)) for r in get_store()._fetch_all(sql, params) or []]


def _with_decision(row):
    if row.get("cause") == "human_approved" and get_store().table_exists("graph_card_completion_decisions"):
        evidence = get_store()._fetch_one(
            "SELECT decision_id FROM graph_card_completion_decisions WHERE completion_id = ?", [row["completion_id"]])
        row["decision_id"] = evidence["decision_id"] if evidence else None
    return row


def by_id(completion_id: Optional[str]) -> Optional[dict]:
    if not completion_id or not available():
        return None
    return _one(f"SELECT {_COLUMNS} FROM {TABLE} WHERE completion_id = ?", [completion_id])


def by_key(run_id: str, node_key: str, attempt_id: Optional[str], result_doc_id: str) -> Optional[dict]:
    """The key's latest row (its highest generation), or None."""
    if not available():
        return None
    return _one(f"SELECT {_COLUMNS} FROM {TABLE} WHERE run_id = ? AND node_key = ? AND attempt_id = ? "
                f"AND result_doc_id = ? ORDER BY generation DESC LIMIT 1",
                [run_id, node_key, attempt_key(attempt_id), result_doc_id])


def live(run_id: str, node_key: str, attempt_id: Optional[str], result_doc_id: str) -> Optional[dict]:
    found = by_key(run_id, node_key, attempt_id, result_doc_id)
    return found if found is not None and found["status"] in LIVE else None


def of_node(run_id: str, node_key: str) -> list[dict]:
    """Every completion of the node in the run, oldest first."""
    if not available():
        return []
    return _all(f"SELECT {_COLUMNS} FROM {TABLE} WHERE run_id = ? AND node_key = ? "
                f"ORDER BY created_at, generation, completion_id", [run_id, node_key])


def of_run(run_id: str) -> list[dict]:
    if not available():
        return []
    return _all(f"SELECT {_COLUMNS} FROM {TABLE} WHERE run_id = ? ORDER BY created_at, generation, completion_id",
                [run_id])


def of_document(group_id: str, result_doc_id: str) -> list[dict]:
    if not available():
        return []
    return _all(f"SELECT {_COLUMNS} FROM {TABLE} WHERE group_id = ? AND result_doc_id = ? "
                f"ORDER BY created_at, generation, completion_id", [group_id, result_doc_id])


def recorded_of_group(group_id: str) -> list[dict]:
    """The Flow's rows still ``recorded`` (written, never applied nor settled), oldest first."""
    if not available():
        return []
    return _all(f"SELECT {_COLUMNS} FROM {TABLE} WHERE group_id = ? AND status = ? "
                f"ORDER BY created_at, generation, completion_id", [group_id, RECORDED])


def has_any(run_id: str) -> bool:
    if not available():
        return False
    return _one(f"SELECT 1 AS ok FROM {TABLE} WHERE run_id = ? LIMIT 1", [run_id]) is not None


def insert(row: dict, generation: int = 0) -> tuple[dict, bool]:
    """Write the completion ``row`` (status ``recorded``) as the key's ``generation`` unless that generation exists
    already: ``(the key's latest row, created)``.

    The INSERT is isolated by a savepoint (``insert_isolated``): a unique violation — another writer of the same key and
    generation, committed first — undoes only it, and the caller's transaction goes on with the existing row."""
    store = get_store()
    completion_id = ID_PREFIX + uuid.uuid4().hex
    stamp = now()
    params = [completion_id, row["run_id"], row["group_id"], row["node_key"], attempt_key(row.get("attempt_id")),
              row["result_doc_id"], int(generation), row.get("revision_no"), row["cause"], row["actor_kind"],
              row.get("actor_id"), row.get("requested_by"), row.get("verdict_id"), RECORDED, None, stamp, None, None,
              stamp]
    marks = ", ".join("?" for _ in params)
    sql = f"INSERT INTO {TABLE} ({_COLUMNS}) VALUES ({marks})"
    with store.transaction():
        created = store.insert_isolated(sql, params, "card_completion_insert")
        if created and row.get("decision_id"):
            store._execute("INSERT INTO graph_card_completion_decisions (completion_id, decision_id) VALUES (?, ?)",
                           [completion_id, row["decision_id"]])
        found = by_key(row["run_id"], row["node_key"], row.get("attempt_id"), row["result_doc_id"])
    if found is None:
        raise AssertionError("card completion missed its row")
    return found, bool(created) and found["completion_id"] == completion_id


def set_status(completion_id: str, expected: Iterable[str], status: str, reason: Optional[str] = None,
               event_seq: Optional[int] = None) -> int:
    """Conditional status change (only from one of ``expected``); the rows changed (0 / 1)."""
    if status not in STATUSES:
        raise ValueError(status)
    olds = list(expected)
    marks = ", ".join("?" for _ in olds)
    stamp = now()
    applied = ", applied_at = ?, applied_event_seq = ?" if status == APPLIED else ""
    params: list = [status, reason, stamp]
    if status == APPLIED:
        params += [stamp, event_seq]
    params += [completion_id, *olds]
    return get_store()._execute_affected(
        f"UPDATE {TABLE} SET status = ?, status_reason = ?, status_changed_at = ?{applied} "
        f"WHERE completion_id = ? AND status IN ({marks})", params)


def revoke(run_id: str, node_keys: Iterable[str], reason: str) -> list[str]:
    """The live completions of ``node_keys`` in the run -> ``revoked`` (a Time Machine rewind); their ids."""
    keys = sorted(set(node_keys))
    if not keys or not available():
        return []
    marks = ", ".join("?" for _ in keys)
    rows = get_store()._fetch_all(
        f"SELECT completion_id FROM {TABLE} WHERE run_id = ? AND node_key IN ({marks}) AND status IN (?, ?) "
        f"ORDER BY created_at, completion_id", [run_id, *keys, *LIVE]) or []
    out = []
    for r in rows:
        if set_status(r["completion_id"], LIVE, REVOKED, reason) == 1:
            out.append(r["completion_id"])
    return out


# ── installation (the boundary between existing runs and runs of this service) ──

def installed_at() -> Optional[str]:
    if not get_store().table_exists(META_TABLE):
        return None
    row = _one(f"SELECT meta_value FROM {META_TABLE} WHERE meta_key = ?", [INSTALLED_AT])
    return str(row["meta_value"]) if row and row.get("meta_value") else None


def ensure_installed(stamp: Optional[str] = None) -> Optional[str]:
    """Write ``installed_at`` once (the first server start with the service); the stored value."""
    store = get_store()
    if not store.table_exists(META_TABLE):
        return None
    if installed_at() is None:
        with store.transaction():
            store.insert_isolated(f"INSERT INTO {META_TABLE} (meta_key, meta_value) VALUES (?, ?)",
                                  [INSTALLED_AT, stamp or now()], "card_completion_installed")
    return installed_at()
