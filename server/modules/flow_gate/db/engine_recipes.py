"""engine_recipes CRUD — sqloader.load pattern (flowgate.default.0157 DB0005).

Inline SQL is prohibited; only use SQL registered in queries.json (`engine_recipes` namespace).
Mirrors project_test_commands.py (0152) but at GLOBAL scope — there is no project column; identity is
the normalized engine string alone (UNIQUE(engine), migration 057).

Physical delete never happens (L §2-2): every row carries status 'active' | 'suppressed'. A suppressed
row is a tombstone — it keeps the (engine) slot so auto-learning skips it, and a manual re-add of the
same engine revives the same row (id preserved). get_by_id / find_by_engine return rows of either
status so revive/patch/delete can read a suppressed row; get_active_by_engine / list_active are the
active-only help paths.
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store, now_iso


def list_active() -> list[dict]:
    """Active rows only, engine ASC, capped (Q1 / DB §4). Global — no project filter."""
    store = get_store()
    return store._fetch_all(store._sql("engine_recipes.list_active"), [])


def count_active() -> int:
    store = get_store()
    row = store._fetch_one(store._sql("engine_recipes.count_active"), [])
    return int(row["cnt"]) if row and row.get("cnt") is not None else 0


def get_by_id(recipe_id: int) -> Optional[dict]:
    """Single row by id, any status, or None."""
    store = get_store()
    return store._fetch_one(store._sql("engine_recipes.get_by_id"), [recipe_id])


def get_active_by_engine(engine: str) -> Optional[dict]:
    """Active single row by normalized engine (Q2), or None."""
    store = get_store()
    return store._fetch_one(store._sql("engine_recipes.get_active_by_engine"), [engine])


def find_by_engine(engine: str) -> Optional[dict]:
    """Look up a row by normalized engine, INCLUDING suppressed rows (identity/revive check, Q3)."""
    store = get_store()
    return store._fetch_one(store._sql("engine_recipes.find_by_engine"), [engine])


def insert(
    engine: str,
    label: str,
    setup: str,
    run_example: str,
    notes: str,
    origin: str,
    updated_by: str,
    status: str = "active",
    last_success_run_id: Optional[str] = None,
    last_success_at: Optional[str] = None,
) -> dict:
    """Insert a new row and return it (INSERT + last_insert_rowid() share one connection)."""
    store = get_store()
    now = now_iso()
    with store.transaction() as s:
        s._execute(
            store._sql("engine_recipes.insert"),
            [
                engine, label, setup, run_example, notes, origin, status,
                last_success_run_id, last_success_at, updated_by, now, now,
            ],
        )
        row = s._fetch_one("SELECT last_insert_rowid() AS rid")
        new_id = row["rid"] if row else None
        return s._fetch_one(store._sql("engine_recipes.get_by_id"), [new_id])


def update_row(recipe_id: int, updates: dict) -> Optional[dict]:
    """Read-modify-write the mutable column set; return the updated row, or None if absent.

    `updates` may carry any subset of
    {label, setup, run_example, notes, origin, status, last_success_run_id, last_success_at,
    updated_by}; unspecified fields keep their value. `engine` is immutable and never written here.
    """
    store = get_store()
    current = get_by_id(recipe_id)
    if current is None:
        return None
    label = updates.get("label", current["label"])
    setup = updates.get("setup", current["setup"])
    run_example = updates.get("run_example", current["run_example"])
    notes = updates.get("notes", current["notes"])
    origin = updates.get("origin", current["origin"])
    status = updates.get("status", current["status"])
    last_success_run_id = updates.get("last_success_run_id", current["last_success_run_id"])
    last_success_at = updates.get("last_success_at", current["last_success_at"])
    updated_by = updates.get("updated_by", current["updated_by"])
    store._execute(
        store._sql("engine_recipes.update_row"),
        [
            label, setup, run_example, notes, origin, status,
            last_success_run_id, last_success_at, updated_by, now_iso(), recipe_id,
        ],
    )
    return get_by_id(recipe_id)
