"""0065 #33: real Graph save prevents allocation, preserves slots and existing documents."""
import pytest
from test_work_plan_deploy_0074 import (  # noqa: F401
    db_path, env, decided, _line_doc, _sql, FA,
)

@pytest.mark.parametrize("doc_type", ["D", "P", "L", "DB", "N", "T"])
@pytest.mark.parametrize("mode", ["omit", "use"])
def test_new_omitted_card_has_no_instruction_document(env, doc_type, mode):
    from flowgate.card_documents.lines import CardView
    from flowgate.card_documents import legacy_adapter
    # 0065 #33: WP creation/deploy is retired. Exercise the actual Graph save
    # factory inside its real Flow guard, without disabling the retirement rule.
    from test_fg_card_settings import node
    from modules.flow_gate.db import groups
    from modules.flow_gate.graph_workflow.domain import draft_request as dr
    from modules.flow_gate.graph_workflow.projection.flow_snapshot import registered_doc_types
    from modules.flow_gate.graph_workflow.services import graph_service
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    req = dr.parse_draft({"draft_version": 0, "nodes": [node("tmp_card", doc_type)], "edges": []})
    result = with_flow_guard(FA, lambda: graph_service.write_draft(
        groups.get_by_id(FA), "u_kim", req, None, registered_doc_types("flowgate"),
        initial_instruction_modes={"tmp_card": mode}))
    key = result["key_map"]["tmp_card"]
    instruction_id = _line_doc(env, key)
    assert bool(instruction_id) == (mode == "use")
    assert _line_doc(env, key, "report")
    docs = {d["doc_id"]: d for d in legacy_adapter.flow_documents(FA)}
    lines = CardView(FA, docs).lines_for({"node_key": key, "node_type": "document", "doc_type": doc_type})
    instruction = next(line for line in lines if line["slot"] == "instruction")
    assert instruction["doc_id"] == instruction_id
    assert instruction["instruction_omitted"] == (mode == "omit")
    if mode == "omit":
        # A use round-trip restores a documentless slot; execution may fill it later.
        from flowgate.card_settings import repository as settings
        with_flow_guard(FA, lambda: settings.put(FA, key, "instruction", "use", "u_kim"))
        shown = CardView(FA, docs).lines_for({"node_key": key, "node_type": "document", "doc_type": doc_type})
        assert next(line for line in shown if line["slot"] == "instruction")["instruction_omitted"] is False
