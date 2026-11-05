from pathlib import Path
from modules.flow_gate.documents import tr2_service as tr2

def test_all_three_entrypoints_reference_the_same_writer():
    root=Path(__file__).resolve().parents[4]/"modules/flow_gate"
    inbox=(root/"api/inbox_routes.py").read_text(encoding="utf-8")
    direct=(root/"documents/routers/tr2.py").read_text(encoding="utf-8")
    assert "tr2_service.create(" in inbox
    import inspect
    from flowgate.card_document_edit import dedicated_windows, tr2_rules
    assert "card_edit(" in inbox
    assert "dedicated_write(" in inspect.getsource(tr2._save)
    assert "dedicated_save(" in inspect.getsource(dedicated_windows.tr2_save)
    assert "tr2.validate(" in inspect.getsource(tr2_rules.build)
    assert "tr2.save(" in direct
    assert callable(tr2.create) and callable(tr2.read) and callable(tr2.save)
