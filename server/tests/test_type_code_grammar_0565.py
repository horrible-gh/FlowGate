import re
import pytest
from modules.flow_gate.documents.type_code import TYPE_CODE_PATTERN, is_valid_type_code, parse_doc_code

@pytest.mark.parametrize("code", ["R", "TR", "TSR", "WP", "T2", "TR2", "A1", "AB12"])
def test_valid_type_codes(code):
    assert is_valid_type_code(code)
    assert re.fullmatch(TYPE_CODE_PATTERN, code)

@pytest.mark.parametrize("code", ["", "2T", "tr2", "T-2", "T_2", "ABCDE", "T2001"])
def test_invalid_type_codes(code):
    assert not is_valid_type_code(code)

def test_compact_t2_number_is_not_a_document_code():
    with pytest.raises(ValueError):
        parse_doc_code("T2001")

def test_client_pattern_matches_server_literal():
    from pathlib import Path
    client = (Path(__file__).resolve().parents[2] / "client/shared/utils/typeCode.ts").read_text(encoding="utf-8")
    assert f"TYPE_CODE_RE = /^{TYPE_CODE_PATTERN}$/" in client
