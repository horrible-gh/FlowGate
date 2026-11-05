import pytest
from modules.flow_gate.documents import tr2_service as tr2
from tests.flowgate.test_card_document_edit_0127 import env  # noqa: F401

def test_pair_mismatch_is_a_workflow_conflict(monkeypatch):
    from modules.flow_gate.db import workflow_sequences, documents
    monkeypatch.setattr(workflow_sequences,"get_paired_instruction_item",lambda *_:None)
    monkeypatch.setattr(workflow_sequences,"get_item_by_result_doc_id",lambda *_:None)
    monkeypatch.setattr(documents,"get_by_id",lambda doc_id:{
        "doc_id":doc_id,"type_code":"TR2" if doc_id.endswith("TR2") else "T2",
        "project_id":"p","group_id":"g"})
    with pytest.raises(tr2.Tr2ValidationError,match="tr2_workflow_conflict"):
        tr2.verify_pair("p.m.g.0006-TR2",{"source_t2_doc_id":"p.m.g.0005-T2"})

def test_revision_cas_and_server_baseline(env,monkeypatch):
    from test_t2_tr2_domain_0565 import test_single_writer_revision_cas_never_touches_file_on_stale_request
    test_single_writer_revision_cas_never_touches_file_on_stale_request(env,monkeypatch)


@pytest.fixture(autouse=True)
def dedicated_document_write_ports_0127(monkeypatch):
    # Direct service/route fixtures reproduce the composition root's required ports.
    from modules.flow_gate.graph_workflow.services import card_document_ports
    from flowgate.card_document_edit import integration as document_edit
    monkeypatch.setattr(card_document_ports, "_handlers", dict(card_document_ports._handlers))
    document_edit.register()
