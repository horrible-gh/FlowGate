import copy
import pytest
from modules.flow_gate.documents import tr2_service as tr2

def body():
    return {"tr2_version":1,"source_t2_doc_id":"flowgate.default.0565.0005-T2",
            "edit_spec":{"termination":"ready_to_apply",
                "edits":[{"id":"e1","file":"src/a.txt","anchor_old":"a",
                          "replacement_new":"b","rationale":"change","confidence":"high"}],
                "deferred":[],"gate":{"commands":[],"apply":False}}}

def test_bad_json_and_empty_ready_edits_rejected():
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_spec_invalid"):
        tr2.parse("{")
    for deferred in ([], [{"id":"d1","reason":"needs_runtime","rationale":"later"}]):
        value=body()
        value["edit_spec"]["edits"]=[]
        value["edit_spec"]["deferred"]=deferred
        with pytest.raises(tr2.Tr2ValidationError, match="tr2_spec_invalid"):
            tr2.validate(value, doc={})

@pytest.mark.parametrize("change", [
    lambda b: b["edit_spec"]["gate"].update(apply=True),
    lambda b: b["edit_spec"]["edits"][0].update(replacement_new="a"),
    lambda b: b["edit_spec"]["edits"][0].update(unexpected=1),
    lambda b: b["edit_spec"]["gate"].update(commands=["  "]),
    lambda b: b["edit_spec"]["gate"].update(commands=["x"]*51),
    lambda b: b["edit_spec"]["gate"].update(commands=["x"*501]),
])
def test_invalid_shapes(change):
    value=body()
    change(value)
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_spec_invalid"):
        tr2.validate(value, doc={})

def test_untrusted_root_fields_are_dropped():
    value=body()
    value.update(codebase_root="/tmp",source_honey="/tmp",baseline_fingerprint="wrong")
    result=tr2.canonicalize(tr2.validate(value,doc={}))
    assert set(result)=={"tr2_version","source_t2_doc_id","edit_spec"}
    assert result["edit_spec"]["edits"][0]["kind"]=="edit"

def test_create_file_rejects_anchor_and_blank_content():
    value=body()
    value["edit_spec"]["edits"][0]={"id":"e1","kind":"create_file","file":"src/new",
        "content":"  ","rationale":"new","confidence":"high"}
    with pytest.raises(tr2.Tr2ValidationError):
        tr2.validate(value,doc={})
    value["edit_spec"]["edits"][0]["content"]="new"
    value["edit_spec"]["edits"][0]["anchor_old"]="old"
    with pytest.raises(tr2.Tr2ValidationError):
        tr2.validate(value,doc={})


def test_mixed_create_and_edit_target_is_rejected():
    value = body()
    value["edit_spec"]["edits"].append({
        "id": "e2", "kind": "create_file", "file": "src/a.txt",
        "content": "new", "rationale": "create", "confidence": "high"})
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_spec_invalid"):
        tr2.validate(value, doc={})


def test_non_json_provenance_is_rejected_before_cas():
    value = body()
    value["edit_spec"]["edits"][0]["evidence"] = object()
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_spec_invalid"):
        tr2.validate(value, doc={})
