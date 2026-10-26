from modules.flow_gate.documents.constants import WORK_PLAN_PAIR_MAP, WORK_PLAN_SET_TYPES
from modules.flow_gate.services import workflow_decision_service as decisions
from modules.flow_gate.documents.type_code import parse_step_key

def test_t2_pair_is_identical_across_registries():
    assert decisions.AUTO_REPORT_MAP["T2"] == WORK_PLAN_PAIR_MAP["T2"] == "TR2"
    assert set(decisions.AUTO_REPORT_MAP) == set(WORK_PLAN_PAIR_MAP)
    assert "T2" in decisions.INSTRUCTION_AUTO_TYPES
    assert "T2" in WORK_PLAN_SET_TYPES
    assert parse_step_key("T2#1") == ("T2", 1)
    assert parse_step_key("TR2#1") == ("TR2", 1)
