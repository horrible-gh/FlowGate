"""Card documents (flowgate.v02.0053 T#1): every card placed on the Designer has its documents.

Reached from the Graph workflow only through ``card_document_ports`` (``integration`` registers
these functions). The write paths run inside the caller's Flow guard — a draft save
(``sync_draft``), a run step (``adopt_result``), an R↔B root conversion (``follow_root_conversion``)
— so the document rows and the line rows commit with it or not at all. Documents are made and
removed only through the existing v0.1 services (``legacy_adapter``).

- Placing a card creates an empty draft for each of its lines (``slots.card_slots``). A requirement
  card shows the Flow's existing root document instead of making a second root (two live roots
  would block the root binding, L0009 §2.4.1); with no root yet its draft becomes the root.
- Removing a draft-only card deletes its own documents that are still drafts. A document that went
  further (submitted, reviewed, superseded) or that the card did not create is only unlinked and
  stays a free document node: v0.1 removes such documents only by a person's delete / cancel
  decision (``documents`` DELETE route, ``process_service.cancel_document``). Lines are kept per
  card kind: a line a confirmed revision of the node shows stays, even when the draft removes or
  retypes the node — the revision view and its runs still show it; a retyped node gets the new
  kind's lines next to it and each view shows the lines of its own kind (``slots.lines_of``).
- A save first releases every line it frees (removed cards, retyped cards) and only then fills the
  missing lines, so the root a requirement card gets never depends on the order of the request's
  nodes; the root is decided on the documents and lines as they are at that card, so the cards of
  one save see what the cards before them made.
- A card whose kind the save does not change keeps its lines. The R↔B conversion of a root a card
  holds moves its line to the new id and kind, so the card keeps showing, owning and — when removed
  while still a draft — deleting it.
- A run result bound to a node takes the line of its type: the empty draft it replaces is
  superseded by the result, so the line, the Canvas and the supersede chain agree.
"""
from __future__ import annotations

from typing import Iterable, Optional

from flowgate.card_documents import legacy_adapter as legacy
from flowgate.card_documents import repository
from flowgate.card_documents import slots as cs


# ── draft save ───────────────────────────────────────────────────────────────

def sync_draft(group: dict, user_id: Optional[str], nodes: list[dict], removed_keys: Iterable[str],
               registered: set[str], previous_nodes: Iterable[dict] = ()) -> dict[str, list[dict]]:
    """Bring the card lines in line with the saved draft; returns the lines of the draft's nodes.

    ``previous_nodes`` are the nodes of the draft (or, for a new draft, the revision) the save replaces.
    """
    flow_id = group["group_id"]
    if not repository.available():
        return {}
    before = {n["node_key"]: _kind(n) for n in previous_nodes}
    by_node = _rows_by_node(flow_id)
    for key in removed_keys:
        _dispose(flow_id, _released(flow_id, key, by_node.get(key, []), set()), user_id)
    for node in nodes:
        if before.get(node["node_key"]) == _kind(node):
            continue                            # an unchanged card keeps its lines
        wanted = {cs.slot_signature(s) for s in cs.card_slots(node["node_type"], node.get("doc_type"))}
        _dispose(flow_id, _released(flow_id, node["node_key"], by_node.get(node["node_key"], []), wanted), user_id)
    by_node = _rows_by_node(flow_id)            # every release is done before any line is filled
    for node in nodes:
        spec = cs.card_slots(node["node_type"], node.get("doc_type"))
        wanted = {cs.slot_signature(s) for s in spec}
        made = {r["slot"]: r["doc_id"] for r in by_node.get(node["node_key"], []) if _signature(r) in wanted}
        for ordinal, slot in enumerate(spec):
            if slot.slot in made:
                continue
            doc_id, owned = _line_document(group, node, slot, made, registered, user_id)
            if doc_id is None:
                continue
            repository.insert(flow_id, node["node_key"], slot.slot, slot.doc_type, doc_id, owned, ordinal, user_id)
            made[slot.slot] = doc_id
    by_id = {d["doc_id"]: d for d in legacy.flow_documents(flow_id) if d.get("doc_id")}
    stored = lines_by_node(flow_id, by_id, [n["node_key"] for n in nodes])
    out = {n["node_key"]: cs.lines_of(n["node_type"], n.get("doc_type"), stored.get(n["node_key"], []))
           for n in nodes}
    return {key: shown for key, shown in out.items() if shown}


def _signature(row: dict) -> tuple:
    return row["slot"], row["doc_type"]


def _kind(node: dict) -> tuple:
    return node.get("node_type"), str(node.get("doc_type") or "").strip().upper()


def _rows_by_node(flow_id: str) -> dict[str, list[dict]]:
    by_node: dict[str, list[dict]] = {}
    for row in repository.rows(flow_id):
        by_node.setdefault(row["node_key"], []).append(row)
    return by_node


def _released(flow_id: str, node_key: str, rows: list[dict], wanted: set) -> list[dict]:
    """The node's lines the draft no longer uses and no confirmed revision of the node shows."""
    stale = [r for r in rows if _signature(r) not in wanted]
    if not stale:
        return []
    kept = {cs.slot_signature(s) for node_type, doc_type in repository.revision_kinds(flow_id, node_key)
            for s in cs.card_slots(node_type, doc_type, ai_delegation=True)}
    return [r for r in stale if _signature(r) not in kept]


def _line_document(group: dict, node: dict, slot: cs.CardSlot, made: dict, registered: set[str],
                   user_id: Optional[str]) -> tuple[Optional[str], bool]:
    """(document of a new line, whether the card owns it); (None, _) = the line stays empty."""
    if slot.shares:
        return made.get(slot.shares), False
    if slot.doc_type not in registered:
        return None, False                      # the project has no such type: nothing to open
    if slot.doc_type in cs.ROOT_TYPES:
        root = _existing_root(group["group_id"], slot.doc_type)
        if root is not False:
            return root, False
    return legacy.create_document(group, node, slot, user_id), True


def _existing_root(flow_id: str, doc_type: str):
    """The Flow's live root the card shows; None = roots exist but none fits; False = no root.

    Read now, not once per save: a root an earlier card of the same save made counts, and every
    line the save releases is already gone, so one save never makes two roots nor misses a removed
    one, whatever the order of its nodes.
    """
    roots = [d for d in legacy.flow_documents(flow_id)
             if legacy.is_alive(d, flow_id) and str(d.get("type_code") or "").upper() in cs.ROOT_TYPES]
    if not roots:
        return False
    claimed = repository.doc_ids(flow_id)
    free = sorted(d["doc_id"] for d in roots
                  if str(d.get("type_code") or "").upper() == doc_type and d["doc_id"] not in claimed)
    return free[0] if len(free) == 1 else None


def _dispose(flow_id: str, rows: list[dict], user_id: Optional[str]) -> None:
    """Unlink lines; the card's own documents that are still drafts are deleted with them."""
    for row in rows:
        repository.delete(flow_id, row["node_key"], row["slot"], row["doc_type"])
    still_linked = repository.doc_ids(flow_id)
    for doc_id in dict.fromkeys(r["doc_id"] for r in rows if r["owned"]):
        if doc_id not in still_linked and cs.is_disposable_draft(legacy.document(doc_id)):
            legacy.delete_document(doc_id, user_id)


# ── R↔B root conversion ──────────────────────────────────────────────────────

def follow_root_conversion(flow_id: str, old_doc_id: str, converted: dict) -> None:
    """The converter gave a root a new id and kind (``document_service.convert_root_document_type``,
    inside the same Flow guard): the lines holding it follow — id, line kind (R/B) — and keep their
    ownership. A line of the new kind the node already has keeps its kind and only takes the new id."""
    if not repository.available():
        return
    new_id, new_type = converted["doc_id"], str(converted.get("type_code") or "").upper()
    rows = repository.rows(flow_id)
    taken = {(r["node_key"], r["slot"], r["doc_type"]) for r in rows}
    for row in rows:
        if row["doc_id"] != old_doc_id:
            continue
        doc_type = row["doc_type"]
        if doc_type in cs.ROOT_TYPES and new_type in cs.ROOT_TYPES and (row["node_key"], row["slot"], new_type) not in taken:
            doc_type = new_type
        repository.retarget(flow_id, row["node_key"], row["slot"], row["doc_type"], doc_type, new_id)


# ── run results ──────────────────────────────────────────────────────────────

def adopt_result(flow_id: str, node_key: str, doc_id: Optional[str]) -> None:
    """A document bound to a node (run result / its revision) takes the line of its type."""
    if not doc_id or not repository.available():
        return
    doc = legacy.document(doc_id)
    if doc is None:
        return
    doc_type = str(doc.get("type_code") or "").upper()
    replaced: list[str] = []
    for row in repository.rows(flow_id, [node_key]):
        if row["doc_type"] != doc_type or row["doc_id"] == doc_id:
            continue
        repository.rebind(flow_id, node_key, row["slot"], row["doc_type"], doc_id)
        if row["owned"]:
            replaced.append(row["doc_id"])
    for old_id in dict.fromkeys(replaced):
        old = legacy.document(old_id)
        if old and not old.get("superseded_by") and old.get("status") != legacy.CANCELLED_STATUS:
            legacy.supersede(old_id, doc_id)


# ── reads ────────────────────────────────────────────────────────────────────

def lines_by_node(flow_id: str, docs_by_id: dict, node_keys: Optional[Iterable[str]] = None) -> dict[str, list[dict]]:
    """``node_key -> [line]`` with each line's live document (the end of its supersede chain)."""
    out: dict[str, list[dict]] = {}
    for row in repository.rows(flow_id, node_keys):
        doc = docs_by_id.get(row["doc_id"])
        end = legacy.live_end(flow_id, doc, docs_by_id)[0] if doc else None
        shown = end or (doc if doc and legacy.is_alive(doc, flow_id) else None)
        out.setdefault(row["node_key"], []).append({
            "slot": row["slot"], "doc_type": row["doc_type"], "author": cs.author_of(row["slot"]),
            "doc_id": shown["doc_id"] if shown else None,
            "title": (shown or {}).get("title"), "status": (shown or {}).get("status"),
            "doc_review_status": (shown or {}).get("doc_review_status"),
        })
    return out


class CardView:
    """The Canvas read of one request: each node's lines of the kind the viewed definition gives it,
    and the documents a card made (drawn inside the card, never as free document nodes)."""

    def __init__(self, flow_id: str, docs_by_id: dict):
        available = repository.available()
        self._lines = lines_by_node(flow_id, docs_by_id) if available else {}
        self.hidden_doc_ids = frozenset(repository.doc_ids(flow_id, owned_only=True)) if available else frozenset()

    def lines_for(self, node: dict) -> list:
        return cs.lines_of(node.get("node_type"), node.get("doc_type"), self._lines.get(node.get("node_key")) or [])
