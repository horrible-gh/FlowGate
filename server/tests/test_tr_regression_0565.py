from pathlib import Path
from modules.flow_gate.services.tool_registry import MUTATING_STEP_TYPES, kind_for_step

def test_existing_tr_remains_mutating_but_tr2_does_not():
    assert kind_for_step("new","TR")==("read_write",None)
    assert kind_for_step("new","TR2")==("read",None)
    assert "TR" in MUTATING_STEP_TYPES
    assert "TR2" not in MUTATING_STEP_TYPES

def test_tr_commit_hook_remains_tr_only():
    root=Path(__file__).resolve().parents[1]/"modules/flow_gate"
    text=(root/"services/tr_commit_service.py").read_text(encoding="utf-8")
    assert "TR_TYPE_CODE" in text
    assert "TR2_TYPE_CODE" not in text
