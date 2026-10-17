"""Live workflow roots and the succession marks (0064 L0005 2.9 / 2.10, DB0007 §2.3 / §4 Q8).

A Flow's live root is a document of type R/B that is not cancelled and has no ``superseded_by``. A root
succeeded by an R↔B succession conversion keeps its row (history, readable) but is never a live root
again; its successor carries ``meta.converted_from`` and the original ``meta.converted_to``, each
``{doc_id, mode: "succession", at, by}``.

The marks live inside the ``documents.meta`` TEXT JSON. They are always parsed here, in the application:
a missing key, another ``mode`` or a broken JSON is "no mark" (never an error), and no dialect JSON SQL is
used (DB0007 §4 Q8). ``superseded_by`` alone is not a succession: a card line replacement fills it too.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional

ROOT_TYPES = frozenset({"R", "B"})
SUCCESSION = "succession"
CANCELLED = "cancelled"


def meta_dict(meta: Any) -> dict:
    """``documents.meta`` as a dict; anything that is not a JSON object is ``{}``."""
    if isinstance(meta, dict):
        return meta
    if not isinstance(meta, str) or not meta.strip():
        return {}
    try:
        value = json.loads(meta)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def conversion_mark(doc: Optional[dict], key: str) -> Optional[dict]:
    """``meta.converted_from`` / ``meta.converted_to`` of a succession, or None (no mark, another mode)."""
    mark = meta_dict((doc or {}).get("meta")).get(key)
    if isinstance(mark, dict) and mark.get("mode") == SUCCESSION:
        return dict(mark)
    return None


def is_root_type(doc: Optional[dict]) -> bool:
    return str((doc or {}).get("type_code") or "").upper() in ROOT_TYPES


def is_live_root(doc: Optional[dict]) -> bool:
    """R/B, not cancelled, not superseded (L0005 2.10)."""
    return (is_root_type(doc) and (doc or {}).get("status") != CANCELLED
            and not str((doc or {}).get("superseded_by") or "").strip())


def live_roots(docs: Iterable[dict]) -> list[dict]:
    return [d for d in docs or () if is_live_root(d)]


def is_succeeded_root(doc: Optional[dict]) -> bool:
    """A root whose live successor is another document (it has ``superseded_by``)."""
    return is_root_type(doc) and bool(str((doc or {}).get("superseded_by") or "").strip())


def live_end_doc_id(doc: Optional[dict], docs_by_id: Optional[dict] = None) -> Optional[str]:
    """The live document at the end of ``doc``'s ``superseded_by`` chain (itself when it is live).

    ``docs_by_id``: the Flow's documents by doc_id; read from the DB when omitted. A cycle, a cancelled
    link, another Flow or a missing link ends with None (``supersede.live_end``)."""
    from modules.flow_gate.graph_workflow.domain.supersede import live_end

    if not doc:
        return None
    flow_id = doc.get("group_id")
    if not flow_id:
        return doc.get("doc_id") if is_live_root(doc) else None
    if docs_by_id is None:
        from modules.flow_gate.db import documents as db_docs

        docs_by_id = {d["doc_id"]: d for d in db_docs.get_documents_by_group_id(flow_id) or [] if d.get("doc_id")}
    end, _hops = live_end(flow_id, doc, docs_by_id)
    return (end or {}).get("doc_id")


def in_succession_chain(doc: Optional[dict]) -> bool:
    """DB0007 §4 Q8: the document was made by a succession, or a predecessor handed over to it by one.

    ① its own ``meta.converted_from.mode == "succession"`` (no query), ② else every predecessor candidate
    ``SELECT doc_id, meta FROM documents WHERE superseded_by = ?`` whose ``meta.converted_to`` names it with
    ``mode == "succession"``."""
    if not doc or not doc.get("doc_id"):
        return False
    if conversion_mark(doc, "converted_from") is not None:
        return True
    from modules.flow_gate.db.connection import get_store

    rows = get_store()._fetch_all("SELECT doc_id, meta FROM documents WHERE superseded_by = ?", [doc["doc_id"]])
    return any(conversion_mark(row, "converted_to") is not None for row in rows or [])
