import hashlib
import pytest
from modules.flow_gate.documents import tr2_service as tr2

def _spec(order=False):
    edits=[{"id":"a","file":"a.txt","kind":"edit"},{"id":"b","file":"b.txt","kind":"create_file"}]
    return {"edits":list(reversed(edits)) if order else edits}

def test_target_fingerprint_order_independent_and_spec_is_order_sensitive(tmp_path):
    (tmp_path/"a.txt").write_bytes(b"one\r\n")
    first=tr2.target_fingerprint(_spec(),tmp_path)
    assert tr2.target_fingerprint(_spec(True),tmp_path)==first
    assert tr2.spec_fingerprint(_spec()) != tr2.spec_fingerprint(_spec(True))
    (tmp_path/"unrelated").write_bytes(b"change")
    assert tr2.target_fingerprint(_spec(),tmp_path)==first
    (tmp_path/"a.txt").write_bytes(b"one\n")
    assert tr2.target_fingerprint(_spec(),tmp_path)!=first
    (tmp_path/"a.txt").write_bytes(b"\xef\xbb\xbfone\r\n")
    assert tr2.target_fingerprint(_spec(),tmp_path)!=first

def test_create_file_appearing_causes_drift(tmp_path):
    before=tr2.target_fingerprint(_spec(),tmp_path)
    (tmp_path/"b.txt").write_bytes(b"created")
    assert tr2.target_fingerprint(_spec(),tmp_path)!=before

def test_empty_target_set_hash():
    assert tr2.target_fingerprint({"edits":[]}, ".")==(
        "sha256:"+hashlib.sha256(b"").hexdigest())

def test_malformed_baseline_is_invariant_error(monkeypatch,tmp_path):
    from modules.flow_gate.db import tr_commit_ledger
    monkeypatch.setattr(tr2,"resolve_source_root",lambda *_:tmp_path)
    monkeypatch.setattr(tr_commit_ledger,"list_by_group",lambda *_:[])
    body={"edit_spec":{"edits":[]},"baseline_fingerprint":"bad"}
    with pytest.raises(tr2.Tr2ValidationError,match="tr2_history_invariant_error"):
        tr2.read_view({"project_id":"p","group_id":"g","doc_id":"d"},body)
