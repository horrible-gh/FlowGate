"""v0.1 records a run reads back (L0009 §2.6.8 reconciliation). Never writes v0.1 tables.

- the documents registered with a node's work tokens: the inbox consumes the token after the
  document is stored and records ``workflow_events.token_consumed`` with ``{token_id, doc_id}``
  (``token_service.consume``) — the only place a document and its token meet in v0.1 data.
- the Flow's documents keyed by id, for supersede chains and review states.
"""
from __future__ import annotations

import json
from typing import Iterable, Optional

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db.connection import get_store


def registered_documents(flow_id: str, token_ids: Iterable[str]) -> list[dict]:
    """Documents registered with any of ``token_ids``, in registration order (event id, doc_id)."""
    found: list[tuple[int, str]] = []
    for token_id in dict.fromkeys(t for t in token_ids if t):
        rows = get_store()._fetch_all(
            "SELECT id, metadata FROM workflow_events WHERE group_id = ? AND event_type = 'token_consumed' "
            "AND metadata LIKE ?",
            [flow_id, f'%"token_id":"{token_id}"%'],
        )
        for row in rows or []:
            try:
                meta = json.loads(row.get("metadata") or "{}")
            except (TypeError, ValueError):
                continue
            if meta.get("token_id") == token_id and meta.get("doc_id"):
                found.append((int(row.get("id") or 0), str(meta["doc_id"])))
    out: list[dict] = []
    for _id, doc_id in sorted(found):
        doc = db_documents.get_by_id(doc_id)
        if doc is not None and all(d["doc_id"] != doc_id for d in out):
            out.append(doc)
    return out


def flow_documents(flow_id: str) -> dict[str, dict]:
    """Every document of the Flow, any status, by doc_id."""
    return {d["doc_id"]: d for d in db_documents.get_documents_by_group_id(flow_id) or [] if d.get("doc_id")}


def document(doc_id: Optional[str]) -> Optional[dict]:
    return db_documents.get_by_id(doc_id) if doc_id else None
