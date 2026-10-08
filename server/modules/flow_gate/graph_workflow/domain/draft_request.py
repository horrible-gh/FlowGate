"""Draft save request checks (P0008 §5.1 / S3-4, L0009 §1.4) — shape only, no storage.

``parse_draft`` checks everything the request alone can tell. Two checks need the Flow's
stored state and are added by the service inside the Flow guard with the same error shape:
``not_in_draft_or_revision`` (``unknown_keys``) and ``invalid_doc_type`` (``unknown_doc_types``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .graph_model import (
    CLIENT_KEY_PREFIX,
    DRAFT_EDGES_MAX,
    DRAFT_NODES_MAX,
    LABEL_MAX_LEN,
    NODE_KEY_MAX_LEN,
    NODE_TYPES,
    PALETTE_ITEM_ID_MAX_LEN,
)


@dataclass
class NodeInput:
    index: int
    node_key: Optional[str]
    client_key: Optional[str]
    node_type: str
    doc_type: Optional[str]
    palette_item_id: Optional[str]
    label: str
    assignee_kind: Optional[str] = None
    assignee_user_id: Optional[str] = None
    assignee_provider_id: Optional[str] = None

    @property
    def ref(self) -> str:
        return self.node_key or self.client_key or ""


@dataclass
class EdgeInput:
    index: int
    edge_id: Optional[str]
    client_key: Optional[str]
    source: str
    target: str
    condition: Any = None


@dataclass
class DraftRequest:
    draft_version: Optional[int] = None
    nodes: list[NodeInput] = field(default_factory=list)
    edges: list[EdgeInput] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)


def _err(errors: list[dict], f: str, reason: str) -> None:
    errors.append({"field": f, "reason": reason})


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_version(body: Any, name: str = "draft_version", minimum: int = 0) -> tuple[Optional[int], list[dict]]:
    errors: list[dict] = []
    if not isinstance(body, dict):
        return None, [{"field": "body", "reason": "not_an_object"}]
    value = body.get(name)
    if not _is_int(value):
        _err(errors, name, "not_an_integer")
        return None, errors
    if value < minimum:
        _err(errors, name, "not_positive" if minimum > 0 else "negative")
        return None, errors
    return value, errors


def _key(errors, raw: dict, f: str, name: str) -> Optional[str]:
    value = raw.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        _err(errors, f"{f}.{name}", "not_a_string")
        return None
    if len(value) > NODE_KEY_MAX_LEN:
        _err(errors, f"{f}.{name}", "too_long")
        return None
    if name == "client_key" and not value.startswith(CLIENT_KEY_PREFIX):
        _err(errors, f"{f}.{name}", "invalid_client_key")
        return None
    return value


def _identity(errors, raw: dict, f: str, key_name: str) -> tuple[Optional[str], Optional[str], bool]:
    key = _key(errors, raw, f, key_name)
    client = _key(errors, raw, f, "client_key")
    if raw.get(key_name) is None and raw.get("client_key") is None:
        _err(errors, f"{f}.{key_name}", "required")
        return None, None, False
    if raw.get(key_name) is not None and raw.get("client_key") is not None:
        _err(errors, f"{f}.client_key", "must_be_null_with_existing_key")
        return None, None, False
    return key, client, key is not None or client is not None


def _assignee(errors, raw: Any, f: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    if raw is None:
        return None, None, None
    if not isinstance(raw, dict):
        _err(errors, f, "invalid_assignee")
        return None, None, None
    kind = raw.get("kind")
    ref = raw.get("user_id") if kind == "human" else raw.get("provider_id") if kind == "ai" else None
    if kind not in ("human", "ai") or not isinstance(ref, str) or not ref.strip():
        _err(errors, f, "invalid_assignee")
        return None, None, None
    return kind, (ref if kind == "human" else None), (ref if kind == "ai" else None)


def _node(errors: list[dict], i: int, raw: Any) -> Optional[NodeInput]:
    f = f"nodes[{i}]"
    if not isinstance(raw, dict):
        _err(errors, f, "not_an_object")
        return None
    before = len(errors)
    key, client, _ok = _identity(errors, raw, f, "node_key")
    node_type = raw.get("node_type")
    if node_type not in NODE_TYPES:
        _err(errors, f"{f}.node_type", "not_one_of:" + ",".join(NODE_TYPES))
    doc_type = raw.get("doc_type")
    if node_type == "document" and (not isinstance(doc_type, str) or not doc_type.strip()):
        _err(errors, f"{f}.doc_type", "required_for_document_node")
    elif node_type in ("gate", "custom") and doc_type is not None:
        _err(errors, f"{f}.doc_type", "must_be_null_for_non_document")
    palette = raw.get("palette_item_id")
    if palette is not None and (not isinstance(palette, str) or len(palette) > PALETTE_ITEM_ID_MAX_LEN):
        _err(errors, f"{f}.palette_item_id", "invalid_palette_item_id")
    label = raw.get("label")
    if not isinstance(label, str) or not label.strip() or len(label) > LABEL_MAX_LEN:
        _err(errors, f"{f}.label", "label_length")
    kind, user, provider = _assignee(errors, raw.get("assignee"), f"{f}.assignee")
    if len(errors) != before:
        return None
    return NodeInput(
        index=i, node_key=key, client_key=client, node_type=node_type,
        doc_type=doc_type.strip().upper() if node_type == "document" else None,
        palette_item_id=palette, label=label, assignee_kind=kind,
        assignee_user_id=user, assignee_provider_id=provider,
    )


def _edge(errors: list[dict], i: int, raw: Any) -> Optional[EdgeInput]:
    f = f"edges[{i}]"
    if not isinstance(raw, dict):
        _err(errors, f, "not_an_object")
        return None
    before = len(errors)
    key, client, _ok = _identity(errors, raw, f, "edge_id")
    for end in ("source", "target"):
        if not isinstance(raw.get(end), str) or not raw.get(end):
            _err(errors, f"{f}.{end}", "unknown_node_reference")
    if len(errors) != before:
        return None
    return EdgeInput(index=i, edge_id=key, client_key=client, source=raw["source"], target=raw["target"],
                     condition=raw.get("condition"))


def parse_draft(body: Any) -> DraftRequest:
    req = DraftRequest()
    version, errors = parse_version(body)
    req.draft_version = version
    req.errors.extend(errors)
    if not isinstance(body, dict):
        return req
    raw_nodes, raw_edges = body.get("nodes"), body.get("edges")
    for name, raw, limit in (("nodes", raw_nodes, DRAFT_NODES_MAX), ("edges", raw_edges, DRAFT_EDGES_MAX)):
        if not isinstance(raw, list):
            _err(req.errors, name, "not_a_list")
        elif len(raw) > limit:
            _err(req.errors, name, "too_many_items")
    if any(e["field"] in ("nodes", "edges") for e in req.errors):
        return req
    req.nodes = [n for i, raw in enumerate(raw_nodes) if (n := _node(req.errors, i, raw)) is not None]
    req.edges = [e for i, raw in enumerate(raw_edges) if (e := _edge(req.errors, i, raw)) is not None]
    _check_duplicates(req)
    refs = {n.ref for n in req.nodes} | _invalid_node_refs(raw_nodes)
    for edge in req.edges:
        for end in ("source", "target"):
            if getattr(edge, end) not in refs:
                _err(req.errors, f"edges[{edge.index}].{end}", "unknown_node_reference")
    return req


def _invalid_node_refs(raw_nodes: list) -> set[str]:
    """Keys of nodes that failed their own checks: an edge to them is not a second error."""
    out: set[str] = set()
    for raw in raw_nodes:
        if isinstance(raw, dict):
            for name in ("node_key", "client_key"):
                if isinstance(raw.get(name), str) and raw.get(name):
                    out.add(raw[name])
    return out


def _check_duplicates(req: DraftRequest) -> None:
    seen: set[str] = set()
    items = [(f"nodes[{n.index}]", "node_key" if n.node_key else "client_key", n.node_key or n.client_key)
             for n in req.nodes]
    items += [(f"edges[{e.index}]", "edge_id" if e.edge_id else "client_key", e.edge_id or e.client_key)
              for e in req.edges]
    for f, name, key in items:
        if key in seen:
            _err(req.errors, f"{f}.{name}", "duplicate_key")
        seen.add(key)


def unknown_keys(req: DraftRequest, node_keys: Iterable[str], edge_ids: Iterable[str]) -> list[dict]:
    """``not_in_draft_or_revision``: an existing key this Flow's draft / latest revision lacks."""
    nodes, edges = set(node_keys), set(edge_ids)
    errors: list[dict] = []
    for n in req.nodes:
        if n.node_key is not None and n.node_key not in nodes:
            _err(errors, f"nodes[{n.index}].node_key", "not_in_draft_or_revision")
    for e in req.edges:
        if e.edge_id is not None and e.edge_id not in edges:
            _err(errors, f"edges[{e.index}].edge_id", "not_in_draft_or_revision")
    return errors


def unknown_doc_types(req: DraftRequest, registered: Iterable[str]) -> list[dict]:
    known = {str(t).upper() for t in registered}
    return [
        {"field": f"nodes[{n.index}].doc_type", "reason": "invalid_doc_type"}
        for n in req.nodes if n.node_type == "document" and n.doc_type not in known
    ]


def sort_errors(errors: list[dict]) -> list[dict]:
    """Request order: body fields first, then nodes[i], then edges[i]."""
    def rank(e: dict):
        f = e["field"]
        group = 1 if f.startswith("nodes") else 2 if f.startswith("edges") else 0
        index = int(f[f.index("[") + 1:f.index("]")]) if "[" in f else -1
        return (group, index)
    return sorted(errors, key=rank)
