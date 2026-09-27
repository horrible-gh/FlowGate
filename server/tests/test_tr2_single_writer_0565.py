from pathlib import Path
from modules.flow_gate.documents import tr2_service as tr2

def test_all_three_entrypoints_reference_the_same_writer():
    root=Path(__file__).resolve().parents[1]/"modules/flow_gate"
    inbox=(root/"api/inbox_routes.py").read_text(encoding="utf-8")
    direct=(root/"documents/routers/tr2.py").read_text(encoding="utf-8")
    assert inbox.count("tr2_service.save(")>=2
    assert "tr2.save(" in direct
    assert callable(tr2.save)
