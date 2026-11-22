"""Storage boundary for the shared first-run naming operation (0071)."""
from __future__ import annotations
import json
from modules.flow_gate.db import groups, documents
from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.graph_workflow.services import first_names_store as ledger
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.graph_workflow.services.flow_default_names import is_default_title
from modules.flow_gate.graph_workflow.services.graph_service import require_writable, as_graph
from modules.flow_gate.graph_workflow.services.engine_router import read_ownership
from modules.flow_gate.graph_workflow.services.canvas_service import follow_supersede, title_views
from modules.flow_gate.graph_workflow.projection.graph_projector import card_document, card_title_source
from modules.flow_gate.graph_workflow.services.canvas_events import publish_canvas_changed, CANVAS_REASON_TITLE
from modules.flow_gate.graph_workflow.store import definition_store, assignment_store, title_store
from modules.flow_gate.graph_workflow.domain.root_binding import check_root_binding
from modules.flow_gate.graph_workflow.projection.flow_snapshot import read_snapshot, alive_documents


def guard(flow_id, body):
    return with_flow_guard(flow_id, body)


def targets(flow_id, chat_doc_id):
    require_writable(flow_id)
    group = groups.get_by_id(flow_id)
    if not group:
        raise ValueError("flow_missing")
    record = ledger.load(flow_id)
    out = {"flow": {"id": flow_id, "title": group["title"],
                    "manual": bool(record.get("flow_manual"))}}
    draft = definition_store.draft(flow_id)
    latest = definition_store.latest_revision_no(flow_id)
    definition = draft or (definition_store.revision(flow_id, latest) if latest else None)
    snap = read_snapshot(flow_id)
    binding = check_root_binding(as_graph(definition), read_ownership(flow_id).state,
                                 alive_documents(snap), assignment_store.root_binding(flow_id),
                                 lambda doc_id: follow_supersede(flow_id, snap.docs_by_id, doc_id))
    if binding.node_key and binding.doc_type in ("R", "B"):
        row = next(n for n in definition["nodes"] if n["node_key"] == binding.node_key)
        st = title_store.state(flow_id, binding.node_key) if title_store.available() else None
        views, by_id = title_views(flow_id)
        display_custom = False
        for view in views:
            shown_row = next((n for n in view.nodes if n["node_key"] == binding.node_key), None)
            if shown_row is not None:
                shown = (st or {}).get("title") or card_title_source(shown_row, card_document(view, shown_row, by_id))[1]
                display_custom |= not is_default_title(shown, "root", doc_type=binding.doc_type)
        out["root"] = {"id": binding.node_key, "doc_type": binding.doc_type,
                       "title": (st or {}).get("title") or row.get("label"),
                       "manual": display_custom or bool(st and (st["human_edited"] or st["opportunity_state"] != "unused")),
                       "version": st["version"] if st else None}
    if chat_doc_id:
        doc = documents.get_by_id(chat_doc_id)
        if not doc or doc.get("group_id") != flow_id or doc.get("project_id") != group["project_id"] or doc.get("type_code") != "CH":
            raise ValueError("chat_scope_mismatch")
        meta = json.loads(doc.get("meta") or "{}")
        marker = meta.get("chat_auto_title", {})
        out["chat"] = {"id": chat_doc_id, "title": doc.get("title"), "meta": doc.get("meta"),
                       "manual": bool(meta.get("first_name_manual") or marker.get("state") in
                                      ("manual", "done", "claimed", "failed"))}
    return group, out


def eligible(kind, target):
    return not target["manual"] and is_default_title(target["title"], kind, doc_type=target.get("doc_type"))


def apply(flow_id, kind, expected, title, actor):
    """Caller owns guard; title and ledger status commit in the same transaction."""
    if kind == "flow":
        groups.update(flow_id, {"title": title})
    elif kind == "root":
        key = expected["id"]
        token = "first-names:" + flow_id
        if title_store.claim(flow_id, key, token, None, expected.get("version")) != 1:
            raise RuntimeError("root_claim_changed")
        st = title_store.state(flow_id, key)
        if title_store.apply_ai(flow_id, key, title, token, st["version"]) != 1:
            raise RuntimeError("root_title_changed")
        group = groups.get_by_id(flow_id)
        publish_canvas_changed(project_id=group["project_id"], flow_id=flow_id, reason=CANVAS_REASON_TITLE,
                               actor_user_id=actor, origin_client_id=None, node_keys=[key])
    else:
        meta = json.loads(expected.get("meta") or "{}")
        meta["chat_auto_title"] = {"state": "done", "title": title}
        changed = get_store()._execute_affected(
            "UPDATE documents SET title = ?, meta = ?, updated_at = ? WHERE doc_id = ? AND title = ? "
            "AND COALESCE(meta, '') = ?",
            [title, json.dumps(meta, ensure_ascii=False), now_iso(), expected["id"], expected["title"],
             expected.get("meta") or ""])
        if changed != 1:
            raise RuntimeError("chat_title_changed")
