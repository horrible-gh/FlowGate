import pytest
from modules.flow_gate.documents import tr2_service as tr2

def test_integrated_group_never_falls_back_to_base_checkout(monkeypatch):
    from modules.flow_gate.services import git_service
    monkeypatch.setattr(git_service,"effective_src_root_ex",lambda *_:(None,"worktree_missing"))
    with pytest.raises(tr2.Tr2ValidationError,match="tr2_git_unavailable"):
        tr2.resolve_source_root("p","g")
