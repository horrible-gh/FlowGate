import pytest
from modules.flow_gate.documents import tr2_service as tr2

def test_read_view_four_blocks_and_unique_files(monkeypatch,tmp_path):
    from modules.flow_gate.db import tr_commit_ledger
    from modules.flow_gate.documents import document_service
    (tmp_path/"a").write_bytes(b"data")
    monkeypatch.setattr(tr2,"resolve_source_root",lambda *_:tmp_path)
    monkeypatch.setattr(tr_commit_ledger,"list_by_group",lambda *_:[])
    monkeypatch.setattr(document_service,"is_document_editable",lambda *a,**kw:True)
    monkeypatch.setattr(document_service,"is_final_approved",lambda *_:False)
    from modules.flow_gate.db import tr2_approval_attempts
    from modules.flow_gate.documents import tr2_approval_service
    monkeypatch.setattr(tr2_approval_attempts,"list_by_doc",lambda *_:[])
    monkeypatch.setattr(tr2_approval_service,"retry_state",lambda _doc:{"allowed":False,"reason":"no_attempt"})
    monkeypatch.setattr(tr2,"mutation_block",lambda _doc:None)
    monkeypatch.setattr(tr2,"_history_state",lambda _doc:"aligned")
    spec={"edits":[{"id":"a","file":"a"},{"id":"b","file":"a"}]}
    body={"edit_spec":spec,"baseline_fingerprint":tr2.target_fingerprint(spec,tmp_path)}
    view=tr2.read_view({"project_id":"p","group_id":"g","doc_id":"d"},body)
    assert set(view)=={"document","mutation","body","derived","approval","history"}
    assert view["mutation"]=={"allowed":True,"reason":None}
    assert len(view["derived"]["files"])==1
    assert view["derived"]["files"][0]["edit_ids"]==["a","b"]
    assert view["approval"]=={"latest_attempt":None,"attempts":[],
                              "retry":{"allowed":False,"reason":"no_attempt"}}
    assert view["derived"]["live_precheck"]["anchors"] is None

def test_attempts_route_shape():
    from modules.flow_gate.documents.routers.tr2 import get_tr2_attempts
    assert callable(get_tr2_attempts)


def test_file_projection_reads_only_declared_target(monkeypatch, tmp_path):
    from modules.flow_gate.db import documents
    # Bytes, not write_text: on Windows text mode stores CRLF, and the projection
    # must return the file exactly as it is on disk.
    (tmp_path / "a").write_bytes(b"current\n")
    doc = {"doc_id": "d", "type_code": "TR2", "project_id": "p", "group_id": "g"}
    spec = {"edits": [{"id": "e1", "kind": "edit", "file": "a",
                       "anchor_old": "current", "replacement_new": "changed"}]}
    monkeypatch.setattr(documents, "get_by_id", lambda *_: doc)
    monkeypatch.setattr(tr2, "canonical_path_for_doc", lambda *_: tmp_path / "document.json")
    monkeypatch.setattr(tr2, "load_body", lambda *_: {"edit_spec": spec})
    monkeypatch.setattr(tr2, "resolve_source_root", lambda *_: tmp_path)
    projection = tr2.read_file_projection("d", "a")
    assert projection["before_text"] == "current\n"
    assert projection["edits"] == spec["edits"]
    assert projection["truncated"] is False
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_path_unsafe"):
        tr2.read_file_projection("d", "other")
