"""Position handoff when a document is superseded (L0009 §2.3.7) and the supersede-chain test
used by rematch stage 1 (L0009 §2.3.4). Pure over a ``doc_id -> document`` map of one Flow.
"""
from __future__ import annotations

from typing import Collection, Mapping, Optional

from .canvas_mapping import CANCELLED_STATUS, is_alive
from .rematch import doc_key


def live_end(flow_id: str, doc: Optional[dict], docs: Mapping[str, dict]) -> tuple[Optional[dict], int]:
    """(first live document following ``superseded_by`` from ``doc``, hops) or (None, hops).

    Intermediate documents need not be alive. A cycle, a cancelled link, a document of another
    Flow or a missing link ends the chain with None (L0009 E-34).
    """
    seen: set[str] = set()
    cur = doc
    hops = 0
    while cur is not None:
        doc_id = cur.get("doc_id")
        if doc_id in seen:
            return None, hops
        seen.add(doc_id)
        if is_alive(cur, flow_id):
            return cur, hops
        if cur.get("status") == CANCELLED_STATUS or cur.get("group_id") != flow_id:
            return None, hops
        cur = docs.get(cur.get("superseded_by") or "")
        hops += 1
    return None, hops


def chain_reaches(old_doc_id: str, new_doc_id: str, docs: Mapping[str, dict]) -> bool:
    """True when following ``superseded_by`` from ``old_doc_id`` reaches ``new_doc_id``."""
    seen: set[str] = set()
    cur = docs.get(old_doc_id)
    while cur is not None and cur.get("doc_id") not in seen:
        seen.add(cur.get("doc_id"))
        nxt = cur.get("superseded_by")
        if not nxt:
            return False
        if nxt == new_doc_id:
            return True
        cur = docs.get(nxt)
    return False


def pending_handoffs(
    flow_id: str,
    docs: Mapping[str, dict],
    saved_keys: Collection[str],
    retired: Collection[str],
) -> list[dict]:
    """Handoffs still to apply: ``[{from_key, to_key, hops}]`` ordered by (hops, from_key)."""
    out = []
    for doc in docs.values():
        if not doc.get("superseded_by"):
            continue
        from_key = doc_key(doc["doc_id"])
        if from_key not in saved_keys or from_key in retired:
            continue
        end, hops = live_end(flow_id, doc, docs)
        if end is None:
            continue
        out.append({"from_key": from_key, "to_key": doc_key(end["doc_id"]), "hops": hops})
    out.sort(key=lambda h: (h["hops"], h["from_key"]))
    return out
