"""flowgate.default.0641 T2#4 — group tree managed-set API contract."""
from modules.flow_gate.api.v1 import git_routes
from modules.flow_gate.services import tr2_file_policy


def test_group_tree_attaches_one_group_scoped_managed_set(monkeypatch):
    calls = []

    monkeypatch.setattr(
        git_routes.git_service,
        "read_group_tree",
        lambda project_id, group_id: {
            "ok": True,
            "data": {
                "group_id": group_id,
                "branch": "fg-0641",
                "commit": "a" * 40,
                "nodes": [
                    {"id": "1", "path": "src/a.py"},
                    {"id": "2", "path": "src/b.py"},
                ],
            },
        },
    )
    monkeypatch.setattr(
        tr2_file_policy,
        "managed_paths",
        lambda group_id: calls.append(group_id) or {"src/b.py", "src/a.py"},
    )

    result = git_routes.get_group_branch_tree(
        "flowgate", "flowgate.default.0641", user={}
    )

    assert calls == ["flowgate.default.0641"]
    assert result["data"]["tr2_managed_paths"] == ["src/a.py", "src/b.py"]


def test_group_tree_managed_set_is_group_scoped(monkeypatch):
    monkeypatch.setattr(
        git_routes.git_service,
        "read_group_tree",
        lambda project_id, group_id: {
            "ok": True,
            "data": {
                "group_id": group_id,
                "branch": "b",
                "commit": "b" * 40,
                "nodes": [],
            },
        },
    )
    owned = {
        "flowgate.default.0641": {"same/path.py"},
        "flowgate.default.other": set(),
    }
    monkeypatch.setattr(
        tr2_file_policy,
        "managed_paths",
        lambda group_id: owned[group_id],
    )

    first = git_routes.get_group_branch_tree(
        "flowgate", "flowgate.default.0641", user={}
    )
    other = git_routes.get_group_branch_tree(
        "flowgate", "flowgate.default.other", user={}
    )

    assert first["data"]["tr2_managed_paths"] == ["same/path.py"]
    assert other["data"]["tr2_managed_paths"] == []
