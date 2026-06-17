"""Workflow result registration CRUD — workflow_item_results table.

Follows the sqloader.load usage pattern (only use SQL registered in queries.json).
Do not add new inline SQL.
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store


def insert_result(
    item_id: int,
    registered_path: str,
    registered_doc_id: str,
    registered_at: str,
) -> None:
    """Insert a result registration record (status = pending_approval)."""
    store = get_store()
    sql = store._sql("workflow_item_results.insert_result")
    store._execute(sql, [item_id, registered_path, registered_doc_id, registered_at])


def get_latest_result_by_item(item_id: int) -> Optional[dict]:
    """Return the latest result record for the item ID (regardless of status)."""
    store = get_store()
    sql = store._sql("workflow_item_results.get_latest_result_by_item")
    return store._fetch_one(sql, [item_id])


def get_pending_result_by_item(item_id: int) -> Optional[dict]:
    """Return the pending_approval result record for the item ID."""
    store = get_store()
    sql = store._sql("workflow_item_results.get_pending_result_by_item")
    return store._fetch_one(sql, [item_id])


def update_result_status(
    result_id: int,
    status: str,
    reviewed_at: str,
    reviewed_by: str,
) -> None:
    """Update the result record status (approved / rejected)."""
    store = get_store()
    sql = store._sql("workflow_item_results.update_result_status")
    store._execute(sql, [status, reviewed_at, reviewed_by, result_id])
