from modules.flow_gate.services.tool_registry import MUTATING_STEP_TYPES, kind_for_step

def test_t2_and_tr2_do_not_gain_write_access():
    assert kind_for_step("new", "T2") == ("read", None)
    assert kind_for_step("new", "TR2") == ("read", None)
    assert kind_for_step("edit", "TR2") == ("read", None)
    assert "TR2" not in MUTATING_STEP_TYPES

def test_existing_report_and_test_permissions_regressions():
    assert kind_for_step("new", "TR") == ("read_write", None)
    assert "TR" in MUTATING_STEP_TYPES
