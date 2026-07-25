"""0319 B0001 - base file explorer follows the enabled Git base branch."""

import pytest

from modules.flow_gate import process_service
from modules.flow_gate.api.v1 import tree_routes
from modules.flow_gate.db import projects
from modules.flow_gate.services import git_service


PROJECT_ID = "project-0319"
PROJECT_NAME = "repo-0319"


@pytest.fixture
def source_slots(tmp_path, monkeypatch):
    """Route FlowGate source slots into an isolated test directory."""

    monkeypatch.setattr(
        git_service,
        "src_root",
        lambda project_name, branch: tmp_path / "src" / project_name / branch,
    )
    monkeypatch.setattr(
        projects,
        "get_by_id",
        lambda project_id: {"project_name": PROJECT_NAME},
    )
    monkeypatch.setattr(
        projects,
        "get_settings",
        lambda project_id: {"branch": "main"},
    )
    return {
        "main": tmp_path / "src" / PROJECT_NAME / "main",
        "develop": tmp_path / "src" / PROJECT_NAME / "develop",
    }


def _enable_git(monkeypatch, *, branch="develop"):
    monkeypatch.setattr(
        git_service.db_git,
        "get_config",
        lambda project_id: {"enabled": True, "base_branch": branch},
    )


def test_base_src_root_uses_enabled_git_branch_and_preserves_fallback(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        git_service,
        "src_root",
        lambda project_name, branch: tmp_path / project_name / branch,
    )

    _enable_git(monkeypatch, branch="develop")
    assert git_service.base_branch_for(PROJECT_ID) == "develop"
    assert git_service.base_src_root(PROJECT_ID, PROJECT_NAME, "main") == (
        tmp_path / PROJECT_NAME / "develop"
    )

    monkeypatch.setattr(
        git_service.db_git,
        "get_config",
        lambda project_id: {"enabled": False, "base_branch": "develop"},
    )
    assert git_service.base_branch_for(PROJECT_ID) is None
    assert git_service.base_src_root(PROJECT_ID, PROJECT_NAME, "release") == (
        tmp_path / PROJECT_NAME / "release"
    )

    def lookup_failure(project_id):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(git_service.db_git, "get_config", lookup_failure)
    assert git_service.base_branch_for(PROJECT_ID) is None
    assert git_service.base_src_root(PROJECT_ID, PROJECT_NAME, "release") == (
        tmp_path / PROJECT_NAME / "release"
    )


def test_file_tree_shows_enabled_git_base_branch(source_slots, monkeypatch):
    _enable_git(monkeypatch)
    source_slots["main"].mkdir(parents=True)
    source_slots["develop"].mkdir(parents=True)
    (source_slots["main"] / "wrong-slot.txt").write_text("main", encoding="utf-8")
    (source_slots["develop"] / "existing-source.txt").write_text(
        "develop", encoding="utf-8"
    )

    nodes = process_service.get_file_tree(PROJECT_ID)["nodes"]
    names = {node["name"] for node in nodes}

    assert "existing-source.txt" in names
    assert "wrong-slot.txt" not in names


def test_base_create_and_content_resolvers_share_git_base_branch(
    source_slots, monkeypatch
):
    _enable_git(monkeypatch)
    source_slots["main"].mkdir(parents=True)
    source_slots["develop"].mkdir(parents=True)
    (source_slots["main"] / "same-name.txt").write_text("main", encoding="utf-8")
    (source_slots["develop"] / "same-name.txt").write_text(
        "develop", encoding="utf-8"
    )

    folder_result = process_service.create_storage_folder(
        PROJECT_ID, "", "new-folder"
    )
    file_result = process_service.create_storage_file(
        PROJECT_ID, "new-folder", "new-file.txt"
    )

    assert folder_result == {"status": "success"}
    assert file_result == {"status": "success"}
    assert (source_slots["develop"] / "new-folder" / "new-file.txt").is_file()
    assert not (source_slots["main"] / "new-folder").exists()
    assert tree_routes._resolve_src_path(PROJECT_ID, "same-name.txt") == (
        source_slots["develop"] / "same-name.txt"
    ).resolve()
    assert tree_routes._resolve_delete_path(PROJECT_ID, "same-name.txt") == (
        source_slots["develop"] / "same-name.txt"
    ).resolve()

