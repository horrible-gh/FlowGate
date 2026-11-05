"""P#2 server-owned Agent coordinate binding. Never resolves the current slot value."""
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.store import run_store
from flowgate.card_documents import repository, lines, slots
from flowgate.card_documents import legacy_adapter as legacy
from . import target
from .errors import invalid


def missing(reason="ambiguous_or_inconsistent_binding"):
    return invalid("card_binding_missing", "card_binding", "invalid_format", validation={"reason": reason})


def classification(doc):
    result = target.classify_card_slot(doc)
    if result == target.UNAVAILABLE:
        raise missing("card_edit_unavailable")
    return result


def bind(token, doc):
    try:
        flow_id, doc_id = token.get("group_id") or doc.get("group_id"), token["doc_ref"]
        if doc.get("group_id") != flow_id or doc.get("project_id") != token.get("project"):
            raise missing()
        if not repository.available() or not run_store.available():
            raise missing("card_edit_unavailable")
        mapping = run_store.node_run_by_token(token["token_id"])
        if mapping:
            run = run_store.run_by_id(mapping["run_id"])
            if not run or run.get("group_id") != flow_id:
                raise missing()
        rows = target.related_rows(doc)
        if mapping:
            nodes = run_store.node_runs(mapping["run_id"])
            evidence = next((n for n in nodes if n["node_key"] == mapping["node_key"]), None)
            if evidence and doc_id in {evidence.get("instruction_doc_id"), evidence.get("result_doc_id")}:
                # The node-run identifies the token document. Type and owned slot definition identify its row.
                rows = list(rows) + [r for r in repository.rows(flow_id, [mapping["node_key"]])
                                     if r["doc_type"] == doc.get("type_code")]
        candidates = set()
        for row in rows:
            if mapping and row["node_key"] != mapping["node_key"]:
                continue
            node = target.settings.card_node(flow_id, row["node_key"])
            if node is None:
                raise missing()
            spec = next((s for s in lines._slots(flow_id, node) if s.slot == row["slot"]), None)
            if spec is None or not slots.lines_of(node.get("node_type"), node.get("doc_type"), [row]):
                raise missing()
            owner = spec.shares or spec.slot
            owner_spec = next((s for s in lines._slots(flow_id, node) if s.slot == owner), None)
            if owner_spec is None or owner_spec.shares:
                raise missing()
            owners = [r for r in repository.rows(flow_id, [node["node_key"]]) if r["slot"] == owner
                      and slots.lines_of(node.get("node_type"), node.get("doc_type"), [r])]
            if len(owners) != 1:
                raise missing()
            candidates.add((flow_id, row["node_key"], owner, doc_id))
        if not repository.available() or len(candidates) != 1:
            raise missing()
        f, n, s, d = candidates.pop()
        return {"flow_id": f, "node_key": n, "slot": s, "doc_id": d}
    except Exception as exc:
        from .errors import GraphApiError
        if isinstance(exc, GraphApiError):
            raise
        raise missing("card_edit_unavailable") from exc
