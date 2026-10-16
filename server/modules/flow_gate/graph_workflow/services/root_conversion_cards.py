"""Draft cards follow an R↔B root conversion (0064 L0005 1.5 / 2.8, P0006 §3.6).

In the conversion's transaction, after the card lines moved: a draft ``document`` card of the old type
whose ``requirement`` line now holds the converted document takes the new type. Its palette id changes
only from one reserved root id to the other, and its label only while it is the old type's default name
(ko / ja / en, normalized as the 0062 L0006 2.1 titles); a label or palette id a person chose stays.
Only when a card changed: the draft version goes up by one and ``graph_canvas_changed`` (``draft``) is
queued for after the commit. Confirmed revisions are never touched.
"""
from __future__ import annotations

import unicodedata
from typing import Iterable, Optional

from modules.flow_gate.graph_workflow.store import definition_store

from .canvas_events import publish_canvas_changed

CANVAS_REASON_DRAFT = "draft"
ROOT_NAME_KEYS = {
    "R": "gui.designer.center.node_kinds.requirement",
    "B": "gui.designer.inspector.header.kind_bug",
}
ROOT_NAMES = {
    "R": {"ko": "요구사항", "ja": "要件", "en": "Requirement"},
    "B": {"ko": "버그", "ja": "バグ", "en": "Bug"},
}
ROOT_PALETTE_IDS = {"R": "requirement", "B": "bug"}
LOCALES = ("ko", "ja", "en")


def name_key(raw: Optional[str]) -> str:
    """0062 L0006 2.1 normalization (NFC, controls and spaces as one space, zero-width removed), casefolded."""
    text = unicodedata.normalize("NFC", raw or "")
    text = "".join(" " if unicodedata.category(ch) in {"Cc", "Zs", "Zl", "Zp"} else ch
                   for ch in text if ch not in {"​", "﻿"})
    return " ".join(part for part in text.split(" ") if part).casefold()


def swap_palette(palette_item_id: Optional[str], old_type: str, new_type: str) -> Optional[str]:
    return ROOT_PALETTE_IDS[new_type] if palette_item_id == ROOT_PALETTE_IDS[old_type] else palette_item_id


def swap_label(label: Optional[str], old_type: str, new_type: str) -> Optional[str]:
    key = name_key(label)
    for locale in LOCALES:
        if key == name_key(ROOT_NAMES[old_type][locale]):
            return ROOT_NAMES[new_type][locale]
    return label


def sync_draft_cards(flow_id: str, project_id: Optional[str], doc_id: str, holders: Iterable[str],
                     old_type: str, new_type: str, actor: Optional[str]) -> list[str]:
    """The node keys whose draft card changed (empty: no draft, no holder or nothing to change)."""
    holders = set(holders or ())
    head = definition_store.draft_header(flow_id) if holders else None
    if head is None or old_type not in ROOT_NAMES or new_type not in ROOT_NAMES:
        return []
    draft = definition_store.draft(flow_id)
    nodes, changed = [], []
    for node in draft["nodes"]:
        node = dict(node)
        if (node.get("node_type") == "document"
                and str(node.get("doc_type") or "").strip().upper() == old_type
                and node["node_key"] in holders):
            node["doc_type"] = new_type
            node["palette_item_id"] = swap_palette(node.get("palette_item_id"), old_type, new_type)
            node["label"] = swap_label(node.get("label"), old_type, new_type)
            changed.append(node["node_key"])
        nodes.append(node)
    if not changed:
        return []
    if definition_store.bump_draft(flow_id, head["draft_version"], actor) != 1:
        from modules.flow_gate.documents.conversion_errors import ConversionRefused

        raise ConversionRefused("draft_version_conflict", doc_id=doc_id)
    definition_store.replace_draft_content(flow_id, nodes, draft["edges"])
    publish_canvas_changed(project_id=project_id, flow_id=flow_id, reason=CANVAS_REASON_DRAFT,
                           actor_user_id=actor, origin_client_id=None,
                           draft_version=int(head["draft_version"]) + 1)
    return changed
