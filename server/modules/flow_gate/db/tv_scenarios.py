"""CRUD for TV scenarios, status, clear scope, and chains (migrated from store.py, phase 'TV').

Ports store.FlowGateStore TV methods with identical SQL and return shapes (highest-risk area, verbatim).
- tv_scenarios table: scenario CRUD
- documents.meta(JSON): tv_status / tv_clear_scope
- documents queries: active/running/previous TV and TV/TVR chains
"""
from __future__ import annotations
import json
from datetime import datetime
from typing import Optional, Any
from .connection import get_store
from . import documents as _docs


def _dump_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


# ── tv_scenarios table ─────────────────────────────────────────────────────────

def get_tv_scenarios(tv_doc_id: str) -> list[dict]:
    """Return scenarios for a TV document ordered by scenario_idx ascending."""
    return get_store()._fetch_all(
        "SELECT * FROM tv_scenarios WHERE tv_doc_id = ? ORDER BY scenario_idx ASC",
        [tv_doc_id],
    )


def insert_tv_scenarios_bulk(tv_doc_id: str, titles: list, source: str = "worker") -> None:
    """Insert TV scenarios in bulk."""
    if not titles:
        return
    now = datetime.now().isoformat()
    store = get_store()
    with store.transaction() as s:
        row = s._fetch_one(
            "SELECT COALESCE(MAX(scenario_idx), 0) AS max_idx FROM tv_scenarios WHERE tv_doc_id = ?",
            [tv_doc_id],
        )
        start_idx = (row["max_idx"] if row else 0) + 1
        for i, title in enumerate(titles):
            s._execute(
                "INSERT INTO tv_scenarios"
                " (tv_doc_id, scenario_idx, source, title, updated_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT DO NOTHING",
                [tv_doc_id, start_idx + i, source, title, now],
            )


def append_tv_scenario(tv_doc_id: str, title: str, source: str = "user") -> int:
    """Append one TV scenario and return its new scenario_idx."""
    now = datetime.now().isoformat()
    store = get_store()
    with store.transaction() as s:
        row = s._fetch_one(
            "SELECT COALESCE(MAX(scenario_idx), 0) AS max_idx FROM tv_scenarios WHERE tv_doc_id = ?",
            [tv_doc_id],
        )
        new_idx = (row["max_idx"] if row else 0) + 1
        s._execute(
            "INSERT INTO tv_scenarios"
            " (tv_doc_id, scenario_idx, source, title, updated_at) VALUES (?, ?, ?, ?, ?)",
            [tv_doc_id, new_idx, source, title, now],
        )
    return new_idx


def update_tv_scenario_result(tv_doc_id: str, scenario_idx: int, result: str,
                              note: Optional[str] = None) -> bool:
    """Record a scenario result (pass/fail/skip/hold)."""
    now = datetime.now().isoformat()
    get_store()._execute(
        "UPDATE tv_scenarios SET result = ?, note = ?, updated_at = ?"
        " WHERE tv_doc_id = ? AND scenario_idx = ?",
        [result, note, now, tv_doc_id, scenario_idx],
    )
    return True


def update_tv_scenario_hold_to_skip(tv_doc_id: str, scenario_idx: int, reason: str) -> bool:
    """Change a held scenario to skipped."""
    now = datetime.now().isoformat()
    get_store()._execute(
        "UPDATE tv_scenarios SET result = 'skip', note = ?, updated_at = ?"
        " WHERE tv_doc_id = ? AND scenario_idx = ? AND result = 'hold'",
        [reason, now, tv_doc_id, scenario_idx],
    )
    return True


def disable_tv_scenario(tv_doc_id: str, scenario_idx: int, reason: str) -> bool:
    """Disable a scenario."""
    now = datetime.now().isoformat()
    get_store()._execute(
        "UPDATE tv_scenarios SET disabled = 1, disabled_reason = ?, updated_at = ?"
        " WHERE tv_doc_id = ? AND scenario_idx = ?",
        [reason, now, tv_doc_id, scenario_idx],
    )
    return True


# ── tv_status (documents.status + meta) ─────────────────────────────────────────

def insert_tv_status(tv_doc_id: str, tv_status: str = "Open",
                     progress_done: int = 0, progress_total: int = 0) -> bool:
    """Store TV status in documents.status and meta."""
    status_lower = tv_status.lower() if tv_status else "open"
    now = datetime.now().isoformat()
    meta = {"progress_done": progress_done, "progress_total": progress_total}
    get_store()._execute(
        "UPDATE documents SET status = ?, meta = ?, updated_at = ?"
        " WHERE doc_id = ? AND type_code = 'TV'",
        [status_lower, _dump_json(meta), now, tv_doc_id],
    )
    return True


def _meta_dict(raw) -> dict:
    """Parse a documents.meta value (TEXT JSON or dict) into a dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def get_tv_status(tv_doc_id: str) -> Optional[dict]:
    """Read TV status from documents."""
    # meta is TEXT JSON; extract in Python rather than via SQLite-only json_extract (0088).
    row = get_store()._fetch_one(
        "SELECT doc_id as tv_doc_id, status as tv_status, meta"
        " FROM documents WHERE doc_id = ? AND type_code = 'TV'",
        [tv_doc_id],
    )
    if not row:
        return None
    meta = _meta_dict(row.pop("meta", None))
    row["progress_done"] = meta.get("progress_done", 0)
    row["progress_total"] = meta.get("progress_total", 0)
    return row


def update_tv_status(tv_doc_id: str, tv_status: str, progress_done: Optional[int] = None,
                     progress_total: Optional[int] = None) -> bool:
    """Update TV status, leaving progress fields unchanged when they are None."""
    now = datetime.now().isoformat()
    status_lower = tv_status.lower() if tv_status else "open"
    store = get_store()
    if progress_done is not None or progress_total is not None:
        doc = _docs.get_document_by_id(tv_doc_id)
        if not doc:
            return False
        meta = doc.get("meta") or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        elif not isinstance(meta, dict):
            meta = {}
        if progress_done is not None:
            meta["progress_done"] = progress_done
        if progress_total is not None:
            meta["progress_total"] = progress_total
        store._execute(
            "UPDATE documents SET status = ?, meta = ?, updated_at = ?"
            " WHERE doc_id = ? AND type_code = 'TV'",
            [status_lower, _dump_json(meta), now, tv_doc_id],
        )
        return True
    store._execute(
        "UPDATE documents SET status = ?, updated_at = ?"
        " WHERE doc_id = ? AND type_code = 'TV'",
        [status_lower, now, tv_doc_id],
    )
    return True


# ── tv_clear_scope (documents.meta) ─────────────────────────────────────────────

def insert_tv_clear_scope(tv_doc_id: str, clear_db: int = 1, clear_fs: int = 1,
                          clear_cache: int = 0, clear_logs: int = 0) -> bool:
    """Store the TV clear scope in documents.meta."""
    now = datetime.now().isoformat()
    doc = _docs.get_document_by_id(tv_doc_id)
    if not doc:
        return False
    meta = doc.get("meta") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    elif not isinstance(meta, dict):
        meta = {}
    meta["tv_clear_scope"] = {
        "clear_db": clear_db, "clear_fs": clear_fs,
        "clear_cache": clear_cache, "clear_logs": clear_logs,
    }
    get_store()._execute(
        "UPDATE documents SET meta = ?, updated_at = ?"
        " WHERE doc_id = ? AND type_code = 'TV'",
        [_dump_json(meta), now, tv_doc_id],
    )
    return True


def get_tv_clear_scope(tv_doc_id: str) -> Optional[dict]:
    """Read the TV clear scope from documents.meta."""
    # meta is TEXT JSON; extract in Python rather than via SQLite-only json_extract (0088).
    row = get_store()._fetch_one(
        "SELECT meta FROM documents WHERE doc_id = ? AND type_code = 'TV'",
        [tv_doc_id],
    )
    if not row:
        return None
    scope = _meta_dict(row.get("meta")).get("tv_clear_scope")
    if scope:
        return {"tv_doc_id": tv_doc_id, **scope}
    return None


def update_tv_clear_scope(tv_doc_id: str, clear_db: Optional[int] = None,
                          clear_fs: Optional[int] = None, clear_cache: Optional[int] = None,
                          clear_logs: Optional[int] = None) -> bool:
    """Partially update the TV clear scope."""
    if all(v is None for v in [clear_db, clear_fs, clear_cache, clear_logs]):
        return True
    now = datetime.now().isoformat()
    doc = _docs.get_document_by_id(tv_doc_id)
    if not doc:
        return False
    meta = doc.get("meta") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    elif not isinstance(meta, dict):
        meta = {}
    if "tv_clear_scope" not in meta:
        meta["tv_clear_scope"] = {"clear_db": 1, "clear_fs": 1, "clear_cache": 0, "clear_logs": 0}
    if clear_db is not None:
        meta["tv_clear_scope"]["clear_db"] = clear_db
    if clear_fs is not None:
        meta["tv_clear_scope"]["clear_fs"] = clear_fs
    if clear_cache is not None:
        meta["tv_clear_scope"]["clear_cache"] = clear_cache
    if clear_logs is not None:
        meta["tv_clear_scope"]["clear_logs"] = clear_logs
    get_store()._execute(
        "UPDATE documents SET meta = ?, updated_at = ?"
        " WHERE doc_id = ? AND type_code = 'TV'",
        [_dump_json(meta), now, tv_doc_id],
    )
    return True


# ── TV/TVR chain queries (documents) ───────────────────────────────────────────

def get_active_tv_for_t(t_doc_id: str) -> Optional[dict]:
    """Return one active TV (open/running/pass/fail/reject) for the same T."""
    return get_store()._fetch_one(
        "SELECT d.* FROM documents d WHERE d.target_id = ? AND d.type_code = 'TV'"
        " AND d.status IN ('open', 'running', 'pass', 'fail', 'reject')"
        " ORDER BY d.id DESC LIMIT 1",
        [t_doc_id],
    )


def get_active_tvs_by_statuses(statuses: list) -> list[dict]:
    """Return TV documents in the specified statuses."""
    if not statuses:
        return []
    placeholders = ",".join(["?"] * len(statuses))
    return get_store()._fetch_all(
        f"SELECT d.* FROM documents d WHERE d.type_code = 'TV'"
        f" AND d.status IN ({placeholders}) ORDER BY d.id DESC",
        list(statuses),
    )


def get_tv_tvr_chain(t_doc_id: str) -> dict:
    """Return TV/TVR records linked to a T: {'tv': [...], 'tvr': [...]}."""
    store = get_store()
    tvs = store._fetch_all(
        "SELECT d.* FROM documents d WHERE d.target_id = ? AND d.type_code = 'TV' ORDER BY d.id ASC",
        [t_doc_id],
    )
    tvrs: list[dict] = []
    for tv in tvs:
        tvr_rows = store._fetch_all(
            "SELECT * FROM documents WHERE target_id = ? AND type_code = 'TVR' ORDER BY id ASC",
            [tv["doc_id"]],
        )
        tvrs.extend(tvr_rows)
    return {"tv": tvs, "tvr": tvrs}


def get_running_tv_in_env(project: str, module: Optional[str] = None,
                          env: Optional[str] = None) -> Optional[dict]:
    """Return the running TV for an environment."""
    return get_store()._fetch_one(
        "SELECT d.* FROM documents d WHERE d.project_id = ? AND d.module = ?"
        " AND d.type_code = 'TV' AND d.status IN ('open', 'running')"
        " ORDER BY d.id DESC LIMIT 1",
        [project, module],
    )


def get_previous_active_tv(target_id: str, exclude_tv_doc_id: str) -> Optional[dict]:
    """Return the previous active TV, excluding the current TV."""
    return get_store()._fetch_one(
        "SELECT d.* FROM documents d WHERE d.type_code = 'TV' AND d.target_id = ?"
        " AND d.doc_id != ? AND (d.superseded_by IS NULL OR d.superseded_by = '')"
        " ORDER BY d.id DESC LIMIT 1",
        [target_id, exclude_tv_doc_id],
    )
