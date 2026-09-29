import pytest
from modules.flow_gate.documents import tr2_service as tr2

def test_pair_mismatch_is_a_workflow_conflict(monkeypatch):
    from modules.flow_gate.db import workflow_sequences, documents
    monkeypatch.setattr(workflow_sequences,"get_paired_instruction_item",lambda *_:None)
    monkeypatch.setattr(workflow_sequences,"get_item_by_result_doc_id",lambda *_:None)
    monkeypatch.setattr(documents,"get_by_id",lambda doc_id:{
        "doc_id":doc_id,"type_code":"TR2" if doc_id.endswith("TR2") else "T2",
        "project_id":"p","group_id":"g"})
    with pytest.raises(tr2.Tr2ValidationError,match="tr2_workflow_conflict"):
        tr2.verify_pair("p.m.g.0006-TR2",{"source_t2_doc_id":"p.m.g.0005-T2"})

def test_revision_cas_and_server_baseline(tmp_path,monkeypatch):
    from test_t2_tr2_domain_0565 import test_single_writer_revision_cas_never_touches_file_on_stale_request
    test_single_writer_revision_cas_never_touches_file_on_stale_request(tmp_path,monkeypatch)
