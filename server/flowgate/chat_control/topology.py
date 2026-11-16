"""Explicit fork/join placement, without exposing general graph editing.

links name card:0..card:4, $after, $end, or existing source node keys.
Only new cards and the selected end may receive new edges. Existing edges are
preserved except the anchor-to-confirm edge that ordinary insertion replaces.
"""
from dataclasses import replace

from modules.flow_gate.graph_workflow.domain import policies
from modules.flow_gate.graph_workflow.domain.root_binding import doc_key
from modules.flow_gate.graph_workflow.services import graph_service, graph_validation_ports
from modules.flow_gate.graph_workflow.services.errors import GraphApiError

from . import placement


def invalid(field, reason):
    raise GraphApiError(422, "invalid_request", {"errors": [{"field": field, "reason": reason}]})


def parse(body, count):
    if "links" not in body:
        if "join" in body:
            invalid("join", "requires_links")
        return None
    links = body["links"]
    if not isinstance(links, list) or not links or len(links) > 30:
        invalid("links", "requires_1_to_30_links")
    join = body.get("join")
    if "join" in body and (not isinstance(join, str) or not join):
        invalid("join", "not_a_node_key")
    seen = set()
    for i, link in enumerate(links):
        if not isinstance(link, dict) or set(link) != {"source", "target"}:
            invalid(f"links[{i}]", "only_source_and_target_required")
        for name in ("source", "target"):
            ref = link[name]
            if not isinstance(ref, str) or not ref:
                invalid(f"links[{i}].{name}", "not_a_reference")
            if ref.startswith("card:") and ref not in {f"card:{n}" for n in range(count)}:
                invalid(f"links[{i}].{name}", "unknown_card_reference")
        pair = (link["source"], link["target"])
        if pair in seen:
            invalid(f"links[{i}]", "duplicate_link")
        seen.add(pair)
    return links


def choose(record, protected, after, want_gate, join, root):
    # An explicit end bypasses automatic confirm selection, but still resolves
    # the anchor with the existing ambiguity and root rules.
    chosen = placement.plan(record.get("nodes", []), record.get("edges", []), protected,
                            after, want_gate, root_type=root["doc_type"] if root else None,
                            root_keys=[doc_key(root["doc_id"])] if root else ())
    if join is None:
        return chosen
    if join not in {n["node_key"] for n in record.get("nodes", [])}:
        raise GraphApiError(404, "chat_cards_join_unknown", {"join": join})
    if join in protected:
        raise GraphApiError(409, "chat_cards_join_protected", {"join": join})
    return replace(chosen, before=join, new_gate=False,
                   removed_edge=chosen.removed_edge if chosen.before == join else None)


def draft_body(record, version, chosen, cards, gate_label, root_card, links):
    body = placement.draft_body(record, version, chosen, cards, gate_label, root_card)
    # Keep its nodes and retained edges; replace only the newly generated chain.
    body["edges"] = [e for e in body["edges"] if e["edge_id"] is not None]
    existing = {n["node_key"] for n in record.get("nodes", [])}
    new = {f"{placement.CLIENT_NODE}{i}" for i in range(len(cards))}
    refs = {f"card:{i}": f"{placement.CLIENT_NODE}{i}" for i in range(len(cards))}
    refs["$after"] = chosen.anchor
    end = chosen.before or (placement.CLIENT_GATE if chosen.new_gate else None)
    if end:
        refs["$end"] = end
    removed = next((e for e in record.get("edges", []) if e["edge_id"] == chosen.removed_edge), None)
    pairs = {(e["source"], e["target"]) for e in body["edges"]}
    for i, link in enumerate(links):
        source = refs.get(link["source"], link["source"])
        target = refs.get(link["target"], link["target"])
        if source not in existing | new | {chosen.anchor}:
            raise GraphApiError(404, "chat_cards_link_unknown", {"reference": link["source"]})
        if target not in new | ({end} if end else set()):
            raise GraphApiError(422, "chat_cards_link_target_invalid", {"reference": link["target"]})
        if (source, target) in pairs:
            invalid(f"links[{i}]", "duplicate_link")
        pairs.add((source, target))
        condition = removed.get("condition") if removed and source == chosen.anchor else None
        body["edges"].append({"edge_id": None, "client_key": f"{placement.CLIENT_EDGE}{i}",
                              "source": source, "target": target, "condition": condition})
    incoming = {e["target"] for e in body["edges"]}
    outgoing = {e["source"] for e in body["edges"]}
    if not new <= incoming or (end and (not new <= outgoing or end not in incoming)):
        raise GraphApiError(422, "chat_cards_links_incomplete", {})
    if chosen.anchor not in {refs.get(e["source"], e["source"]) for e in links}:
        raise GraphApiError(422, "chat_cards_links_incomplete", {"reason": "anchor_not_connected"})
    # Draft saves deliberately allow invalid graphs. Chat topology placement
    # must instead reject them before any draft or card document is written.
    graph_record = {
        "nodes": [{**n, "node_key": n["node_key"] or n["client_key"]} for n in body["nodes"]],
        "edges": [{**e, "edge_id": e["edge_id"] or e["client_key"]} for e in body["edges"]],
    }
    graph = graph_service.as_graph(graph_record)
    issues = graph_validation_ports.validate(graph, policies.current())
    if issues:
        raise GraphApiError(422, "chat_cards_graph_invalid", {"issues": issues})
    from flowgate.graph_validation.run_scope import run_scope
    if not new <= run_scope(graph).targets:
        raise GraphApiError(422, "chat_cards_links_incomplete", {"reason": "new_card_outside_run"})
    return body
