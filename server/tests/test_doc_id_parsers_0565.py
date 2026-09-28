import pytest
from modules.flow_gate.documents.type_code import (
    doc_code_seq_text, doc_code_type, doc_id_type_code, parse_doc_code, parse_step_key,
)
from modules.flow_gate.numbering.id_formatter import extract_numeric_suffix

def test_canonical_document_codes():
    assert parse_doc_code("0005-T2") == ("T2", 5)
    assert parse_doc_code("0016-TR2") == ("TR2", 16)
    assert doc_code_type("0016-TR2") == "TR2"
    assert doc_code_seq_text("0005-T2") == "0005"
    assert extract_numeric_suffix("0005-T2") == "0005"
    assert doc_id_type_code("flowgate.default.0565.0016-TR2") == "TR2"

@pytest.mark.parametrize("key,expected", [("T2#1", ("T2", 1)), ("TR2#12", ("TR2", 12)), ("T#1", ("T", 1)), ("TR2001", None)])
def test_step_key(key, expected):
    assert parse_step_key(key) == expected
