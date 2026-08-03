from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from scratch_support import remove_tree, session_scratch

from app import app
from modules.flow_gate.api.inbox_routes import (
    DeletedGroupFileRestore,
    _group_path_is_deleted,
    restore_deleted_group_file,
)
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.services import git_service
from modules.flow_gate.storage import paths as storage_paths


TEST_PROJECT_ID = "proj-t509"
# 0382 B0001: this was `Path(__file__).resolve().parent / "_scratch_t509_src"` — a
# scratch tree *inside* server/tests. `rmtree(ignore_errors=True)` hid every failed
# cleanup, so anything Windows refused to delete stayed in the repository waiting for
# the next finalize to commit it. The root now lives outside the repository.
TEST_ROOT = session_scratch("t509") / "src"


def _prepare_root() -> Path:
    remove_tree(TEST_ROOT)
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    return TEST_ROOT


def _client(monkeypatch) -> TestClient:
    root = _prepare_root()
    monkeypatch.setattr(db_projects, "get_by_id", lambda project_id: {"project_name": "t509-project"} if project_id == TEST_PROJECT_ID else None)
    monkeypatch.setattr(db_projects, "get_settings", lambda _project_id: {"branch": "main"})
    monkeypatch.setattr(storage_paths, "src_root", lambda _project_name, _branch: root)
    return TestClient(app)


def teardown_module(_module) -> None:
    remove_tree(TEST_ROOT)


def test_src_content_get_and_head_support_empty_file(monkeypatch):
    client = _client(monkeypatch)
    file_path = TEST_ROOT / "docs" / "empty.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("", encoding="utf-8")

    get_res = client.get(f"/flowgate/api/v1/projects/{TEST_PROJECT_ID}/files/src-content", params={"path": "docs/empty.md"})
    assert get_res.status_code == 200
    assert get_res.text == ""

    head_res = client.head(f"/flowgate/api/v1/projects/{TEST_PROJECT_ID}/files/src-content", params={"path": "docs/empty.md"})
    assert head_res.status_code == 200
    assert head_res.headers.get("content-length") == "0"


def test_src_content_patch_updates_existing_file(monkeypatch):
    client = _client(monkeypatch)
    file_path = TEST_ROOT / "docs" / "editable.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("before", encoding="utf-8")

    patch_res = client.patch(
        f"/flowgate/api/v1/projects/{TEST_PROJECT_ID}/files/src-content",
        params={"path": "docs/editable.md"},
        json={"content": "after"},
    )
    assert patch_res.status_code == 200
    payload = patch_res.json()
    assert payload["path"] == "docs/editable.md"
    assert payload["content_length"] == 5
    # 0176 T0010: the save response carries the advisory base-checkout dirty status
    # (benign default for a git-disabled project). Pre-existing stale expectation
    # fixed alongside flowgate.default.0226 — the route behavior is unchanged.
    assert payload["base_git"] == {"enabled": False, "dirty": False, "files": []}
    assert file_path.read_text(encoding="utf-8") == "after"

    get_res = client.get(f"/flowgate/api/v1/projects/{TEST_PROJECT_ID}/files/src-content", params={"path": "docs/editable.md"})
    assert get_res.status_code == 200
    assert get_res.text == "after"


def test_src_content_patch_returns_404_for_missing_file(monkeypatch):
    client = _client(monkeypatch)

    patch_res = client.patch(
        f"/flowgate/api/v1/projects/{TEST_PROJECT_ID}/files/src-content",
        params={"path": "docs/missing.md"},
        json={"content": "after"},
    )
    assert patch_res.status_code == 404
    assert patch_res.json()["detail"] == "Not found"

def test_group_deleted_path_uses_change_status_and_normalizes_separators(monkeypatch):
    monkeypatch.setattr(
        git_service,
        "read_group_changes",
        lambda _project_id, _group_id: {
            "data": {
                "changes": [
                    {"path": "docs/gone.md", "status": "D"},
                    {"path": "docs/edited.md", "status": "M"},
                ]
            }
        },
    )

    assert _group_path_is_deleted("p1", "g1", "docs\\gone.md") is True
    assert _group_path_is_deleted("p1", "g1", "docs/edited.md") is False
    assert _group_path_is_deleted("p1", "g1", "docs/missing.md") is False


def test_restore_deleted_group_file_checks_out_head_and_emits_refresh(monkeypatch):
    root = _prepare_root() / "worktree"
    target = root / "docs" / "gone.md"
    target.parent.mkdir(parents=True)
    calls = []

    monkeypatch.setattr(
        "modules.flow_gate.api.inbox_routes._editable_source_path",
        lambda _project_id, _path, group_id: (target, root, group_id),
    )
    monkeypatch.setattr(
        "modules.flow_gate.api.inbox_routes._group_path_is_deleted",
        lambda _project_id, _group_id, _path: True,
    )
    monkeypatch.setattr(
        "modules.flow_gate.api.inbox_routes._emit_source_edit_refresh",
        lambda project_id, group_id, path: calls.append(("refresh", project_id, group_id, path)),
    )

    class Result:
        returncode = 0

    def fake_run(args, cwd):
        calls.append(("git", args, cwd))
        target.write_text("restored", encoding="utf-8")
        return Result()

    monkeypatch.setattr(git_service, "_run_git", fake_run)

    result = restore_deleted_group_file(
        "p1",
        "g1",
        DeletedGroupFileRestore(path="docs\\gone.md"),
        user={"user_id": "u1"},
    )

    assert result["data"] == {
        "group_id": "g1",
        "path": "docs/gone.md",
        "restored": True,
    }
    assert calls == [
        ("git", ["checkout", "HEAD", "--", "docs/gone.md"], root),
        ("refresh", "p1", "g1", "docs/gone.md"),
    ]
