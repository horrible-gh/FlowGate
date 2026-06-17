"""Numbering service — reserve and concurrency control.

Uses the id_counter table to reserve group, subgroup, and document sequences atomically.
In SQLite environments, process-local concurrency is controlled with threading.Lock.
"""
from __future__ import annotations

import threading
from typing import Optional

from ..db.connection import get_store, now_iso
from ..db import projects as db_projects
from ..db import groups as db_groups
from .id_formatter import (
    format_group_code,
    format_subgroup_code,
    format_doc_code,
)

# Project-specific advisory lock (in-process)
_locks: dict[str, threading.Lock] = {}
_locks_meta = threading.Lock()


def _get_lock(project_id: str) -> threading.Lock:
    with _locks_meta:
        if project_id not in _locks:
            _locks[project_id] = threading.Lock()
        return _locks[project_id]


# ── Load configuration ───────────────────────────────────────────────────────

def _get_widths(project_id: str) -> dict[str, int]:
    """Load digit widths from the project settings. Use defaults if missing."""
    ps = db_projects.get_settings(project_id)
    if ps:
        return {
            "group": ps.get("digits_group", 4) or 4,
            "subgroup": ps.get("digits_sub_group", 3) or 3,
            "document": ps.get("digits_type", 4) or 4,
        }
    return {"group": 4, "subgroup": 3, "document": 4}


# type_code sentinel for the group-shared document counter (prevents key collisions with the subgroup counter)
_DOC_COUNTER_TYPE = "__doc__"


# ── Internal: atomic id_counter increment ────────────────────────────────────

def _reserve_seq(
    project_id: str,
    module: str,
    group_seq: str,
    sub_group_seq: str,
    series: str,
    type_code: str,
    max_retry: int = 5,
    _seed: int = 0,
) -> int:
    """Atomically reserve and return the next seq from the id_counter table.

    _seed: Initial last_seq value when the counter row does not exist (starts at 1 when 0).
    """
    store = get_store()
    key = (project_id, module, group_seq, sub_group_seq, series, type_code)

    for attempt in range(max_retry):
        row = store._fetch_one(
            "SELECT last_seq FROM id_counter "
            "WHERE project_id=? AND module=? AND group_seq=? "
            "AND sub_group_seq=? AND series=? AND type_code=?",
            list(key),
        )
        if row is None:
            # Insert a new row (start from the seed value when _seed > 0)
            init_seq = _seed + 1
            try:
                store._execute(
                    "INSERT INTO id_counter "
                    "(project_id, module, group_seq, sub_group_seq, series, type_code, "
                    "last_seq, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    [*key, init_seq, now_iso()],
                )
                return init_seq
            except Exception:
                # UNIQUE conflict -> retry
                if attempt == max_retry - 1:
                    raise
                continue
        else:
            next_seq = row["last_seq"] + 1
            # CAS update
            store._execute(
                "UPDATE id_counter SET last_seq=?, updated_at=? "
                "WHERE project_id=? AND module=? AND group_seq=? "
                "AND sub_group_seq=? AND series=? AND type_code=? AND last_seq=?",
                [next_seq, now_iso(), *key, row["last_seq"]],
            )
            # Verify that the update was applied
            verify = store._fetch_one(
                "SELECT last_seq FROM id_counter "
                "WHERE project_id=? AND module=? AND group_seq=? "
                "AND sub_group_seq=? AND series=? AND type_code=?",
                list(key),
            )
            if verify and verify["last_seq"] == next_seq:
                return next_seq
            # Another thread incremented first -> retry
            if attempt == max_retry - 1:
                raise RuntimeError(
                    f"Numbering collision: {key} — failed after {max_retry} retries"
                )

    raise RuntimeError(f"Numbering failed: {key}")


# ── Internal: initialize the group-shared document counter ───────────────────

def _ensure_group_doc_counter(
    project_id: str,
    module: str,
    group_id: str,
    sg_key: str,
) -> None:
    """Create the group-shared document counter if missing, seeded from the current maximum documents.seq.

    Do nothing if a shared counter row with type_code=_DOC_COUNTER_TYPE already exists.
    Otherwise, insert it with the current group's MAX(seq) as the seed — the next seq becomes max+1.
    """
    store = get_store()
    shared_key = (project_id, module, group_id, sg_key, "", _DOC_COUNTER_TYPE)

    existing = store._fetch_one(
        "SELECT last_seq FROM id_counter "
        "WHERE project_id=? AND module=? AND group_seq=? "
        "AND sub_group_seq=? AND series=? AND type_code=?",
        list(shared_key),
    )
    if existing is not None:
        return  # Shared counter row already exists

    # Use the max(seq) from existing documents as the seed (or 0 if none exist)
    max_row = store._fetch_one(
        "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM documents WHERE group_id=?",
        [group_id],
    )
    seed = max_row["max_seq"] if max_row else 0

    try:
        store._execute(
            "INSERT INTO id_counter "
            "(project_id, module, group_seq, sub_group_seq, series, type_code, "
            "last_seq, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            [*shared_key, seed, now_iso()],
        )
    except Exception:
        # Ignore concurrent INSERTs — _reserve_seq handles them correctly
        pass


# ── Public API ────────────────────────────────────────────────────────────────

def reserve_group(
    project_id: str,
    module: str = "none",
) -> str:
    """Reserve and return a new group code. Example: '0001'"""
    widths = _get_widths(project_id)
    with _get_lock(project_id):
        seq = _reserve_seq(project_id, module, "", "", "", "")
    return format_group_code(seq, widths["group"])


def reserve_subgroup(
    group_id: str,
    module: str = "none",
) -> str:
    """Reserve and return a new subgroup code. Example: '001'

    Look up the parent project using group_id.
    """
    grp = db_groups.get_by_id(group_id)
    if grp is None:
        raise ValueError(f"Group not found: {group_id!r}")
    project_id = grp["project_id"]
    widths = _get_widths(project_id)

    # Use group_id itself as the group_seq key
    with _get_lock(project_id):
        seq = _reserve_seq(project_id, module, group_id, "", "", "")
    return format_subgroup_code(seq, widths["subgroup"])


def reserve_document(
    group_id: str,
    doc_type: str,
    sub_group_id: Optional[str] = None,
    module: str = "none",
) -> str:
    """Reserve and return a new document code. Example: '0001-R'

    Parameters
    ----------
    group_id : str
        Parent group ID.
    doc_type : str
        Document type code (for example, 'R', 'DS', 'T').
    sub_group_id : str | None
        Parent subgroup ID (uses an empty string if missing).
    module : str
        Module identifier.
    """
    grp = db_groups.get_by_id(group_id)
    if grp is None:
        raise ValueError(f"Group not found: {group_id!r}")
    project_id = grp["project_id"]
    widths = _get_widths(project_id)

    sg_key = sub_group_id or ""
    with _get_lock(project_id):
        _ensure_group_doc_counter(project_id, module, group_id, sg_key)
        seq = _reserve_seq(project_id, module, group_id, sg_key, "", _DOC_COUNTER_TYPE)
    return format_doc_code(doc_type, seq, widths["document"])


def peek_document_code(
    group_id: str,
    doc_type: str,
    module: str = "none",
) -> str:
    """Return the next document code WITHOUT consuming the shared counter.

    Derived from the current MAX(documents.seq) of the group (+1). Intended for
    ephemeral, always-terminal, file-less docs (e.g. the AC final-approval doc)
    that are deleted and recreated across time-machine reopen cycles: routing
    them through ``reserve_document`` would burn a permanent sequence number on
    every cycle, leaving gaps (0004 → 0006). Because such docs are always the
    last in workflow order, MAX(seq)+1 is stable and collision-free.
    """
    grp = db_groups.get_by_id(group_id)
    if grp is None:
        raise ValueError(f"Group not found: {group_id!r}")
    project_id = grp["project_id"]
    widths = _get_widths(project_id)
    store = get_store()
    row = store._fetch_one(
        "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM documents WHERE group_id=?",
        [group_id],
    )
    seq = (row["max_seq"] if row else 0) + 1
    return format_doc_code(doc_type, seq, widths["document"])
