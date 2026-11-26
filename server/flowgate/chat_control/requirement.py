"""A chat places one requirement card, in one guarded draft transaction.

Transport authorization is shared with work placement. Never ensure a start card before
the refusal checks: duplicate/ambiguous requests must not mutate the Flow.
"""
from flowgate.card_documents import legacy_adapter as legacy, repository, slots
from modules.flow_gate.graph_workflow.domain import draft_request as dr
from modules.flow_gate.graph_workflow.projection.flow_snapshot import registered_doc_types
from modules.flow_gate.graph_workflow.services import card_settings_ports, graph_service, root_start
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.graph_workflow.store import definition_store

CLIENT_KEY = "tmp_chat_requirement"


def place(group, user_id, card, locale):
    from .service import _definition

    flow_id = group["group_id"]

    def save():
        record, version = _definition(flow_id)
        existing = [n for n in record.get("nodes", [])
                    if n.get("node_type") == "document" and n.get("doc_type") in ("R", "B")]
        if existing:
            raise GraphApiError(409, "chat_cards_start_exists", {
                "flow_id": flow_id, "cards": [
                    {k: n.get(k) for k in ("node_key", "doc_type", "label")} for n in existing]})
        claimed = repository.doc_ids(flow_id)
        candidates = sorted(d["doc_id"] for d in legacy.flow_documents(flow_id)
                            if legacy.is_alive(d, flow_id) and str(d.get("type_code") or "").upper() == "R"
                            and d["doc_id"] not in claimed)
        if len(candidates) > 1:
            raise GraphApiError(409, "chat_cards_requirement_ambiguous",
                                {"flow_id": flow_id, "doc_ids": candidates})
        nodes = [root_start._node_input(i, n) for i, n in enumerate(record.get("nodes", []))]
        nodes.append(dr.NodeInput(index=len(nodes), node_key=None, client_key=CLIENT_KEY,
                                  node_type="document", doc_type="R", palette_item_id="requirement",
                                  label=card["label"]))
        edges = [dr.EdgeInput(index=i, edge_id=e["edge_id"], client_key=None, source=e["source"],
                              target=e["target"], condition=e.get("condition"))
                 for i, e in enumerate(record.get("edges", []))]
        request = dr.DraftRequest(draft_version=version, nodes=nodes, edges=edges)
        saved = graph_service.write_draft(group, user_id, request, None,
                                         registered_doc_types(group.get("project_id")), locale)
        key = saved["key_map"][CLIENT_KEY]
        node = next(n for n in definition_store.draft(flow_id)["nodes"] if n["node_key"] == key)
        line = next((r for r in repository.rows(flow_id, [key]) if r["slot"] == slots.REQUIREMENT), None)
        # The palette factory reuses the single free R or creates the first root.
        # With only other roots / historical claimed roots it leaves the line empty;
        # this explicit R placement still requires its own new empty R document.
        if line is None:
            slot = next(s for s in slots.card_slots("document", "R") if s.slot == slots.REQUIREMENT)
            doc_id = candidates[0] if candidates else legacy.create_document(group, node, slot, user_id, locale=locale)
            repository.insert(flow_id, key, slot.slot, "R", doc_id, not candidates, 0, user_id)
        else:
            doc_id = line["doc_id"]
        card_settings_ports.cards_created(group, user_id, [node])
        root = {"node_key": key, "doc_type": "R", "doc_id": doc_id, "created": True}
        return {"flow_id": flow_id, "draft_version": saved["draft_version"], "after": None,
                "root": root, "cards": [{"node_key": key, "label": card["label"], "doc_type": "R",
                                         "requirement": {"slot": slots.REQUIREMENT, "doc_id": doc_id}}],
                "gate": None, "defaults_used": [],
                "next": "The requirement card is placed. Read or write its requirement line. "
                        "To append work cards, use this node_key as after."}

    return with_flow_guard(flow_id, save)
