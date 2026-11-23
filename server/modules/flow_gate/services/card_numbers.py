"""Card numbers of a Flow (the ``12`` of ``F77-12``) and the lookup of typed Flow / card ids.

Migration 211_graph_card_numbers.sql. One counter row per Flow (``graph_card_counters.next_card_no``) and one
number row per card (``graph_card_numbers``, keyed by Flow and node key, the number unique in its Flow).

* A number is given once and never changes or moves: rows are only ever inserted, never updated or deleted
  here, and the counter only goes up, so the number of a deleted card is never handed out again. A copied
  card is a new node key (the draft save issues one), so it gets the next number of the Flow it lands in.
* ``assign`` reserves its numbers with one ``UPDATE ... next_card_no = next_card_no + n`` on the Flow's counter
  row, which every dialect serializes, so two writers never take the same number; a node key numbered by a
  concurrent writer meanwhile keeps that number (``ON CONFLICT DO NOTHING``) and the reserved one is a gap.
* The first ``assign`` of a Flow numbers the cards it already has, in the order they were made (the confirmed
  revisions oldest first, each in its stored order, then the draft), before the keys it was given.

The lookup reads the same rows: a typed id (``card_ids.parse``) -> the Flow(s) and card it names, with the
card's representative document (its work instruction, else its requirement / bug document, else its first
other line). Nothing here imports the Graph package; it reads the Graph tables it needs directly.
SQL is written in the SQLite/PostgreSQL form; ``db/dialect.py`` rewrites it for MySQL.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from modules.flow_gate.db.connection import get_store, in_transaction

from . import card_ids

NUMBER_TABLE = "graph_card_numbers"
COUNTER_TABLE = "graph_card_counters"
DOC_KEY_PREFIX = "doc:"
# Which line of a card stands for it when a typed card id opens a document.
SLOT_RANK = {"instruction": 0, "requirement": 1, "delegation": 2, "plan": 3, "deploy": 4, "scenario": 5,
             "report": 6}
RESOLVE_LIMIT = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def available() -> bool:
    """False until migration 211 is applied (no card then has a number, and nothing is written)."""
    store = get_store()
    return store.table_exists(NUMBER_TABLE) and store.table_exists(COUNTER_TABLE)


def _dedupe(keys: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


# ── Reads ────────────────────────────────────────────────────────────────────

def numbers(flow_id: str) -> dict[str, int]:
    """``node_key -> card_no`` of the Flow."""
    if not available():
        return {}
    rows = get_store()._fetch_all(
        "SELECT node_key, card_no FROM graph_card_numbers WHERE group_id = ?", [flow_id])
    return {r["node_key"]: int(r["card_no"]) for r in rows or []}


def node_for(flow_id: str, card_no: int) -> Optional[str]:
    """The node key of card ``card_no`` of the Flow, or None."""
    if not available():
        return None
    row = get_store()._fetch_one(
        "SELECT node_key FROM graph_card_numbers WHERE group_id = ? AND card_no = ?", [flow_id, int(card_no)])
    return row["node_key"] if row else None


def _counter(flow_id: str) -> Optional[int]:
    row = get_store()._fetch_one("SELECT next_card_no FROM graph_card_counters WHERE group_id = ?", [flow_id])
    return int(row["next_card_no"]) if row else None


def history_keys(flow_id: str) -> list[str]:
    """Every node key the Flow's Graph has held, in the order its cards were made: the confirmed revisions
    oldest first (each in stored order), then the draft."""
    store = get_store()
    keys: list[str] = []
    if store.table_exists("graph_revision_nodes"):
        keys += [r["node_key"] for r in store._fetch_all(
            "SELECT node_key FROM graph_revision_nodes WHERE group_id = ? ORDER BY revision_no, ordinal",
            [flow_id]) or []]
    if store.table_exists("graph_draft_nodes"):
        keys += [r["node_key"] for r in store._fetch_all(
            "SELECT node_key FROM graph_draft_nodes WHERE group_id = ? ORDER BY ordinal", [flow_id]) or []]
    return _dedupe(keys)


# ── Writes ───────────────────────────────────────────────────────────────────

def assign(flow_id: str, keys: Iterable[str]) -> dict[str, int]:
    """Give each of ``keys`` without a number the Flow's next number, in the given order; answer the Flow's
    numbers. Joins the caller's transaction (a Flow guard) or runs in one of its own."""
    wanted = _dedupe(keys)
    if not available():
        return {}
    store = get_store()

    def body() -> dict[str, int]:
        now = _now()
        store._execute(
            "INSERT INTO graph_card_counters (group_id, next_card_no, created_at, updated_at) VALUES (?, 1, ?, ?) "
            "ON CONFLICT (group_id) DO NOTHING", [flow_id, now, now])
        # The counter row lock: every later read of this unit sees the numbers committed before it.
        store._execute_affected(
            "UPDATE graph_card_counters SET updated_at = ? WHERE group_id = ?", [now, flow_id])
        order = (history_keys(flow_id) + wanted) if _counter(flow_id) == 1 else wanted
        existing = numbers(flow_id)
        missing = [key for key in _dedupe(order) if key not in existing]
        if not missing:
            return existing
        store._execute_affected(
            "UPDATE graph_card_counters SET next_card_no = next_card_no + ?, updated_at = ? WHERE group_id = ?",
            [len(missing), now, flow_id])
        first = (_counter(flow_id) or 1) - len(missing)
        for offset, key in enumerate(missing):
            store._execute(
                "INSERT INTO graph_card_numbers (group_id, node_key, card_no, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (group_id, node_key) DO NOTHING", [flow_id, key, first + offset, now])
        return numbers(flow_id)

    if in_transaction():
        return body()
    with store.transaction():
        return body()


def ensure_flow(flow_id: str) -> None:
    """Number the cards of a Flow that has never been numbered (no write when it has been, or when called
    inside an open transaction that is not meant to write)."""
    if not available() or in_transaction():
        return
    counter = _counter(flow_id)
    if counter is None or counter == 1:
        assign(flow_id, [])


# ── Display ids ──────────────────────────────────────────────────────────────

def decorate(flow_id: str, nodes: list[dict]) -> Optional[dict]:
    """``card_ref`` on each Canvas node (None while it has no number); answers the Flow's ``flow_ref``."""
    known = numbers(flow_id)
    for node in nodes:
        node["card_ref"] = card_ids.card_ref(flow_id, known.get(node.get("node_key")))
    return card_ids.flow_ref(flow_id)


def fill_missing(flow_id: str, nodes: list[dict]) -> None:
    """After a Canvas read: number the drawn cards that have none yet (a Flow made before card numbers, a v0.1
    slot, a free document), in drawn order after the Flow's own history, and put their ``card_ref`` on."""
    missing = [n["node_key"] for n in nodes if n.get("card_ref") is None and n.get("node_key")]
    if not missing or not available() or in_transaction():
        return
    known = assign(flow_id, missing)
    for node in nodes:
        if node.get("card_ref") is None:
            node["card_ref"] = card_ids.card_ref(flow_id, known.get(node.get("node_key")))


# ── Lookup ───────────────────────────────────────────────────────────────────

def _group(group_id: str) -> Optional[dict]:
    row = get_store()._fetch_one(
        "SELECT group_id, project_id, module, title FROM groups WHERE group_id = ?", [group_id])
    return dict(row) if row else None


def _flows(parsed: card_ids.ParsedRef, context: Optional[dict]) -> list[dict]:
    """The Flows a parsed id can name: its number, narrowed by the typed project / module, else by the
    context Flow's project and module (a bare ``F77-12`` in a Flow), else every module."""
    if parsed.flow_no is None:
        return [context] if context else []
    rows = get_store()._fetch_all(
        "SELECT group_id, project_id, module, title FROM groups WHERE group_id LIKE ? ORDER BY group_id",
        [f"%{parsed.flow_no}"]) or []
    out = []
    for row in rows:
        split = card_ids.split_group_id(row["group_id"])
        if split is None or split[2] != parsed.flow_no:
            continue
        project, module = row.get("project_id") or split[0], row.get("module") or split[1]
        if parsed.project is not None and not card_ids.same_key(parsed.project, project):
            continue
        if parsed.module is not None and not card_ids.same_key(parsed.module, module):
            continue
        out.append(dict(row))
    if parsed.module is None and context is not None:
        narrowed = [r for r in out if card_ids.same_key(r.get("project_id"), context.get("project_id"))
                    and card_ids.same_key(r.get("module"), context.get("module"))]
        if narrowed:
            out = narrowed
    return out


def representative_doc(flow_id: str, node_key: str) -> Optional[str]:
    """The document a card id opens: its instruction line, else requirement / bug, else its first line; a
    root card without lines its bound root; a v0.1 document node its document."""
    store = get_store()
    if store.table_exists("graph_node_documents"):
        rows = store._fetch_all(
            "SELECT n.slot, n.doc_id, n.ordinal FROM graph_node_documents n JOIN documents d ON d.doc_id = n.doc_id "
            "WHERE n.group_id = ? AND n.node_key = ?", [flow_id, node_key]) or []
        if rows:
            best = min(rows, key=lambda r: (SLOT_RANK.get(r["slot"], len(SLOT_RANK)), int(r["ordinal"] or 0)))
            return best["doc_id"]
    if store.table_exists("graph_root_bindings"):
        row = store._fetch_one(
            "SELECT doc_id FROM graph_root_bindings WHERE group_id = ? AND node_key = ?", [flow_id, node_key])
        if row:
            return row["doc_id"]
    if node_key.startswith(DOC_KEY_PREFIX):
        return node_key[len(DOC_KEY_PREFIX):]
    return None


def root_doc(flow_id: str) -> Optional[str]:
    """The document a Flow id opens: the Flow's bound root, else its first requirement / bug document."""
    store = get_store()
    if store.table_exists("graph_root_bindings"):
        row = store._fetch_one("SELECT doc_id FROM graph_root_bindings WHERE group_id = ?", [flow_id])
        if row:
            return row["doc_id"]
    row = store._fetch_one(
        "SELECT doc_id FROM documents WHERE group_id = ? AND type_code IN ('R', 'B') ORDER BY seq LIMIT 1", [flow_id])
    return row["doc_id"] if row else None


def resolve(text: Optional[str], *, context_flow_id: Optional[str] = None,
            allowed_projects: Optional[Iterable[str]] = None, allow_bare: bool = True) -> list[dict]:
    """The Flows / cards a typed id names (empty when ``text`` is no Flow / card id or names nothing).

    Each candidate: ``kind`` (card / flow), ``group_id``, ``project_id``, ``module``, ``group_title``,
    ``flow_ref``, ``card_ref``, ``node_key`` and ``doc_id`` (the representative document; a Flow's root)."""
    parsed = card_ids.parse(text, allow_bare=allow_bare, allow_local=context_flow_id is not None)
    if parsed is None:
        return []
    context = _group(context_flow_id) if context_flow_id else None
    allowed = None if allowed_projects is None else {p for p in allowed_projects}
    out = []
    for flow in _flows(parsed, context)[:RESOLVE_LIMIT]:
        flow_id = flow["group_id"]
        if allowed is not None and flow.get("project_id") not in allowed:
            continue
        item = {"kind": parsed.kind, "group_id": flow_id, "project_id": flow.get("project_id"),
                "module": flow.get("module"), "group_title": flow.get("title"),
                "flow_ref": card_ids.flow_ref(flow_id), "card_ref": None, "node_key": None, "doc_id": None}
        if parsed.card_no is None:
            item["doc_id"] = root_doc(flow_id)
            out.append(item)
            continue
        node_key = node_for(flow_id, parsed.card_no)
        if node_key is None:
            ensure_flow(flow_id)                      # a Flow nobody opened since card numbers came
            node_key = node_for(flow_id, parsed.card_no)
        if node_key is None:
            continue
        item.update(card_ref=card_ids.card_ref(flow_id, parsed.card_no), node_key=node_key,
                    doc_id=representative_doc(flow_id, node_key))
        out.append(item)
    return out


def card_of_document(doc_id: str, flow_id: Optional[str]) -> Optional[dict]:
    """The numbered card a document is a line of (its ``card_ref`` and node key), or None."""
    if not flow_id or not available() or not get_store().table_exists("graph_node_documents"):
        return None
    row = get_store()._fetch_one(
        "SELECT c.node_key, c.card_no FROM graph_node_documents n JOIN graph_card_numbers c "
        "ON c.group_id = n.group_id AND c.node_key = n.node_key WHERE n.group_id = ? AND n.doc_id = ? "
        "ORDER BY c.card_no LIMIT 1", [flow_id, doc_id])
    if row is None:
        return None
    return {"node_key": row["node_key"], "card_ref": card_ids.card_ref(flow_id, int(row["card_no"]))}
