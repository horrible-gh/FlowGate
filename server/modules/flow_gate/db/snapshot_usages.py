"""Durable records of actual AI snapshot access and execution."""
from __future__ import annotations

import json
import uuid

from .connection import get_store, now_iso


def _decode(row):
    if row is None:
        return None
    result = dict(row)
    for key in ("success", "stale_at_use", "current_worktree_claim"):
        result[key] = bool(result.get(key))
    raw = result.get("detail")
    if isinstance(raw, str):
        try:
            result["detail"] = json.loads(raw)
        except (TypeError, ValueError):
            pass
    return result


def record(data: dict) -> dict:
    usage_id = str(data.get("usage_id") or ("suse_" + uuid.uuid4().hex))
    used_at = str(data.get("used_at") or now_iso())
    detail = data.get("detail")
    if detail is not None and not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False, sort_keys=True)
    get_store()._execute(
        "INSERT INTO snapshot_usages "
        "(usage_id,snapshot_id,run_id,token_id,access_kind,operation,task_kind,"
        "success,stale_at_use,current_worktree_claim,detail,used_at,document_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            usage_id, data["snapshot_id"], data["run_id"], data["token_id"],
            data["access_kind"], data["operation"], data.get("task_kind"),
            bool(data.get("success", True)), bool(data.get("stale_at_use")),
            bool(data.get("current_worktree_claim")), detail, used_at,
            data.get("document_id"),
        ],
    )
    return _decode(get_store()._fetch_one(
        "SELECT * FROM snapshot_usages WHERE usage_id=?", [usage_id]
    ))


def list_for_run(run_id: str) -> list[dict]:
    return [
        _decode(row) for row in get_store()._fetch_all(
            "SELECT * FROM snapshot_usages WHERE run_id=? ORDER BY used_at ASC, usage_id ASC",
            [run_id],
        )
    ]


def list_for_document(document_id: str) -> list[dict]:
    return [
        _decode(row) for row in get_store()._fetch_all(
            "SELECT * FROM snapshot_usages WHERE document_id=? ORDER BY used_at ASC, usage_id ASC",
            [document_id],
        )
    ]


def attach_document(run_id: str, document_id: str) -> int:
    return get_store()._execute_affected(
        "UPDATE snapshot_usages SET document_id=? "
        "WHERE run_id=? AND document_id IS NULL",
        [document_id, run_id],
    )