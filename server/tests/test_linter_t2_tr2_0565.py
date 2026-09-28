from modules.flow_gate import linter

def _header(type_code, target_id):
    return {"project":"flowgate", "module":"default", "group_id":"flowgate.default.0565",
            "type":type_code, "title":"test", "target_id":target_id, "next":"T2"}

def test_type_sets_and_unmodified_nr_tr_special_case():
    for code in ("T2", "TR2"):
        assert code in linter.VALID_TYPES
        assert code in linter.INBOX_TARGET_TYPES
    assert "T2" in linter.NEXT_VALID_VALUES
    assert linter.NR_TR_TYPES == {"NR", "TR"}

def test_canonical_target_accepted_and_compact_rejected():
    assert not any("target_id format" in e for e in linter.lint_header(
        _header("T2", "flowgate.default.0565.0005-T2"), set()))
    assert any("target_id format" in e for e in linter.lint_header(
        _header("T2", "T2001"), set()))

def test_next_error_lists_current_allowed_set():
    header = _header("AR", "flowgate.default.0565.0005-T2")
    header["next"] = "INVALID"
    errors = linter.lint_header(header, set())
    assert any(", ".join(sorted(linter.NEXT_VALID_VALUES)) in e for e in errors)
